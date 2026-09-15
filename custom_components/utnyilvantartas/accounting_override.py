from __future__ import annotations

import json
from copy import deepcopy
from datetime import date
from pathlib import Path
from typing import Any

from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util

from .coordinator import MonthlySummary, UtnyMonthlyCoordinator
from .odometer import update_odometer_ledger


DEFAULT_ACCOUNTING_NOTE = "Kézi elszámolási felülbírálás"


def _read_accounting_overrides(path: Path) -> dict[str, dict[str, Any]]:
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
            "morning_eligible": bool(value.get("morning_eligible", False)),
            "evening_eligible": bool(value.get("evening_eligible", False)),
            "note": str(value.get("note") or "").strip(),
            "updated_at": str(value.get("updated_at") or ""),
        }
    return result


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    tmp.replace(path)


def _write_accounting_overrides(
    path: Path,
    device_id: int,
    entries: dict[str, dict[str, Any]],
) -> None:
    _write_json_atomic(
        path,
        {
            "version": 1,
            "device_id": int(device_id),
            "updated_at": dt_util.now().isoformat(),
            "entries": dict(sorted(entries.items())),
        },
    )


def _whole_day_state(morning: bool, evening: bool) -> bool | None:
    legs = int(morning) + int(evening)
    if legs == 2:
        return True
    if legs == 0:
        return False
    return None


def _apply_accounting_override(
    record: dict[str, Any],
    override: dict[str, Any],
) -> dict[str, Any]:
    """Apply only the accounting result; preserve Kelio/GPS evidence unchanged."""
    result = deepcopy(record)

    automatic_morning = result.get("morning_commute_eligible")
    automatic_evening = result.get("evening_commute_eligible")
    automatic_reason = str(result.get("reason") or "")

    morning = bool(override.get("morning_eligible", False))
    evening = bool(override.get("evening_eligible", False))
    eligible_legs = int(morning) + int(evening)
    ineligible_legs = 2 - eligible_legs
    note = str(override.get("note") or "").strip() or DEFAULT_ACCOUNTING_NOTE

    result.update(
        {
            "accounting_override": True,
            "accounting_override_note": note,
            "accounting_override_updated_at": str(override.get("updated_at") or ""),
            "automatic_commute_eligible": result.get("commute_eligible"),
            "automatic_morning_commute_eligible": automatic_morning,
            "automatic_evening_commute_eligible": automatic_evening,
            "automatic_eligible_legs": int(result.get("eligible_legs") or 0),
            "automatic_ineligible_legs": int(result.get("ineligible_legs") or 0),
            "automatic_unknown_legs": int(result.get("unknown_legs") or 0),
            "automatic_reason": automatic_reason,
            "commute_eligible": _whole_day_state(morning, evening),
            "morning_commute_eligible": morning,
            "evening_commute_eligible": evening,
            "eligible_legs": eligible_legs,
            "ineligible_legs": ineligible_legs,
            "unknown_legs": 0,
            "eligible_day_equivalent": eligible_legs / 2.0,
            "reason": (
                f"Kézi elszámolási felülbírálás: {note}"
                + (f" · Automatikus döntés: {automatic_reason}" if automatic_reason else "")
            ),
        }
    )
    return result


def _rewrite_month_payload(
    path: Path,
    *,
    records: list[dict[str, Any]],
    override_dates: list[str],
    override_file: Path,
    eligible_days: float,
    ineligible_presence_days: float,
    eligible_legs: int,
    ineligible_legs: int,
    unknown_legs: int,
    private_commute_km: float,
    reimbursement_huf: float,
    odometer_start_km: float,
    odometer_end_km: float,
    updated_at: str,
) -> None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}

    payload.update(
        {
            "updated_at": updated_at,
            "accounting_override_dates": override_dates,
            "accounting_override_file": str(override_file),
            "eligible_days": eligible_days,
            "ineligible_presence_days": ineligible_presence_days,
            "eligible_legs": eligible_legs,
            "ineligible_legs": ineligible_legs,
            "unknown_legs": unknown_legs,
            "private_commute_km": private_commute_km,
            "reimbursement_huf": reimbursement_huf,
            "odometer": {
                "start_km": round(odometer_start_km, 3),
                "end_km": round(odometer_end_km, 3),
            },
            "records": records,
        }
    )
    gate = payload.get("eligibility_gate")
    if isinstance(gate, dict):
        gate["manual_accounting_override_supported"] = True
        gate["manual_override_never_changes_kelio_or_gps_evidence"] = True
    _write_json_atomic(path, payload)


