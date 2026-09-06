from __future__ import annotations

import asyncio
import json
import logging
import html
import re
from time import monotonic
from calendar import monthrange
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from math import asin, cos, radians, sin, sqrt
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import AlapnyomkovetesClient, AlapnyomkovetesError
from .const import DOMAIN, KELIO_ADDON_SLUG
from .odometer import update_odometer_ledger

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class Point:
    lat: float
    lon: float
    when: datetime
    speed: float | None
    distance: float | None
    ignition: Any
    raw_id: Any


@dataclass(slots=True)
class DailySummary:
    date: str
    distance_km: float | None
    movement_detected: bool
    max_speed_kmh: float | None
    total_records: int
    valid_points: int
    unique_points: int
    first_point: Point | None
    last_point: Point | None
    started_home: bool | None
    ended_home: bool | None
    touched_home: bool | None
    started_work: bool | None
    ended_work: bool | None
    touched_work: bool | None
    home_radius_m: float
    work_radius_m: float
    endpoint_window_km: float
    start_home_min_distance_m: float | None
    end_home_min_distance_m: float | None
    start_work_min_distance_m: float | None
    end_work_min_distance_m: float | None
    start_home_min_time: str | None
    end_home_min_time: str | None
    start_work_min_time: str | None
    end_work_min_time: str | None
    overnight_home_radius_m: float | None
    overnight_before_home: bool | None
    overnight_after_home: bool | None
    overnight_before_complete: bool
    overnight_after_complete: bool
    overnight_before_first_home_time: str | None
    overnight_before_last_home_time: str | None
    overnight_after_first_home_time: str | None
    overnight_after_last_home_time: str | None
    overnight_before_min_distance_m: float | None
    overnight_before_min_time: str | None
    overnight_after_min_distance_m: float | None
    overnight_after_min_time: str | None
    overnight_before_points_inside: int
    overnight_after_points_inside: int
    overnight_before_evidence: str
    overnight_after_evidence: str
    overnight_before_anchor_time: str | None
    overnight_after_anchor_time: str | None
    overnight_before_anchor_distance_m: float | None
    overnight_after_anchor_distance_m: float | None
    route_segments: list[dict[str, Any]] | None = None
    route_stats_available: bool = False


@dataclass(slots=True)
class MonthlySummary:
    month: str
    source_available: bool
    presence_dates: list[str]
    manual_dates: list[str]
    presence_days: int
    eligible_days: float
    ineligible_presence_days: float
    eligible_legs: int
    ineligible_legs: int
    unknown_legs: int
    company_distance_km: float
    private_commute_km: float
    reimbursement_huf: float
    odometer_start_km: float
    odometer_end_km: float
    records: list[dict[str, Any]]
    errors: dict[str, str]
    updated_at: str
    stored_file: str | None


def _parse_point(value: Any) -> tuple[float, float] | None:
    if not isinstance(value, str) or not value.startswith("POINT(") or not value.endswith(")"):
        return None
    try:
        lon_s, lat_s = value[6:-1].split()
        return float(lat_s), float(lon_s)
    except (TypeError, ValueError):
        return None


def _parse_when(row: dict[str, Any]) -> datetime | None:
    text = row.get("measure_textdate")
    if not isinstance(text, str) or text.startswith("1970-01-01"):
        return None
    try:
        naive = datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
        return naive.replace(tzinfo=dt_util.DEFAULT_TIME_ZONE)
    except (ValueError, TypeError):
        return None


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    earth = 6371000.0
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return 2 * earth * asin(sqrt(a))


def _zone_tuple(
    hass: HomeAssistant,
    entity_id: str,
    minimum_radius_m: float | None = None,
) -> tuple[float, float, float] | None:
    state = hass.states.get(entity_id)
    if state is None:
        return None
    try:
        lat = float(state.attributes["latitude"])
        lon = float(state.attributes["longitude"])
        radius = float(state.attributes.get("radius", 100))
        if minimum_radius_m is not None:
            radius = max(radius, float(minimum_radius_m))
        return lat, lon, radius
    except (KeyError, TypeError, ValueError):
        return None


def _point_distance_to_zone(
    point: Point, zone: tuple[float, float, float] | None
) -> float | None:
    if zone is None:
        return None
    lat, lon, _radius = zone
    return _haversine_m(point.lat, point.lon, lat, lon)


def _inside(point: Point, zone: tuple[float, float, float] | None) -> bool | None:
    if zone is None:
        return None
    distance = _point_distance_to_zone(point, zone)
    return distance is not None and distance <= zone[2]


def _any_inside(points: list[Point], zone: tuple[float, float, float] | None) -> bool | None:
    if zone is None:
        return None
    return any(bool(_inside(point, zone)) for point in points)


def _nearest_point_to_zone(
    points: list[Point],
    zone: tuple[float, float, float] | None,
) -> tuple[Point | None, float | None]:
    if zone is None or not points:
        return None, None
    nearest_point: Point | None = None
    nearest_distance: float | None = None
    for point in points:
        value = _point_distance_to_zone(point, zone)
        if value is None:
            continue
        if nearest_distance is None or value < nearest_distance:
            nearest_point = point
            nearest_distance = value
    return nearest_point, nearest_distance


def _min_distance_to_zone(
    points: list[Point], zone: tuple[float, float, float] | None
) -> float | None:
    _point, distance = _nearest_point_to_zone(points, zone)
    return distance


def _path_window_from_start(points: list[Point], window_km: float) -> list[Point]:
    """Return points within the first N travelled km, using GPS geometry."""
    if not points:
        return []
    if window_km <= 0:
        return [points[0]]
    limit_m = window_km * 1000.0
    result = [points[0]]
    travelled = 0.0
    previous = points[0]
    for point in points[1:]:
        travelled += _haversine_m(previous.lat, previous.lon, point.lat, point.lon)
        result.append(point)
        if travelled >= limit_m:
            break
        previous = point
    return result


def _path_window_from_end(points: list[Point], window_km: float) -> list[Point]:
    """Return points within the last N travelled km, using GPS geometry."""
    if not points:
        return []
    reversed_window = _path_window_from_start(list(reversed(points)), window_km)
    return list(reversed(reversed_window))


def _number(container: Any, key: str, default=None):
    try:
        value = container.get(key) if isinstance(container, dict) else default
        return float(value) if value is not None else default
    except (TypeError, ValueError):
        return default



def _extract_position_samples(
    data: dict[str, Any],
    device_id: int,
    *,
    include_synthetic: bool = True,
) -> list[Point]:
    """Parse tracker position samples.

    Alapnyomkövetés returns synthetic playback/state rows with id=-1. Those
    rows are exactly what the web playback uses to show where a stationary car
    was at the beginning of a requested interval. They are useful for
    overnight location state, but must not be used for route/distance math.
    """
    key = str(device_id)
    rows = (data.get("locations") or {}).get(key, [])
    if not isinstance(rows, list):
        return []

    points: list[Point] = []
    for row in rows:
        if not isinstance(row, dict):
            continue

        raw_id = row.get("id")
        if not include_synthetic and raw_id == -1:
            continue

        coord = _parse_point(row.get("position"))
        when = _parse_when(row)
        if coord is None or when is None:
            continue

        lat, lon = coord
        try:
            speed = float(row["speed"]) if row.get("speed") is not None else None
        except (TypeError, ValueError):
            speed = None
        try:
            distance = float(row["distance"]) if row.get("distance") is not None else None
        except (TypeError, ValueError):
            distance = None

        points.append(
            Point(
                lat=lat,
                lon=lon,
                when=when,
                speed=speed,
                distance=distance,
                ignition=row.get("ignition"),
                raw_id=raw_id,
            )
        )

    points.sort(key=lambda point: point.when)
    return points


def _extract_valid_points(data: dict[str, Any], device_id: int) -> list[Point]:
    """Parse real route points only; id=-1 playback snapshots are excluded."""
    return _extract_position_samples(data, device_id, include_synthetic=False)



def _unique_points(points: list[Point]) -> list[Point]:
    result: list[Point] = []
    seen: set[tuple[Any, ...]] = set()
    for point in points:
        sig = (
            point.when.isoformat(),
            round(point.lat, 6),
            round(point.lon, 6),
            point.distance,
            point.speed,
        )
        if sig in seen:
            continue
        seen.add(sig)
        result.append(point)
    return result


def _window_home_analysis(
    evening_points: list[Point],
    morning_points: list[Point],
    zone: tuple[float, float, float] | None,
    evening_start: datetime,
    evening_end: datetime,
    morning_start: datetime,
    morning_end: datetime,
) -> dict[str, Any]:
    """Determine Home presence only inside the exact 18:00-06:00 night.

    The Alapnyomkövetés web playback can show a parked car even with no route.
    To reproduce that reliably, the night is queried as TWO exact playback
    intervals:
      - 18:00 -> 23:59:59
      - 00:00 -> 06:00

    Synthetic id=-1 playback state points are accepted only from those exact
    queries. Absolutely no point before 18:00 or after 06:00 is allowed to
    influence the decision.

    Decision:
      * any real/synthetic point inside zone.home -> HOME
      * if both exact subqueries return a playback state and neither is Home,
        and no Home point exists -> NOT HOME
      * otherwise -> UNKNOWN
    """
    if zone is None:
        return {
            "home": None,
            "first_home_time": None,
            "last_home_time": None,
            "min_distance_m": None,
            "min_time": None,
            "points_inside": 0,
            "evidence": "zone_unavailable",
            "anchor_time": None,
            "anchor_distance_m": None,
        }

    evening = sorted(
        [p for p in evening_points if evening_start <= p.when <= evening_end],
        key=lambda p: p.when,
    )
    morning = sorted(
        [p for p in morning_points if morning_start <= p.when <= morning_end],
        key=lambda p: p.when,
    )
    ordered = evening + morning

    if not ordered:
        return {
            "home": None,
            "first_home_time": None,
            "last_home_time": None,
            "min_distance_m": None,
            "min_time": None,
            "points_inside": 0,
            "evidence": "no_exact_night_samples",
            "anchor_time": None,
            "anchor_distance_m": None,
        }

    inside_points = [p for p in ordered if _inside(p, zone) is True]
    nearest_point, nearest_distance = _nearest_point_to_zone(ordered, zone)

    if inside_points:
        first = inside_points[0]
        last = inside_points[-1]
        evidence = (
            "exact_playback_snapshot_home"
            if any(p.raw_id == -1 for p in inside_points)
            else "exact_direct_home_point"
        )
        return {
            "home": True,
            "first_home_time": first.when.isoformat(),
            "last_home_time": last.when.isoformat(),
            "min_distance_m": round(nearest_distance, 1) if nearest_distance is not None else None,
            "min_time": nearest_point.when.isoformat() if nearest_point else None,
            "points_inside": len(inside_points),
            "evidence": evidence,
            "anchor_time": first.when.isoformat(),
            "anchor_distance_m": (
                round(_point_distance_to_zone(first, zone), 1)
                if _point_distance_to_zone(first, zone) is not None
                else None
            ),
        }

    def _query_start_snapshot(
        points: list[Point],
        query_start: datetime,
    ) -> Point | None:
        synthetic = [p for p in points if p.raw_id == -1]
        if not synthetic:
            return None
        snapshot = min(
            synthetic,
            key=lambda p: abs((p.when - query_start).total_seconds()),
        )
        # It must really belong to the exact query start, not some unrelated
        # synthetic row elsewhere in the response.
        if abs((snapshot.when - query_start).total_seconds()) <= 20 * 60:
            return snapshot
        return None

    evening_snapshot = _query_start_snapshot(evening, evening_start)
    morning_snapshot = _query_start_snapshot(morning, morning_start)

    # For a completed historical night, the two exact playback snapshots are
    # the cleanest "where was the parked car?" evidence.
    if evening_snapshot is not None and morning_snapshot is not None:
        ed = _point_distance_to_zone(evening_snapshot, zone)
        md = _point_distance_to_zone(morning_snapshot, zone)
        closest = (
            (evening_snapshot, ed)
            if ed is not None and (md is None or ed <= md)
            else (morning_snapshot, md)
        )
        cp, cd = closest
        return {
            "home": False,
            "first_home_time": None,
            "last_home_time": None,
            "min_distance_m": round(cd, 1) if cd is not None else None,
            "min_time": cp.when.isoformat() if cp else None,
            "points_inside": 0,
            "evidence": "exact_playback_snapshots_outside",
            "anchor_time": evening_snapshot.when.isoformat(),
            "anchor_distance_m": round(ed, 1) if ed is not None else None,
        }

    # If one subquery has no synthetic snapshot but is densely observed by real
    # GPS points and all are outside Home, we can still use that section.
    def _section_observed_outside(points: list[Point], start: datetime, end: datetime) -> bool:
        real = [p for p in points if p.raw_id != -1]
        if not real:
            return False
        first = real[0]
        last = real[-1]
        near_start = (first.when - start) <= timedelta(minutes=30)
        near_end = (end - last.when) <= timedelta(minutes=30)
        return near_start and near_end and all(_inside(p, zone) is not True for p in real)

    evening_outside = (
        evening_snapshot is not None
        or _section_observed_outside(evening, evening_start, evening_end)
    )
    morning_outside = (
        morning_snapshot is not None
        or _section_observed_outside(morning, morning_start, morning_end)
    )

    if evening_outside and morning_outside:
        return {
            "home": False,
            "first_home_time": None,
            "last_home_time": None,
            "min_distance_m": round(nearest_distance, 1) if nearest_distance is not None else None,
            "min_time": nearest_point.when.isoformat() if nearest_point else None,
            "points_inside": 0,
            "evidence": "exact_night_observed_outside",
            "anchor_time": (
                evening_snapshot.when.isoformat()
                if evening_snapshot is not None
                else (evening[0].when.isoformat() if evening else None)
            ),
            "anchor_distance_m": (
                round(_point_distance_to_zone(evening_snapshot, zone), 1)
                if evening_snapshot is not None
                and _point_distance_to_zone(evening_snapshot, zone) is not None
                else None
            ),
        }

    return {
        "home": None,
        "first_home_time": None,
        "last_home_time": None,
        "min_distance_m": round(nearest_distance, 1) if nearest_distance is not None else None,
        "min_time": nearest_point.when.isoformat() if nearest_point else None,
        "points_inside": 0,
        "evidence": "incomplete_exact_night_playback",
        "anchor_time": (
            evening_snapshot.when.isoformat()
            if evening_snapshot is not None
            else (
                morning_snapshot.when.isoformat()
                if morning_snapshot is not None
                else None
            )
        ),
        "anchor_distance_m": (
            round(_point_distance_to_zone(evening_snapshot, zone), 1)
            if evening_snapshot is not None
            and _point_distance_to_zone(evening_snapshot, zone) is not None
            else (
                round(_point_distance_to_zone(morning_snapshot, zone), 1)
                if morning_snapshot is not None
                and _point_distance_to_zone(morning_snapshot, zone) is not None
                else None
            )
        ),
    }



def _summarize_data(
    hass: HomeAssistant,
    device_id: int,
    home_zone: str,
    work_zone: str,
    home_gps_radius_m: float,
    work_gps_radius_m: float,
    endpoint_window_km: float,
    target_date: date,
    data: dict[str, Any],
    overnight_before_evening_data: dict[str, Any] | None = None,
    overnight_before_morning_data: dict[str, Any] | None = None,
    overnight_after_evening_data: dict[str, Any] | None = None,
    overnight_after_morning_data: dict[str, Any] | None = None,
    overnight_query_end: datetime | None = None,
) -> DailySummary:
    key = str(device_id)
    rows = (data.get("locations") or {}).get(key, [])
    if not isinstance(rows, list):
        rows = []

    valid = _extract_valid_points(data, device_id)
    unique = _unique_points(valid)

    home = _zone_tuple(hass, home_zone, home_gps_radius_m)
    work = _zone_tuple(hass, work_zone, work_gps_radius_m)
    effective_home_radius = home[2] if home is not None else float(home_gps_radius_m)
    effective_work_radius = work[2] if work is not None else float(work_gps_radius_m)
    distance_km = _number(data.get("distances"), key, 0.0)
    max_speed_kmh = _number(data.get("maxspeed"), key, 0.0)

    # Az Alapnyomkövetés álló autónál is küldhet mintákat. Csak tényleges
    # elmozdulás esetén értékeljük indulásnak/érkezésnek a GPS pontokat.
    movement_detected = bool(distance_km is not None and distance_km >= 0.05)
    movement_points = unique if movement_detected else []
    first = movement_points[0] if movement_points else None
    last = movement_points[-1] if movement_points else None

    # A tracker első/utolsó valódi GPS mintája gyakran már néhány száz méterre
    # van a tényleges indulási/érkezési helytől. Ezért az indulást az út első,
    # az érkezést az út utolsó konfigurálható szakaszán vizsgáljuk.
    start_window = _path_window_from_start(movement_points, endpoint_window_km)
    end_window = _path_window_from_end(movement_points, endpoint_window_km)

    start_home_point, start_home_min = _nearest_point_to_zone(start_window, home)
    end_home_point, end_home_min = _nearest_point_to_zone(end_window, home)
    start_work_point, start_work_min = _nearest_point_to_zone(start_window, work)
    end_work_point, end_work_min = _nearest_point_to_zone(end_window, work)

    # Éjszakai szabály:
    # előző nap 18:00 -> munkanap 06:00, illetve
    # munkanap 18:00 -> következő nap 06:00.
    # Itt a tényleges zone.home sugarat használjuk, és az álló GPS-pontok is számítanak.
    tz = dt_util.now().tzinfo or dt_util.DEFAULT_TIME_ZONE
    before_start = datetime.combine(target_date - timedelta(days=1), time(18, 0), tzinfo=tz)
    before_end = datetime.combine(target_date, time(6, 0), tzinfo=tz)
    after_start = datetime.combine(target_date, time(18, 0), tzinfo=tz)
    after_end = datetime.combine(target_date + timedelta(days=1), time(6, 0), tzinfo=tz)

    exact_home = _zone_tuple(hass, home_zone, None)
    # Exact-night state uses ONLY the four exact playback subqueries.
    # No before-18:00 or after-06:00 context is allowed.
    before_evening_end = datetime.combine(
        target_date, time(0, 0), tzinfo=tz
    ) - timedelta(seconds=1)
    before_morning_start = datetime.combine(
        target_date, time(0, 0), tzinfo=tz
    )
    after_evening_end = datetime.combine(
        target_date + timedelta(days=1), time(0, 0), tzinfo=tz
    ) - timedelta(seconds=1)
    after_morning_start = datetime.combine(
        target_date + timedelta(days=1), time(0, 0), tzinfo=tz
    )

    before_evening_points = _unique_points(
        _extract_position_samples(
            overnight_before_evening_data or {},
            device_id,
            include_synthetic=True,
        )
    )
    before_morning_points = _unique_points(
        _extract_position_samples(
            overnight_before_morning_data or {},
            device_id,
            include_synthetic=True,
        )
    )
    after_evening_points = _unique_points(
        _extract_position_samples(
            overnight_after_evening_data or {},
            device_id,
            include_synthetic=True,
        )
    )
    after_morning_points = _unique_points(
        _extract_position_samples(
            overnight_after_morning_data or {},
            device_id,
            include_synthetic=True,
        )
    )

    before_night = _window_home_analysis(
        before_evening_points,
        before_morning_points,
        exact_home,
        before_start,
        before_evening_end,
        before_morning_start,
        before_end,
    )
    after_night = _window_home_analysis(
        after_evening_points,
        after_morning_points,
        exact_home,
        after_start,
        after_evening_end,
        after_morning_start,
        after_end,
    )

    query_end = overnight_query_end or dt_util.now()
    before_complete = query_end >= before_end
    after_complete = query_end >= after_end

    return DailySummary(
        date=target_date.isoformat(),
        distance_km=distance_km,
        movement_detected=movement_detected,
        max_speed_kmh=max_speed_kmh,
        total_records=len(rows),
        valid_points=len(valid),
        unique_points=len(unique),
        first_point=first,
        last_point=last,
        started_home=(
            None
            if home is None
            else (
                start_home_min is not None and start_home_min <= effective_home_radius
            )
            if movement_detected
            else False
        ),
        ended_home=(
            None
            if home is None
            else (
                end_home_min is not None and end_home_min <= effective_home_radius
            )
            if movement_detected
            else False
        ),
        touched_home=(
            None if home is None else _any_inside(movement_points, home) if movement_detected else False
        ),
        started_work=(
            None
            if work is None
            else (
                start_work_min is not None and start_work_min <= effective_work_radius
            )
            if movement_detected
            else False
        ),
        ended_work=(
            None
            if work is None
            else (
                end_work_min is not None and end_work_min <= effective_work_radius
            )
            if movement_detected
            else False
        ),
        touched_work=(
            None if work is None else _any_inside(movement_points, work) if movement_detected else False
        ),
        home_radius_m=effective_home_radius,
        work_radius_m=effective_work_radius,
        endpoint_window_km=float(endpoint_window_km),
        start_home_min_distance_m=round(start_home_min, 1) if start_home_min is not None else None,
        end_home_min_distance_m=round(end_home_min, 1) if end_home_min is not None else None,
        start_work_min_distance_m=round(start_work_min, 1) if start_work_min is not None else None,
        end_work_min_distance_m=round(end_work_min, 1) if end_work_min is not None else None,
        start_home_min_time=start_home_point.when.isoformat() if start_home_point else None,
        end_home_min_time=end_home_point.when.isoformat() if end_home_point else None,
        start_work_min_time=start_work_point.when.isoformat() if start_work_point else None,
        end_work_min_time=end_work_point.when.isoformat() if end_work_point else None,
        overnight_home_radius_m=exact_home[2] if exact_home is not None else None,
        overnight_before_home=before_night["home"],
        overnight_after_home=after_night["home"],
        overnight_before_complete=before_complete,
        overnight_after_complete=after_complete,
        overnight_before_first_home_time=before_night["first_home_time"],
        overnight_before_last_home_time=before_night["last_home_time"],
        overnight_after_first_home_time=after_night["first_home_time"],
        overnight_after_last_home_time=after_night["last_home_time"],
        overnight_before_min_distance_m=before_night["min_distance_m"],
        overnight_before_min_time=before_night["min_time"],
        overnight_after_min_distance_m=after_night["min_distance_m"],
        overnight_after_min_time=after_night["min_time"],
        overnight_before_points_inside=before_night["points_inside"],
        overnight_after_points_inside=after_night["points_inside"],
        overnight_before_evidence=before_night["evidence"],
        overnight_after_evidence=after_night["evidence"],
        overnight_before_anchor_time=before_night["anchor_time"],
        overnight_after_anchor_time=after_night["anchor_time"],
        overnight_before_anchor_distance_m=before_night["anchor_distance_m"],
        overnight_after_anchor_distance_m=after_night["anchor_distance_m"],
    )