class UtnyMonthlyAccountingCoordinator(UtnyMonthlyCoordinator):
    """Monthly coordinator with a persistent, per-day accounting override layer."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.accounting_overrides_path = Path(
            self.hass.config.path(
                "utnyilvantartas",
                f"accounting_overrides_{self.daily.device_id}.json",
            )
        )

    async def async_set_accounting_override(
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
            raise HomeAssistantError(
                "Az elszámolási felülbírálás dátuma YYYY-MM-DD formátumú legyen."
            ) from err

        today = dt_util.now().date()
        if target > today:
            raise HomeAssistantError("Jövőbeli nap nem szerkeszthető.")

        target_month = target.strftime("%Y-%m")
        if target_month != self.selected_month:
            raise HomeAssistantError(
                f"A szerkesztett nap a megnyitott hónaphoz tartozzon ({self.selected_month})."
            )

        data = self.data
        if data is None or data.month != target_month:
            await self.async_refresh()
            data = self.data

        record = next(
            (item for item in (data.records if data else []) if item.get("date") == target.isoformat()),
            None,
        )
        if not record or record.get("kelio_present") is not True:
            raise HomeAssistantError(
                "Csak igazolt jelenléti nap elszámolása bírálható felül. "
                "A Kelio/GPS eredeti adatot a szerkesztés nem módosítja."
            )

        entries = await self.hass.async_add_executor_job(
            _read_accounting_overrides,
            self.accounting_overrides_path,
        )
        entries[target.isoformat()] = {
            "morning_eligible": bool(morning_eligible),
            "evening_eligible": bool(evening_eligible),
            "note": str(note or "").strip(),
            "updated_at": dt_util.now().isoformat(),
        }
        await self.hass.async_add_executor_job(
            _write_accounting_overrides,
            self.accounting_overrides_path,
            self.daily.device_id,
            entries,
        )
        await self.async_refresh()

    async def async_remove_accounting_override(self, value: str) -> None:
        try:
            target = date.fromisoformat(str(value or "").strip())
        except ValueError as err:
            raise HomeAssistantError(
                "Az elszámolási felülbírálás dátuma YYYY-MM-DD formátumú legyen."
            ) from err

        entries = await self.hass.async_add_executor_job(
            _read_accounting_overrides,
            self.accounting_overrides_path,
        )
        entries.pop(target.isoformat(), None)
        await self.hass.async_add_executor_job(
            _write_accounting_overrides,
            self.accounting_overrides_path,
            self.daily.device_id,
            entries,
        )
        await self.async_refresh()

    async def _async_update_data(self) -> MonthlySummary:
        # First run the proven Kelio + GPS calculation unchanged.
        summary = await super()._async_update_data()
        if not summary.source_available or not summary.records:
            return summary

        overrides = await self.hass.async_add_executor_job(
            _read_accounting_overrides,
            self.accounting_overrides_path,
        )
        month_prefix = f"{summary.month}-"
        active = {
            key: value
            for key, value in overrides.items()
            if key.startswith(month_prefix)
        }
        if not active:
            return summary

        records = [deepcopy(record) for record in summary.records]
        applied_dates: list[str] = []
        for index, record in enumerate(records):
            day_key = str(record.get("date") or "")
            override = active.get(day_key)
            # Never let a manual accounting entry manufacture Kelio presence.
            if override is None or record.get("kelio_present") is not True:
                continue
            records[index] = _apply_accounting_override(record, override)
            applied_dates.append(day_key)

        if not applied_dates:
            return summary

        eligible_legs = sum(int(record.get("eligible_legs") or 0) for record in records)
        ineligible_legs = sum(int(record.get("ineligible_legs") or 0) for record in records)
        unknown_legs = sum(int(record.get("unknown_legs") or 0) for record in records)
        eligible_days = round(eligible_legs / 2.0, 2)
        ineligible_presence_days = round(ineligible_legs / 2.0, 2)
        private_commute_km = round(eligible_legs * self.commute_one_way_km, 3)
        reimbursement_huf = round(private_commute_km * self.reimbursement_huf_per_km, 2)

        ledger_path = Path(self.hass.config.path("utnyilvantartas", "odometer.json"))
        odometer = await self.hass.async_add_executor_job(
            update_odometer_ledger,
            ledger_path,
            summary.month,
            self.base_odometer_km,
            private_commute_km,
        )

        summary.records = records
        summary.eligible_legs = eligible_legs
        summary.ineligible_legs = ineligible_legs
        summary.unknown_legs = unknown_legs
        summary.eligible_days = eligible_days
        summary.ineligible_presence_days = ineligible_presence_days
        summary.private_commute_km = private_commute_km
        summary.reimbursement_huf = reimbursement_huf
        summary.odometer_start_km = round(float(odometer["start_km"]), 3)
        summary.odometer_end_km = round(float(odometer["end_km"]), 3)
        summary.updated_at = dt_util.now().isoformat()

        if summary.stored_file:
            await self.hass.async_add_executor_job(
                _rewrite_month_payload,
                Path(summary.stored_file),
                records=records,
                override_dates=sorted(applied_dates),
                override_file=self.accounting_overrides_path,
                eligible_days=eligible_days,
                ineligible_presence_days=ineligible_presence_days,
                eligible_legs=eligible_legs,
                ineligible_legs=ineligible_legs,
                unknown_legs=unknown_legs,
                private_commute_km=private_commute_km,
                reimbursement_huf=reimbursement_huf,
                odometer_start_km=summary.odometer_start_km,
                odometer_end_km=summary.odometer_end_km,
                updated_at=summary.updated_at,
            )

        return summary