def _leg_state(
    car_home: bool | None,
    complete: bool,
) -> bool | None:
    """Return whether ONE private-car commute leg is reimbursable.

    Company car at Home in the adjacent night window means that commute leg
    was replaced by the company car -> False.
    Company car definitely not at Home -> True.
    Unknown/incomplete night evidence -> None.
    """
    if not complete:
        return None
    if car_home is True:
        return False
    if car_home is False:
        return True
    return None


def evaluate_commute_legs(
    kelio_present: bool | None,
    data: DailySummary,
) -> dict[str, Any]:
    """Evaluate morning and evening commute legs independently.

    This fixes the key business case:
      Monday own car TO work + Friday own car HOME = 2 reimbursable legs
      = 1 full reimbursement day, even though the legs are on different dates.
    """
    if kelio_present is None:
        return {
            "morning": None,
            "evening": None,
            "eligible_legs": 0,
            "ineligible_legs": 0,
            "unknown_legs": 2,
            "day_equivalent": 0.0,
            "reason": "Kelio jelenlét nem állapítható meg",
        }

    if not kelio_present:
        return {
            "morning": False,
            "evening": False,
            "eligible_legs": 0,
            "ineligible_legs": 2,
            "unknown_legs": 0,
            "day_equivalent": 0.0,
            "reason": "Kelio szerint nincs igazolt munkanapi jelenlét",
        }

    morning = _leg_state(
        data.overnight_before_home,
        data.overnight_before_complete,
    )
    evening = _leg_state(
        data.overnight_after_home,
        data.overnight_after_complete,
    )

    states = [morning, evening]
    eligible_legs = sum(value is True for value in states)
    ineligible_legs = sum(value is False for value in states)
    unknown_legs = sum(value is None for value in states)

    def _night_reason(
        name: str,
        eligible: bool | None,
        home: bool | None,
        first_when: str | None,
        last_when: str | None,
    ) -> str:
        if eligible is True:
            return f"{name}: JÁR"
        if eligible is None:
            return f"{name}: ?"
        first_text = (
            first_when[11:19]
            if isinstance(first_when, str) and len(first_when) >= 19
            else "-"
        )
        last_text = (
            last_when[11:19]
            if isinstance(last_when, str) and len(last_when) >= 19
            else "-"
        )
        if home is True:
            return f"{name}: NEM JÁR (céges autó Otthon: {first_text}–{last_text})"
        return f"{name}: NEM JÁR"

    morning_text = _night_reason(
        "reggeli út",
        morning,
        data.overnight_before_home,
        data.overnight_before_first_home_time,
        data.overnight_before_last_home_time,
    )
    evening_text = _night_reason(
        "esti út",
        evening,
        data.overnight_after_home,
        data.overnight_after_first_home_time,
        data.overnight_after_last_home_time,
    )

    return {
        "morning": morning,
        "evening": evening,
        "eligible_legs": eligible_legs,
        "ineligible_legs": ineligible_legs,
        "unknown_legs": unknown_legs,
        "day_equivalent": eligible_legs / 2.0,
        "reason": f"Kelio jelenlét igazolt; {morning_text}; {evening_text}",
    }


def evaluate_eligibility(
    kelio_present: bool | None,
    data: DailySummary,
) -> tuple[bool | None, str]:
    """Compatibility whole-day state; accounting itself is leg based."""
    result = evaluate_commute_legs(kelio_present, data)
    if result["unknown_legs"] > 0:
        return None, result["reason"]
    if result["eligible_legs"] == 2:
        return True, result["reason"]
    if result["eligible_legs"] == 0:
        return False, result["reason"]
    # One reimbursable leg is intentionally a partial day, not True/False.
    return None, result["reason"]




def _html_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value)
    text = re.sub(r"(?i)<br\\s*/?>", " ", text)
    text = re.sub(r"(?is)<script.*?>.*?</script>", " ", text)
    text = re.sub(r"(?is)<style.*?>.*?</style>", " ", text)
    text = re.sub(r"(?s)</?(?:div|span|p|td|th|li|ul|ol|strong|b|em|i|a)\\b[^>]*>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html.unescape(text)
    return " ".join(text.split())


def _html_rel_coordinate(value: Any) -> dict[str, float] | None:
    if value is None:
        return None
    match = re.search(
        r"""rel=["']\\s*(-?\\d+(?:\\.\\d+)?)\\s*,\\s*(-?\\d+(?:\\.\\d+)?)\\s*["']""",
        str(value),
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    try:
        return {
            "latitude": float(match.group(1)),
            "longitude": float(match.group(2)),
        }
    except ValueError:
        return None


def _parse_route_segments(route_stats_data: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(route_stats_data, dict):
        return []
    grid = route_stats_data.get("device_grid")
    if not isinstance(grid, list):
        return []

    parsed = []
    for row in grid:
        if not isinstance(row, dict):
            continue
        parsed.append({
            "route_id": _html_text(row.get("route_id")),
            "from_date": _html_text(row.get("from_date")),
            "from_address": _html_text(row.get("from_pos")),
            "from_point": _html_rel_coordinate(row.get("from_pos")),
            "to_date": _html_text(row.get("to_date")),
            "to_address": _html_text(row.get("to_pos")),
            "to_point": _html_rel_coordinate(row.get("to_pos")),
            "travel_time": _html_text(row.get("time")),
            "distance": _html_text(row.get("distance")),
            "average_speed": _html_text(row.get("average_speed")),
            "max_speed": _html_text(row.get("max_speed")),
            "stop_time": _html_text(row.get("stop_time")),
            "user_name": _html_text(row.get("user_name")),
            "type": _html_text(row.get("type")),
        })
    return parsed


def _daily_to_record(
    summary: DailySummary,
    kelio_present: bool,
    eligible: bool | None,
    reason: str,
    *,
    morning_eligible: bool | None = None,
    evening_eligible: bool | None = None,
    eligible_legs: int = 0,
    ineligible_legs: int = 0,
    unknown_legs: int = 0,
) -> dict[str, Any]:
    return {
        "date": summary.date,
        "kelio_present": bool(kelio_present),
        "presence_source": "kelio" if kelio_present else None,
        "manual_override": False,
        "manual_note": None,
        "gps_checked": True,
        "commute_eligible": eligible,
        "morning_commute_eligible": morning_eligible,
        "evening_commute_eligible": evening_eligible,
        "eligible_legs": eligible_legs,
        "ineligible_legs": ineligible_legs,
        "unknown_legs": unknown_legs,
        "eligible_day_equivalent": eligible_legs / 2.0,
        "reason": reason,
        "company_car": {
            "distance_km": summary.distance_km,
            "movement_detected": summary.movement_detected,
            "max_speed_kmh": summary.max_speed_kmh,
            "first_time": summary.first_point.when.isoformat() if summary.first_point else None,
            "last_time": summary.last_point.when.isoformat() if summary.last_point else None,
            "first_point": {
                "latitude": summary.first_point.lat,
                "longitude": summary.first_point.lon,
            } if summary.first_point else None,
            "last_point": {
                "latitude": summary.last_point.lat,
                "longitude": summary.last_point.lon,
            } if summary.last_point else None,
            "started_home": summary.started_home,
            "ended_home": summary.ended_home,
            "touched_home": summary.touched_home,
            "started_work": summary.started_work,
            "ended_work": summary.ended_work,
            "touched_work": summary.touched_work,
            "home_radius_m": summary.home_radius_m,
            "work_radius_m": summary.work_radius_m,
            "endpoint_window_km": summary.endpoint_window_km,
            "start_home_min_distance_m": summary.start_home_min_distance_m,
            "end_home_min_distance_m": summary.end_home_min_distance_m,
            "start_work_min_distance_m": summary.start_work_min_distance_m,
            "end_work_min_distance_m": summary.end_work_min_distance_m,
            "start_home_min_time": summary.start_home_min_time,
            "end_home_min_time": summary.end_home_min_time,
            "start_work_min_time": summary.start_work_min_time,
            "end_work_min_time": summary.end_work_min_time,
            "overnight_home_radius_m": summary.overnight_home_radius_m,
            "overnight_before_home": summary.overnight_before_home,
            "overnight_after_home": summary.overnight_after_home,
            "overnight_before_complete": summary.overnight_before_complete,
            "overnight_after_complete": summary.overnight_after_complete,
            "overnight_before_first_home_time": summary.overnight_before_first_home_time,
            "overnight_before_last_home_time": summary.overnight_before_last_home_time,
            "overnight_after_first_home_time": summary.overnight_after_first_home_time,
            "overnight_after_last_home_time": summary.overnight_after_last_home_time,
            "overnight_before_min_distance_m": summary.overnight_before_min_distance_m,
            "overnight_before_min_time": summary.overnight_before_min_time,
            "overnight_after_min_distance_m": summary.overnight_after_min_distance_m,
            "overnight_after_min_time": summary.overnight_after_min_time,
            "overnight_before_points_inside": summary.overnight_before_points_inside,
            "overnight_after_points_inside": summary.overnight_after_points_inside,
            "overnight_before_evidence": summary.overnight_before_evidence,
            "overnight_after_evidence": summary.overnight_after_evidence,
            "overnight_before_anchor_time": summary.overnight_before_anchor_time,
            "overnight_after_anchor_time": summary.overnight_after_anchor_time,
            "overnight_before_anchor_distance_m": summary.overnight_before_anchor_distance_m,
            "overnight_after_anchor_distance_m": summary.overnight_after_anchor_distance_m,
            "total_records": summary.total_records,
            "valid_points": summary.valid_points,
            "unique_points": summary.unique_points,
            "route_stats_available": summary.route_stats_available,
            "route_segments": summary.route_segments or [],
            "route_segments_count": len(summary.route_segments or []),
            "route_stats_source": "/statistics/ajaxGetLocationLines + /statistics/ajaxGetStatDeviceGrid",
        },
    }



def _route_export_urls(target_date: date) -> tuple[str, str]:
    day = target_date.isoformat()
    base = f"/local/utnyilvantartas/routes/utvonal_{day}"
    return f"{base}.html", f"{base}.gpx"


def _write_route_exports(
    html_path: Path,
    gpx_path: Path,
    target_date: date,
    device_id: int,
    points: list[Point],
) -> None:
    """Write exact-day route viewer + GPX from the already fetched GPS points."""
    html_path.parent.mkdir(parents=True, exist_ok=True)
    gpx_path.parent.mkdir(parents=True, exist_ok=True)

    # GPX: exact real tracker points (synthetic id=-1 points are already excluded).
    gpx_lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<gpx version="1.1" creator="Home Assistant Útnyilvántartás" '
        'xmlns="http://www.topografix.com/GPX/1/1">',
        f'  <metadata><name>Alapnyomkövetés {target_date.isoformat()}</name></metadata>',
        f'  <trk><name>{target_date.isoformat()} · eszköz {device_id}</name><trkseg>',
    ]
    for point in points:
        gpx_lines.append(
            f'    <trkpt lat="{point.lat:.8f}" lon="{point.lon:.8f}">'
            f'<time>{xml_escape(point.when.isoformat())}</time></trkpt>'
        )
    gpx_lines += ['  </trkseg></trk>', '</gpx>']
    gpx_path.write_text("\n".join(gpx_lines) + "\n", encoding="utf-8")

    coords = [[round(p.lat, 7), round(p.lon, 7), p.when.isoformat()] for p in points]
    coords_json = json.dumps(coords, ensure_ascii=False)

    first = points[0] if points else None
    last = points[-1] if points else None
    first_text = first.when.strftime("%H:%M:%S") if first else "-"
    last_text = last.when.strftime("%H:%M:%S") if last else "-"

    # Leaflet + OpenStreetMap gives a familiar map. The page itself is generated
    # locally and already contains the exact requested day's GPS polyline.
    route_html = f"""<!doctype html>
<html lang="hu">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Útvonal · {target_date.isoformat()}</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<style>
html,body,#map{{height:100%;margin:0}}
body{{font-family:Arial,sans-serif;background:#f4f6f8;overflow:hidden}}
#map{{position:absolute;inset:0;background:#e9eef2}}

/* Leaflet essential fallback.
   If the CDN stylesheet is blocked or rejected, tiles/panes still stay correctly
   positioned instead of appearing as giant stacked image blocks. */
.leaflet-container{{overflow:hidden;outline:0}}
.leaflet-pane,.leaflet-tile,.leaflet-marker-icon,.leaflet-marker-shadow,
.leaflet-tile-container,.leaflet-pane>svg,.leaflet-pane>canvas,
.leaflet-zoom-box{{position:absolute;left:0;top:0}}
.leaflet-pane{{z-index:400}}
.leaflet-tile-pane{{z-index:200}}
.leaflet-overlay-pane{{z-index:400}}
.leaflet-shadow-pane{{z-index:500}}
.leaflet-marker-pane{{z-index:600}}
.leaflet-tooltip-pane{{z-index:650}}
.leaflet-popup-pane{{z-index:700}}
.leaflet-map-pane canvas{{z-index:100}}
.leaflet-map-pane svg{{z-index:200}}
.leaflet-control{{position:relative;z-index:800;pointer-events:auto}}
.leaflet-top,.leaflet-bottom{{position:absolute;z-index:1000;pointer-events:none}}
.leaflet-top{{top:0}} .leaflet-right{{right:0}} .leaflet-bottom{{bottom:0}} .leaflet-left{{left:0}}
.leaflet-control{{float:left;clear:both}}
.leaflet-right .leaflet-control{{float:right}}
.leaflet-top .leaflet-control{{margin-top:10px}}
.leaflet-bottom .leaflet-control{{margin-bottom:10px}}
.leaflet-left .leaflet-control{{margin-left:10px}}
.leaflet-right .leaflet-control{{margin-right:10px}}
.leaflet-tile{{visibility:hidden}}
.leaflet-tile-loaded{{visibility:inherit}}
.leaflet-zoom-animated{{transform-origin:0 0}}
.leaflet-interactive{{cursor:pointer}}
.leaflet-grab{{cursor:grab}}
.leaflet-dragging .leaflet-grab{{cursor:grabbing}}
.leaflet-tile,.leaflet-marker-icon,.leaflet-marker-shadow{{user-select:none;-webkit-user-drag:none}}
.leaflet-marker-icon,.leaflet-marker-shadow{{display:block}}
.leaflet-container .leaflet-overlay-pane svg{{max-width:none!important;max-height:none!important}}
.leaflet-container .leaflet-marker-pane img,
.leaflet-container .leaflet-shadow-pane img,
.leaflet-container .leaflet-tile-pane img,
.leaflet-container img.leaflet-image-layer,
.leaflet-container .leaflet-tile{{max-width:none!important;max-height:none!important;width:256px;height:256px}}
.panel{{position:absolute;z-index:1100;top:14px;left:14px;width:min(620px,calc(100vw - 28px));
background:rgba(255,255,255,.96);border-radius:14px;padding:12px 14px;
box-shadow:0 4px 18px rgba(0,0,0,.18);backdrop-filter:blur(6px)}}
.title{{font-size:18px;font-weight:800;margin-bottom:5px}}
.meta{{font-size:12px;color:#555;line-height:1.5}}
.actions{{display:flex;gap:8px;flex-wrap:wrap;margin-top:9px}}
.actions a{{display:inline-flex;align-items:center;text-decoration:none;border-radius:9px;
padding:8px 11px;font-size:12px;font-weight:700;background:#1976d2;color:#fff}}
.actions a.secondary{{background:#546e7a}}
.warn{{display:none;margin-top:8px;padding:8px 10px;background:#fff3cd;border-radius:8px;font-size:12px}}
@media (max-width:700px){{
  .panel{{top:8px;left:8px;width:calc(100vw - 16px);padding:10px 11px}}
  .title{{font-size:16px}}
  .meta{{font-size:11px}}
}}
</style>
</head>
<body>
<div id="map"></div>
<div class="panel">
  <div class="title">Aznapi cégesautó-útvonal · {target_date.isoformat()}</div>
  <div class="meta">Eszköz: {device_id} · GPS-pontok: {len(points)} · első: {first_text} · utolsó: {last_text}</div>
  <div class="actions">
    <a href="utvonal_{target_date.isoformat()}.gpx" download>GPX letöltése</a>
    <a class="secondary" href="https://service.alapnyomkovetes.hu/positions/past" target="_blank" rel="noopener">Alapnyomkövetés</a>
  </div>
  <div id="warn" class="warn">A térképréteg nem töltődött be, de a GPX fájl ettől még letölthető.</div>
</div>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script>
const pts = {coords_json};
if (typeof L === "undefined") {{
  document.getElementById("warn").style.display = "block";
}} else {{
  const map = L.map("map", {{ zoomControl:true, preferCanvas:true }});
  L.tileLayer("https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png", {{
    maxZoom: 19,
    minZoom: 2,
    tileSize: 256,
    updateWhenIdle: true,
    keepBuffer: 3,
    attribution: "&copy; OpenStreetMap"
  }}).addTo(map);
  setTimeout(() => map.invalidateSize(true), 50);
  if (pts.length) {{
    const latlngs = pts.map(p => [p[0], p[1]]);
    const line = L.polyline(latlngs, {{weight:6, opacity:.92}}).addTo(map);
    map.fitBounds(line.getBounds(), {{paddingTopLeft:[45,150],paddingBottomRight:[45,45],maxZoom:15}});
    const a = pts[0], b = pts[pts.length - 1];
    L.marker([a[0],a[1]]).addTo(map).bindPopup("Indulás<br>" + a[2]);
    L.marker([b[0],b[1]]).addTo(map).bindPopup("Érkezés<br>" + b[2]);
  }} else {{
    map.setView([47.4979,19.0402], 9);
    document.getElementById("warn").textContent = "Ehhez a naphoz nincs valódi GPS-útvonal.";
    document.getElementById("warn").style.display = "block";
  }}
}}
</script>
</body>
</html>
"""
    html_path.write_text(route_html, encoding="utf-8")



def _write_route_evaluation_print(
    print_path: Path,
    target_date: date,
    device_id: int,
    points: list[Point],
    segments: list[dict[str, Any]],
) -> None:
    """Create an A4-landscape printable map + server-side route evaluation."""
    print_path.parent.mkdir(parents=True, exist_ok=True)

    coords = [[round(p.lat, 7), round(p.lon, 7), p.when.isoformat()] for p in points]
    coords_json = json.dumps(coords, ensure_ascii=False)

    rows: list[str] = []
    for segment in segments:
        def esc(key: str) -> str:
            return html.escape(str(segment.get(key) or "-"))

        rows.append(
            "<tr>"
            f"<td>{esc('route_id')}</td>"
            f"<td>{esc('from_date')}</td>"
            f"<td>{esc('from_address')}</td>"
            f"<td>{esc('to_date')}</td>"
            f"<td>{esc('to_address')}</td>"
            f"<td>{esc('travel_time')}</td>"
            f"<td class='num'>{esc('distance')}</td>"
            f"<td class='num'>{esc('average_speed')}</td>"
            f"<td class='num'>{esc('max_speed')}</td>"
            f"<td>{esc('stop_time')}</td>"
            "</tr>"
        )

    if not rows:
        rows.append(
            "<tr><td colspan='10' class='empty'>"
            "Az Alapnyomkövetés erre a napra nem adott vissza kiértékelési szakaszt."
            "</td></tr>"
        )

    rows_html = "\n".join(rows)
    day = target_date.isoformat()
    point_count = len(points)
    segment_count = len(segments)

    document = f"""<!doctype html>
<html lang="hu">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Kiértékelés + térkép · {day}</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<style>
*{{box-sizing:border-box}}
html,body{{margin:0;padding:0;background:#eef1f4;color:#111;font-family:Arial,Helvetica,sans-serif}}
body{{padding:14px}}
.sheet{{max-width:1500px;margin:0 auto;background:#fff;border-radius:14px;box-shadow:0 3px 18px rgba(0,0,0,.14);padding:16px}}
.head{{display:flex;align-items:flex-start;justify-content:space-between;gap:16px;margin-bottom:12px}}
h1{{font-size:24px;margin:0 0 4px}}
.meta{{font-size:12px;color:#555;line-height:1.5}}
.actions{{display:flex;gap:8px;flex-wrap:wrap}}
.actions button,.actions a{{border:0;border-radius:9px;padding:9px 12px;font:inherit;font-size:12px;font-weight:700;text-decoration:none;cursor:pointer}}
.actions button{{background:#1976d2;color:#fff}}
.actions a{{background:#546e7a;color:#fff}}
#map{{height:420px;border:1px solid #cfd8dc;border-radius:10px;margin-bottom:12px;background:#e9eef2;overflow:hidden}}
.section-title{{font-size:17px;font-weight:800;margin:8px 0}}
.table-wrap{{overflow:auto;border:1px solid #cfd8dc;border-radius:9px}}
table{{width:100%;border-collapse:collapse;font-size:10px}}
th{{background:#eceff1;text-align:left;padding:7px 6px;border-bottom:1px solid #b0bec5;text-transform:uppercase;font-size:9px}}
td{{padding:7px 6px;border-top:1px solid #e0e0e0;vertical-align:top;line-height:1.3}}
tbody tr:nth-child(odd){{background:#f8fbfd}}
.num{{text-align:right;white-space:nowrap}}
.empty{{padding:18px;text-align:center;color:#666}}
.footer{{margin-top:8px;font-size:9px;color:#777;display:flex;justify-content:space-between;gap:10px}}
.print-note{{font-size:10px;color:#555;margin-top:7px}}

/* Leaflet fallback in case external CSS is blocked */
.leaflet-container{{overflow:hidden;outline:0}}
.leaflet-pane,.leaflet-tile,.leaflet-marker-icon,.leaflet-marker-shadow,
.leaflet-tile-container,.leaflet-pane>svg,.leaflet-pane>canvas,
.leaflet-zoom-box{{position:absolute;left:0;top:0}}
.leaflet-pane{{z-index:400}} .leaflet-tile-pane{{z-index:200}}
.leaflet-overlay-pane{{z-index:400}} .leaflet-shadow-pane{{z-index:500}}
.leaflet-marker-pane{{z-index:600}} .leaflet-tooltip-pane{{z-index:650}}
.leaflet-popup-pane{{z-index:700}}
.leaflet-map-pane canvas{{z-index:100}} .leaflet-map-pane svg{{z-index:200}}
.leaflet-control{{position:relative;z-index:800;pointer-events:auto}}
.leaflet-top,.leaflet-bottom{{position:absolute;z-index:1000;pointer-events:none}}
.leaflet-top{{top:0}} .leaflet-right{{right:0}} .leaflet-bottom{{bottom:0}} .leaflet-left{{left:0}}
.leaflet-control{{float:left;clear:both}} .leaflet-right .leaflet-control{{float:right}}
.leaflet-top .leaflet-control{{margin-top:10px}} .leaflet-bottom .leaflet-control{{margin-bottom:10px}}
.leaflet-left .leaflet-control{{margin-left:10px}} .leaflet-right .leaflet-control{{margin-right:10px}}
.leaflet-tile{{visibility:hidden}} .leaflet-tile-loaded{{visibility:inherit}}
.leaflet-zoom-animated{{transform-origin:0 0}} .leaflet-interactive{{cursor:pointer}}
.leaflet-tile,.leaflet-marker-icon,.leaflet-marker-shadow{{user-select:none;-webkit-user-drag:none}}
.leaflet-marker-icon,.leaflet-marker-shadow{{display:block}}
.leaflet-container .leaflet-overlay-pane svg{{max-width:none!important;max-height:none!important}}
.leaflet-container .leaflet-marker-pane img,
.leaflet-container .leaflet-shadow-pane img,
.leaflet-container .leaflet-tile-pane img,
.leaflet-container img.leaflet-image-layer,
.leaflet-container .leaflet-tile{{max-width:none!important;max-height:none!important;width:256px;height:256px}}

@page{{size:A4 landscape;margin:7mm}}
@media print{{
  html,body{{background:#fff}}
  body{{padding:0}}
  .sheet{{max-width:none;margin:0;padding:0;border-radius:0;box-shadow:none}}
  .actions,.print-note{{display:none!important}}
  .head{{margin-bottom:5mm}}
  h1{{font-size:18pt}}
  .meta{{font-size:8.5pt}}
  #map{{height:92mm;border-radius:0;margin-bottom:4mm}}
  .section-title{{font-size:12pt;margin:2mm 0}}
  .table-wrap{{overflow:visible;border-radius:0}}
  table{{font-size:7pt;table-layout:fixed}}
  th{{font-size:6.5pt;padding:1.7mm 1.2mm}}
  td{{padding:1.5mm 1.2mm;overflow-wrap:anywhere}}
  th:nth-child(1),td:nth-child(1){{width:5%}}
  th:nth-child(2),td:nth-child(2){{width:10%}}
  th:nth-child(3),td:nth-child(3){{width:17%}}
  th:nth-child(4),td:nth-child(4){{width:10%}}
  th:nth-child(5),td:nth-child(5){{width:17%}}
  th:nth-child(6),td:nth-child(6){{width:8%}}
  th:nth-child(7),td:nth-child(7){{width:8%}}
  th:nth-child(8),td:nth-child(8){{width:8%}}
  th:nth-child(9),td:nth-child(9){{width:8%}}
  th:nth-child(10),td:nth-child(10){{width:9%}}
  .footer{{font-size:6.5pt}}
}}
</style>
</head>
<body>
<div class="sheet">
  <div class="head">
    <div>
      <h1>Alapnyomkövetés · Kiértékelés + térkép</h1>
      <div class="meta">
        Dátum: <b>{day}</b> · Eszköz: <b>{device_id}</b><br>
        GPS-pontok: <b>{point_count}</b> · Kiértékelési szakaszok: <b>{segment_count}</b>
      </div>
    </div>
    <div class="actions">
      <button onclick="window.print()">🖨 Nyomtatás / Mentés PDF-be</button>
      <a href="utvonal_{day}.gpx" download>GPX letöltése</a>
    </div>
  </div>

  <div id="map"></div>

  <div class="section-title">Kiértékelés</div>
  <div class="table-wrap">
    <table>
      <thead>
        <tr>
          <th>Sorszám</th><th>Indulás</th><th>Honnan</th><th>Érkezés</th><th>Hova</th>
          <th>Menetidő</th><th>Távolság</th><th>Átlag seb.</th><th>Max. seb.</th><th>Állásidő</th>
        </tr>
      </thead>
      <tbody>
        {rows_html}
      </tbody>
    </table>
  </div>

  <div class="print-note">A táblázat az Alapnyomkövetés saját /statistics/route szerveroldali kiértékeléséből származik.</div>
  <div class="footer">
    <span>Home Assistant · Útnyilvántartás</span>
    <span>{day}</span>
  </div>
</div>

<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script>
const pts = {coords_json};
if (typeof L !== "undefined") {{
  const map = L.map("map", {{zoomControl:true, preferCanvas:true}});
  L.tileLayer("https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png", {{
    maxZoom:19, minZoom:2, tileSize:256, keepBuffer:3,
    attribution:"&copy; OpenStreetMap"
  }}).addTo(map);

  if (pts.length) {{
    const latlngs = pts.map(p => [p[0],p[1]]);
    const line = L.polyline(latlngs, {{weight:5,opacity:.92}}).addTo(map);
    map.fitBounds(line.getBounds(), {{padding:[22,22],maxZoom:15}});
    const first = pts[0], last = pts[pts.length-1];
    L.marker([first[0],first[1]]).addTo(map).bindPopup("Indulás<br>"+first[2]);
    L.marker([last[0],last[1]]).addTo(map).bindPopup("Érkezés<br>"+last[2]);
  }} else {{
    map.setView([47.4979,19.0402],9);
  }}

  setTimeout(() => map.invalidateSize(true),100);

  window.addEventListener("beforeprint", () => {{
    map.invalidateSize(true);
    if (pts.length) {{
      const lineBounds = L.latLngBounds(pts.map(p => [p[0],p[1]]));
      map.fitBounds(lineBounds, {{padding:[15,15],maxZoom:15}});
    }}
  }});
}}
</script>
</body>
</html>
"""
    print_path.write_text(document, encoding="utf-8")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


class UtnyilvantartasCoordinator(DataUpdateCoordinator[DailySummary]):
    def __init__(
        self,
        hass: HomeAssistant,
        client: AlapnyomkovetesClient,
        device_id: int,
        home_zone: str,
        work_zone: str,
        home_gps_radius_m: float,
        work_gps_radius_m: float,
        endpoint_window_km: float,
        update_interval,
    ) -> None:
        super().__init__(
            hass,
            logger=_LOGGER,
            name=DOMAIN,
            update_interval=update_interval,
        )
        self.client = client
        self.device_id = device_id
        self.home_zone = home_zone
        self.work_zone = work_zone
        self.home_gps_radius_m = float(home_gps_radius_m)
        self.work_gps_radius_m = float(work_gps_radius_m)
        self.endpoint_window_km = float(endpoint_window_km)

    async def async_fetch_day(self, target_date: date) -> DailySummary:
        now = dt_util.now()
        tz = now.tzinfo or dt_util.DEFAULT_TIME_ZONE
        start = datetime.combine(target_date, time.min, tzinfo=tz)
        if target_date == now.date():
            end = now
        else:
            end = datetime.combine(target_date, time(23, 59), tzinfo=tz)

        result = await self.client.async_fetch_past(self.device_id, start, end)

        # Fetch the server-side Statistics/Route evaluation discovered in the
        # Alapnyomkövetés web UI. This is intentionally sidecar/diagnostic in
        # v0.4.20 until we verify the returned JSON schema on the real account.
        # A failure here must NEVER change the existing JÁR/NEM JÁR result.
        route_stats_path = Path(
            self.hass.config.path(
                "utnyilvantartas",
                "route_stats",
                f"route_stats_{target_date.isoformat()}.json",
            )
        )
        route_stats_data: dict[str, Any] | None = None
        try:
            route_stats = await self.client.async_fetch_route_statistics(
                self.device_id, start, end
            )
            route_stats_data = route_stats.data
            await self.hass.async_add_executor_job(
                _write_json, route_stats_path, route_stats.data
            )
            _LOGGER.info(
                "Alapnyomkövetés Statistics/Route lekérve: %s -> %s",
                target_date.isoformat(),
                route_stats_path,
            )
        except AlapnyomkovetesError as err:
            _LOGGER.warning(
                "Alapnyomkövetés Statistics/Route nem olvasható (%s): %s",
                target_date.isoformat(),
                err,
            )
            await self.hass.async_add_executor_job(
                _write_json,
                route_stats_path,
                {
                    "success": 0,
                    "date": target_date.isoformat(),
                    "device_id": self.device_id,
                    "error": str(err),
                },
            )

        # Generate exact-day route viewer + GPX from this SAME Alapnyomkövetés
        # response. This is diagnostic/export only; it does not change commute
        # eligibility or distance calculations.
        route_points = _unique_points(_extract_valid_points(result.data, self.device_id))
        route_dir = Path(self.hass.config.path("www", "utnyilvantartas", "routes"))
        route_html_path = route_dir / f"utvonal_{target_date.isoformat()}.html"
        route_gpx_path = route_dir / f"utvonal_{target_date.isoformat()}.gpx"
        await self.hass.async_add_executor_job(
            _write_route_exports,
            route_html_path,
            route_gpx_path,
            target_date,
            self.device_id,
            route_points,
        )

        # Exact night 1: previous day 18:00 -> target day 06:00.
        before_evening_start = datetime.combine(
            target_date - timedelta(days=1), time(18, 0), tzinfo=tz
        )
        before_evening_end = datetime.combine(
            target_date, time(0, 0), tzinfo=tz
        ) - timedelta(seconds=1)
        before_morning_start = datetime.combine(
            target_date, time(0, 0), tzinfo=tz
        )
        before_morning_end = datetime.combine(
            target_date, time(6, 0), tzinfo=tz
        )

        # Exact night 2: target day 18:00 -> next day 06:00.
        after_evening_start = datetime.combine(
            target_date, time(18, 0), tzinfo=tz
        )
        after_evening_end = datetime.combine(
            target_date + timedelta(days=1), time(0, 0), tzinfo=tz
        ) - timedelta(seconds=1)
        after_morning_start = datetime.combine(
            target_date + timedelta(days=1), time(0, 0), tzinfo=tz
        )
        after_target_end = datetime.combine(
            target_date + timedelta(days=1), time(6, 0), tzinfo=tz
        )

        # IMPORTANT: four playback queries, exactly matching the two halves of
        # the two 18:00-06:00 windows. No 06:52, no 17:28, no other context.
        before_evening_result = await self.client.async_fetch_past(
            self.device_id,
            before_evening_start,
            min(before_evening_end, now),
        )
        before_morning_result = await self.client.async_fetch_past(
            self.device_id,
            before_morning_start,
            min(before_morning_end, now),
        )

        if now >= after_evening_start:
            after_evening_result = await self.client.async_fetch_past(
                self.device_id,
                after_evening_start,
                min(after_evening_end, now),
            )
            after_evening_data = after_evening_result.data
        else:
            after_evening_data = {}

        if now >= after_morning_start:
            after_morning_result = await self.client.async_fetch_past(
                self.device_id,
                after_morning_start,
                min(after_target_end, now),
            )
            after_morning_data = after_morning_result.data
        else:
            after_morning_data = {}

        summary = _summarize_data(
            self.hass,
            self.device_id,
            self.home_zone,
            self.work_zone,
            self.home_gps_radius_m,
            self.work_gps_radius_m,
            self.endpoint_window_km,
            target_date,
            result.data,
            before_evening_result.data,
            before_morning_result.data,
            after_evening_data,
            after_morning_data,
            min(after_target_end, now),
        )
        summary.route_segments = _parse_route_segments(route_stats_data)
        summary.route_stats_available = route_stats_data is not None

        print_path = route_dir / f"kiertekeles_{target_date.isoformat()}.html"
        await self.hass.async_add_executor_job(
            _write_route_evaluation_print,
            print_path,
            target_date,
            self.device_id,
            route_points,
            summary.route_segments,
        )

        return summary

    async def _async_update_data(self) -> DailySummary:
        try:
            return await self.async_fetch_day(dt_util.now().date())
        except AlapnyomkovetesError as err:
            raise UpdateFailed(str(err)) from err


def _read_manual_overrides(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    entries = payload.get("entries") if isinstance(payload, dict) else None
    if not isinstance(entries, dict):
        return {}

    result: dict[str, dict[str, Any]] = {}
    for key, value in entries.items():
        if not isinstance(key, str) or not isinstance(value, dict):
            continue
        try:
            date.fromisoformat(key)
        except ValueError:
            continue
        result[key] = {
            "morning_eligible": bool(value.get("morning_eligible", True)),
            "evening_eligible": bool(value.get("evening_eligible", True)),
            "note": str(value.get("note") or "").strip(),
            "updated_at": str(value.get("updated_at") or ""),
        }
    return result


def _write_manual_overrides(
    path: Path,
    device_id: int,
    entries: dict[str, dict[str, Any]],
) -> None:
    payload = {
        "version": 1,
        "device_id": int(device_id),
        "updated_at": dt_util.now().isoformat(),
        "entries": dict(sorted(entries.items())),
    }
    _write_json(path, payload)


def _manual_record(day_key: str, item: dict[str, Any]) -> dict[str, Any]:
    morning = bool(item.get("morning_eligible", True))
    evening = bool(item.get("evening_eligible", True))
    eligible_legs = int(morning) + int(evening)
    ineligible_legs = 2 - eligible_legs
    if eligible_legs == 2:
        whole_day = True
    elif eligible_legs == 0:
        whole_day = False
    else:
        whole_day = None

    note = str(item.get("note") or "").strip()
    detail = note or "Hónap végi kézi kiegészítés – a Kelio aznapi bejegyzése még nem érhető el"
    return {
        "date": day_key,
        "kelio_present": True,
        "presence_source": "manual",
        "manual_override": True,
        "manual_note": detail,
        "gps_checked": False,
        "commute_eligible": whole_day,
        "morning_commute_eligible": morning,
        "evening_commute_eligible": evening,
        "eligible_legs": eligible_legs,
        "ineligible_legs": ineligible_legs,
        "unknown_legs": 0,
        "eligible_day_equivalent": eligible_legs / 2.0,
        "reason": f"Kézi kiegészítés: {detail}",
        "company_car": None,
    }



def _scan_saved_pdfs(root: Path) -> list[dict[str, Any]]:
    """List generated monthly PDFs without exposing arbitrary files."""
    if not root.exists():
        return []
    items: list[dict[str, Any]] = []
    pattern = re.compile(r"^utnyilvantartas_(\d{4}-\d{2})\.pdf$")
    for path in root.glob("utnyilvantartas_*.pdf"):
        if not path.is_file():
            continue
        match = pattern.match(path.name)
        if not match:
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        month = match.group(1)
        items.append({
            "month": month,
            "filename": path.name,
            "url": f"/local/utnyilvantartas/{path.name}?v={int(stat.st_mtime)}",
            "modified_ts": float(stat.st_mtime),
            "size_bytes": int(stat.st_size),
        })
    items.sort(key=lambda item: (item["month"], item["modified_ts"]), reverse=True)
    return items


class UtnyMonthlyCoordinator(DataUpdateCoordinator[MonthlySummary]):
    def __init__(
        self,
        hass: HomeAssistant,
        daily_coordinator: UtnyilvantartasCoordinator,
        kelio_month_entity: str,
        kelio_today_entity: str,
        commute_one_way_km: float,
        reimbursement_huf_per_km: float,
        base_odometer_km: float,
    ) -> None:
        super().__init__(
            hass,
            logger=_LOGGER,
            name=f"{DOMAIN}_monthly",
            update_interval=timedelta(hours=12),
        )
        self.daily = daily_coordinator
        self.kelio_month_entity = kelio_month_entity
        self.kelio_today_entity = kelio_today_entity
        self.commute_one_way_km = float(commute_one_way_km)
        self.reimbursement_huf_per_km = float(reimbursement_huf_per_km)
        self.base_odometer_km = float(base_odometer_km)
        self.kelio_refresh_in_progress = False
        self.last_kelio_refresh_at: str | None = None
        self.last_pdf_path: str | None = None
        self.last_pdf_url: str | None = None
        self.last_pdf_generated_at: str | None = None
        self.last_pdf_month: str | None = None
        self.last_pdf_error: str | None = None
        self.pdf_dir = Path(self.hass.config.path("www", "utnyilvantartas"))
        self.saved_pdfs: list[dict[str, Any]] = []
        self.selected_month = dt_util.now().strftime("%Y-%m")
        self.last_kelio_refresh_month: str | None = None
        self.manual_overrides_path = Path(
            self.hass.config.path(
                "utnyilvantartas",
                f"manual_overrides_{self.daily.device_id}.json",
            )
        )

    async def async_set_manual_day(
        self,
        value: str,
        *,
        morning_eligible: bool,
        evening_eligible: bool,
        note: str = "",
    ) -> None:
        try:
            target = date.fromisoformat(str(value or "").strip())
        except ValueError as err:
            raise HomeAssistantError("A kézi kiegészítés dátuma YYYY-MM-DD formátumú legyen.") from err

        today = dt_util.now().date()
        if target > today:
            raise HomeAssistantError("Jövőbeli nap nem adható hozzá kézi kiegészítésként.")

        target_month = target.strftime("%Y-%m")
        if target_month != self.selected_month:
            raise HomeAssistantError(
                f"A kézi nap a megnyitott hónaphoz tartozzon ({self.selected_month})."
            )

        # If Kelio already contains this day, automatic data must win.
        source = self.hass.states.get(self.kelio_month_entity)
        if source is not None:
            raw_dates = source.attributes.get("presence_dates") or []
            if isinstance(raw_dates, list) and target.isoformat() in raw_dates:
                raise HomeAssistantError(
                    "Ehhez a naphoz már van Kelio jelenlét. Kézi kiegészítés nem szükséges."
                )

        entries = await self.hass.async_add_executor_job(
            _read_manual_overrides, self.manual_overrides_path
        )
        entries[target.isoformat()] = {
            "morning_eligible": bool(morning_eligible),
            "evening_eligible": bool(evening_eligible),
            "note": str(note or "").strip(),
            "updated_at": dt_util.now().isoformat(),
        }
        await self.hass.async_add_executor_job(
            _write_manual_overrides,
            self.manual_overrides_path,
            self.daily.device_id,
            entries,
        )
        # Finish the monthly recalculation before the service returns so the
        # dashboard immediately receives the new state after Mentés/Felülírás.
        await self.async_refresh()

    async def async_remove_manual_day(self, value: str) -> None:
        try:
            target = date.fromisoformat(str(value or "").strip())
        except ValueError as err:
            raise HomeAssistantError("A kézi kiegészítés dátuma YYYY-MM-DD formátumú legyen.") from err

        entries = await self.hass.async_add_executor_job(
            _read_manual_overrides, self.manual_overrides_path
        )
        entries.pop(target.isoformat(), None)
        await self.hass.async_add_executor_job(
            _write_manual_overrides,
            self.manual_overrides_path,
            self.daily.device_id,
            entries,
        )
        # Deletion must also finish the monthly recalculation before returning.
        await self.async_refresh()

    @staticmethod
    def _validate_month(value: str) -> str:
        """Validate YYYY-MM and reject future months."""
        raw = str(value or "").strip()
        try:
            year_s, month_s = raw.split("-", 1)
            year = int(year_s)
            month_no = int(month_s)
            monthrange(year, month_no)
        except (ValueError, TypeError):
            raise HomeAssistantError(
                "A hónap formátuma YYYY-MM legyen, például 2026-08."
            )
        normalized = f"{year:04d}-{month_no:02d}"
        current = dt_util.now().strftime("%Y-%m")
        if normalized > current:
            raise HomeAssistantError("Jövőbeli hónap nem választható ki.")
        return normalized

    async def async_refresh_with_kelio_history(
        self,
        target_month: str | None = None,
        timeout_seconds: float = 360.0,
    ) -> None:
        """Frissítse a Kelio havi előzményeket, majd csak utána számolja újra a GPS hónapot."""
        now = dt_util.now()
        target_month = self._validate_month(target_month or self.selected_month)
        self.selected_month = target_month
        source_before = self.hass.states.get(self.kelio_month_entity)
        before_updated_at = (
            source_before.attributes.get("updated_at")
            if source_before is not None
            else None
        )
        before_last_updated = (
            source_before.last_updated
            if source_before is not None
            else None
        )

        kelio_result_entity = "sensor.kelio_utolso_eredmeny"
        result_before = self.hass.states.get(kelio_result_entity)
        result_before_updated = result_before.last_updated if result_before is not None else None

        if not self.hass.services.has_service("hassio", "addon_stdin"):
            raise HomeAssistantError(
                "A hassio.addon_stdin szolgáltatás nem érhető el; "
                "a Kelio add-on havi frissítése nem indítható."
            )

        self.kelio_refresh_in_progress = True
        try:
            _LOGGER.info(
                "Kelio havi előzmények frissítése indul az Útnyilvántartás gombjáról: %s",
                target_month,
            )
            await self.hass.services.async_call(
                "hassio",
                "addon_stdin",
                {
                    "addon": KELIO_ADDON_SLUG,
                    "input": f"history_month {target_month}",
                },
                blocking=True,
            )

            deadline = monotonic() + timeout_seconds
            last_progress_log: str | None = None
            while monotonic() < deadline:
                await asyncio.sleep(0.5)

                # The Kelio add-on publishes explicit progress/failure on this entity.
                result_state = self.hass.states.get(kelio_result_entity)
                if result_state is not None:
                    result_is_new = (
                        result_before_updated is None
                        or result_state.last_updated > result_before_updated
                    )
                    result_status = str(result_state.state or "")
                    result_message = str(result_state.attributes.get("message") or "")
                    if result_is_new and result_status == "history_month_failed":
                        raise HomeAssistantError(
                            "A Kelio havi előzmény lekérése hibával leállt: "
                            + (result_message or "ismeretlen Kelio hiba")
                        )
                    if (
                        result_is_new
                        and result_status == "history_month_running"
                        and result_message
                        and result_message != last_progress_log
                    ):
                        last_progress_log = result_message
                        _LOGGER.info("Kelio havi lekérés folyamatban: %s", result_message)

                source = self.hass.states.get(self.kelio_month_entity)
                if source is None or source.state in {"unknown", "unavailable"}:
                    continue

                source_month = str(source.attributes.get("month") or "")
                updated_at = source.attributes.get("updated_at")
                source_changed = (
                    updated_at is not None
                    and updated_at != before_updated_at
                )
                if not source_changed and before_last_updated is not None:
                    source_changed = source.last_updated > before_last_updated
                if before_last_updated is None:
                    source_changed = True

                if source_month == target_month and source_changed:
                    self.last_kelio_refresh_at = (
                        str(updated_at)
                        if updated_at is not None
                        else source.last_updated.isoformat()
                    )
                    self.last_kelio_refresh_month = target_month
                    _LOGGER.info(
                        "Kelio havi előzmények frissültek: %s, updated_at=%s",
                        target_month,
                        self.last_kelio_refresh_at,
                    )
                    break
            else:
                raise HomeAssistantError(
                    f"A Kelio havi előzmények nem készültek el {timeout_seconds:.0f} másodpercen belül. "
                    "A GPS havi újraszámítás nem indult el, így régi Kelio-adatból nem számolunk. "
                    "Ellenőrizd a Kelio add-on naplóját / sensor.kelio_utolso_eredmeny állapotát."
                )

            await self.async_request_refresh()
        finally:
            self.kelio_refresh_in_progress = False

    async def async_refresh_saved_pdfs(self) -> None:
        self.saved_pdfs = await self.hass.async_add_executor_job(
            _scan_saved_pdfs, self.pdf_dir
        )

    def set_pdf_result(self, *, path: str | None, url: str | None, month: str | None, error: str | None = None) -> None:
        self.last_pdf_path = path
        self.last_pdf_url = url
        self.last_pdf_month = month
        self.last_pdf_generated_at = dt_util.now().isoformat()
        self.last_pdf_error = error
        if self.data is not None:
            self.async_set_updated_data(self.data)

    def _empty(self, month: str, source_available: bool = False) -> MonthlySummary:
        return MonthlySummary(
            month=month,
            source_available=source_available,
            presence_dates=[],
            manual_dates=[],
            presence_days=0,
            eligible_days=0.0,
            ineligible_presence_days=0.0,
            eligible_legs=0,
            ineligible_legs=0,
            unknown_legs=0,
            company_distance_km=0.0,
            private_commute_km=0.0,
            reimbursement_huf=0.0,
            odometer_start_km=self.base_odometer_km,
            odometer_end_km=self.base_odometer_km,
            records=[],
            errors={},
            updated_at=dt_util.now().isoformat(),
            stored_file=None,
        )

    async def _async_update_data(self) -> MonthlySummary:
        await self.async_refresh_saved_pdfs()
        now = dt_util.now()
        current_month = now.strftime("%Y-%m")
        month = self._validate_month(self.selected_month or current_month)
        source = self.hass.states.get(self.kelio_month_entity)
        if source is None or source.state in {"unknown", "unavailable"}:
            return self._empty(month, False)

        source_month = str(source.attributes.get("month") or "")
        if source_month != month:
            # Never calculate a selected month from a different Kelio month's
            # presence_dates. The month-switch service refreshes Kelio first.
            return self._empty(month, True)

        try:
            year, month_no = (int(part) for part in month.split("-", 1))
            monthrange(year, month_no)
        except (ValueError, TypeError):
            return self._empty(current_month, True)

        raw_dates = source.attributes.get("presence_dates") or []
        presence_dates: set[str] = set()
        if isinstance(raw_dates, list):
            for value in raw_dates:
                if not isinstance(value, str):
                    continue
                try:
                    parsed = date.fromisoformat(value)
                except ValueError:
                    continue
                if parsed.strftime("%Y-%m") == month:
                    presence_dates.add(parsed.isoformat())

        # Ha az aktuális napi Kelio entitás már ON, de a havi lista még nem
        # frissült, a mai napot akkor is vegyük figyelembe.
        today_source = self.hass.states.get(self.kelio_today_entity)
        if (
            month == current_month
            and today_source is not None
            and today_source.state.strip().lower() in {"on", "true", "yes", "igen", "success", "already", "present", "jelen"}
        ):
            presence_dates.add(now.date().isoformat())

        manual_overrides = await self.hass.async_add_executor_job(
            _read_manual_overrides, self.manual_overrides_path
        )

        # A valódi Kelio adat automatikusan felülírja és törli az ugyanarra a
        # napra korábban készített kézi kiegészítést.
        pruned_manual = False
        for actual_day in list(presence_dates):
            if actual_day in manual_overrides:
                manual_overrides.pop(actual_day, None)
                pruned_manual = True
        if pruned_manual:
            await self.hass.async_add_executor_job(
                _write_manual_overrides,
                self.manual_overrides_path,
                self.daily.device_id,
                manual_overrides,
            )

        manual_dates = {
            key
            for key in manual_overrides
            if key.startswith(f"{month}-")
            and date.fromisoformat(key) <= now.date()
        }
        effective_presence_dates = set(presence_dates) | manual_dates

        last_day = monthrange(year, month_no)[1]
        if month == current_month:
            upto = now.day
        elif date(year, month_no, 1) > now.date():
            upto = 0
        else:
            upto = last_day

        records: list[dict[str, Any]] = []
        errors: dict[str, str] = {}
        eligible_legs = 0
        ineligible_legs = 0
        unknown_legs = 0
        company_distance_km = 0.0

        for day_no in range(1, upto + 1):
            day = date(year, month_no, day_no)
            day_key = day.isoformat()
            if day_key in manual_dates and day_key not in presence_dates:
                manual_record = _manual_record(day_key, manual_overrides[day_key])
                records.append(manual_record)
                eligible_legs += int(manual_record["eligible_legs"])
                ineligible_legs += int(manual_record["ineligible_legs"])
                continue

            if day_key not in effective_presence_dates:
                records.append(
                    {
                        "date": day_key,
                        "kelio_present": False,
                        "presence_source": None,
                        "manual_override": False,
                        "manual_note": None,
                        "gps_checked": False,
                        "commute_eligible": False,
                        "morning_commute_eligible": False,
                        "evening_commute_eligible": False,
                        "eligible_legs": 0,
                        "ineligible_legs": 2,
                        "unknown_legs": 0,
                        "eligible_day_equivalent": 0.0,
                        "reason": "Kelio: nincs igazolt jelenlét → egyik bejárási út sem jár (GPS nem kerül értékelésre)",
                        "company_car": None,
                    }
                )
                continue

            try:
                summary = await self.daily.async_fetch_day(day)
                leg_result = evaluate_commute_legs(True, summary)

                if leg_result["unknown_legs"] > 0:
                    whole_day_state = None
                elif leg_result["eligible_legs"] == 2:
                    whole_day_state = True
                elif leg_result["eligible_legs"] == 0:
                    whole_day_state = False
                else:
                    # Partial day: exactly one reimbursable commute leg.
                    whole_day_state = None

                records.append(
                    _daily_to_record(
                        summary,
                        True,
                        whole_day_state,
                        leg_result["reason"],
                        morning_eligible=leg_result["morning"],
                        evening_eligible=leg_result["evening"],
                        eligible_legs=leg_result["eligible_legs"],
                        ineligible_legs=leg_result["ineligible_legs"],
                        unknown_legs=leg_result["unknown_legs"],
                    )
                )

                if summary.distance_km is not None:
                    company_distance_km += float(summary.distance_km)

                eligible_legs += int(leg_result["eligible_legs"])
                ineligible_legs += int(leg_result["ineligible_legs"])
                unknown_legs += int(leg_result["unknown_legs"])
            except AlapnyomkovetesError as err:
                errors[day_key] = str(err)
                records.append(
                    {
                        "date": day_key,
                        "kelio_present": True,
                        "presence_source": "kelio",
                        "manual_override": False,
                        "manual_note": None,
                        "gps_checked": False,
                        "commute_eligible": None,
                        "morning_commute_eligible": None,
                        "evening_commute_eligible": None,
                        "eligible_legs": 0,
                        "ineligible_legs": 0,
                        "unknown_legs": 2,
                        "eligible_day_equivalent": 0.0,
                        "reason": f"Alapnyomkövetés hiba: {err}",
                        "company_car": None,
                    }
                )

        # Hard invariant: no Kelio -> zero reimbursable commute legs.
        # Recalculate all monthly leg counters from final records so stale data
        # can never inflate the reimbursement.
        corrected_eligible_legs = 0
        corrected_ineligible_legs = 0
        corrected_unknown_legs = 0

        for record in records:
            day_key = str(record.get("date") or "")
            kelio_ok = day_key in presence_dates and record.get("kelio_present") is True
            manual_ok = (
                day_key in manual_dates
                and record.get("manual_override") is True
                and record.get("kelio_present") is True
            )
            presence_ok = kelio_ok or manual_ok

            if not presence_ok:
                record["kelio_present"] = False
                record["commute_eligible"] = False
                record["morning_commute_eligible"] = False
                record["evening_commute_eligible"] = False
                record["eligible_legs"] = 0
                record["ineligible_legs"] = 2
                record["unknown_legs"] = 0
                record["eligible_day_equivalent"] = 0.0
                record["reason"] = (
                    "Kelio: nincs igazolt jelenlét → egyik bejárási út sem jár "
                    "(a céges autó helyzete ilyenkor nem számít)"
                )
                record["company_car"] = None
                record["gps_checked"] = False

            corrected_eligible_legs += int(record.get("eligible_legs") or 0)
            corrected_ineligible_legs += int(record.get("ineligible_legs") or 0)
            corrected_unknown_legs += int(record.get("unknown_legs") or 0)

        eligible_legs = corrected_eligible_legs
        ineligible_legs = corrected_ineligible_legs
        unknown_legs = corrected_unknown_legs

        # Two one-way private-car commute legs = one reimbursement day.
        # The legs do NOT have to be on the same calendar day.
        eligible_days = round(eligible_legs / 2.0, 2)
        ineligible_presence_days = round(ineligible_legs / 2.0, 2)

        private_commute_km = round(eligible_legs * self.commute_one_way_km, 3)
        reimbursement_huf = round(private_commute_km * self.reimbursement_huf_per_km, 2)
        ledger_path = Path(self.hass.config.path("utnyilvantartas", "odometer.json"))
        odometer = await self.hass.async_add_executor_job(
            update_odometer_ledger,
            ledger_path,
            month,
            self.base_odometer_km,
            private_commute_km,
        )
        odometer_start_km = float(odometer["start_km"])
        odometer_end_km = float(odometer["end_km"])

        updated_at = dt_util.now().isoformat()
        path = Path(self.hass.config.path("utnyilvantartas", f"{month}.json"))
        payload = {
            "version": 2,
            "month": month,
            "updated_at": updated_at,
            "kelio_source_entity": self.kelio_month_entity,
            "kelio_presence_dates": sorted(presence_dates),
            "manual_presence_dates": sorted(manual_dates),
            "effective_presence_dates": sorted(effective_presence_dates),
            "manual_override_file": str(self.manual_overrides_path),
            "eligibility_gate": {
                "kelio_presence_required": True,
                "gps_checked_only_after_kelio_presence": True,
                "non_kelio_day_can_never_be_eligible": True,
                "accounting_mode": "commute_legs",
                "two_eligible_legs_equal_one_day": True,
                "eligible_legs_may_be_on_different_dates": True,
            },
            "presence_days": len(effective_presence_dates),
            "eligible_days": eligible_days,
            "ineligible_presence_days": ineligible_presence_days,
            "eligible_legs": eligible_legs,
            "ineligible_legs": ineligible_legs,
            "unknown_legs": unknown_legs,
            "company_distance_km": round(company_distance_km, 3),
            "private_commute_km": private_commute_km,
            "reimbursement_huf": reimbursement_huf,
            "odometer": {
                "start_km": round(odometer_start_km, 3),
                "end_km": round(odometer_end_km, 3),
            },
            "gps_detection": {
                "home_radius_m": self.daily.home_gps_radius_m,
                "work_radius_m": self.daily.work_gps_radius_m,
                "endpoint_window_km": self.daily.endpoint_window_km,
                "overnight_home_rule": {
                    "before_workday": "previous day 18:00 -> workday 06:00",
                    "after_workday": "workday 18:00 -> next day 06:00",
                    "zone_radius_source": "actual zone.home radius",
                    "stationary_points_count": True,
                    "playback_id_minus_one_snapshot_used": True,
                    "exact_overnight_queries": True,
                    "split_at_midnight": True,
                    "ignore_points_before_18_and_after_06": True,
                    "is_only_company_car_eligibility_rule": True,
                    "daytime_route_is_diagnostic_only": True
                },
            },
            "errors": errors,
            "records": records,
        }
        await self.hass.async_add_executor_job(_write_json, path, payload)

        return MonthlySummary(
            month=month,
            source_available=True,
            presence_dates=sorted(presence_dates),
            manual_dates=sorted(manual_dates),
            presence_days=len(effective_presence_dates),
            eligible_days=eligible_days,
            ineligible_presence_days=ineligible_presence_days,
            eligible_legs=eligible_legs,
            ineligible_legs=ineligible_legs,
            unknown_legs=unknown_legs,
            company_distance_km=round(company_distance_km, 3),
            private_commute_km=private_commute_km,
            reimbursement_huf=reimbursement_huf,
            odometer_start_km=round(odometer_start_km, 3),
            odometer_end_km=round(odometer_end_km, 3),
            records=records,
            errors=errors,
            updated_at=updated_at,
            stored_file=str(path),
        )
