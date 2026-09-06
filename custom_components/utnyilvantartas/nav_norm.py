from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
import re

from aiohttp import ClientSession


NAV_NORM_URL = "https://nav.gov.hu/print/ugyfeliranytu/uzemanyag/gjnorma"


@dataclass(frozen=True)
class NavConsumptionNorm:
    fuel_key: str
    engine_cc: int
    consumption: float
    unit: str
    source_url: str
    source_label: str = "NAV"


class _TextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        value = " ".join(data.split())
        if value:
            self.parts.append(value)

    def text(self) -> str:
        return " ".join(self.parts)


def _fuel_key(fuel_type: str) -> str:
    value = (fuel_type or "").strip().lower()
    if any(x in value for x in ("gázolaj", "gazolaj", "dízel", "dizel", "diesel")):
        return "diesel"
    if "lpg" in value or "autógáz" in value or "autogaz" in value:
        return "lpg"
    if "cng" in value or "lng" in value or "földgáz" in value or "foldgaz" in value:
        return "cng"
    if any(x in value for x in ("benzin", "esz-95", "esz95", "95")):
        return "petrol"
    raise ValueError(f"Ehhez az üzemanyagtípushoz nincs automatikus NAV alapnorma-átalány: {fuel_type}")


def _embedded_base_norm(fuel_key: str, engine_cc: int) -> float:
    cc = int(engine_cc)
    if cc <= 0:
        raise ValueError("A hengerűrtartalom nincs megadva")

    if fuel_key == "petrol":
        if cc <= 1000:
            return 7.6
        if cc <= 1500:
            return 8.6
        if cc <= 2000:
            return 9.5
        if cc <= 3000:
            return 11.4
        return 13.3

    if fuel_key == "diesel":
        if cc <= 1500:
            return 5.7
        if cc <= 2000:
            return 6.7
        if cc <= 3000:
            return 7.6
        return 9.5

    petrol = _embedded_base_norm("petrol", cc)
    if fuel_key == "lpg":
        return round(petrol * 1.2, 2)
    if fuel_key == "cng":
        return round(petrol * 0.8, 2)

    raise ValueError(f"Ismeretlen üzemanyagkulcs: {fuel_key}")


def _parse_decimal(value: str) -> float:
    return float(value.replace(",", "."))


def _extract_ranges_from_nav_text(text: str, fuel_key: str) -> list[tuple[int | None, int | None, float]]:
    normalized = " ".join(text.replace("\xa0", " ").split())
    lower = normalized.lower()

    if fuel_key == "petrol":
        start_marker = "benzinüzemű gépkocsi"
        end_marker = "gázolajüzemű gépkocsi"
    elif fuel_key == "diesel":
        start_marker = "gázolajüzemű gépkocsi"
        end_marker = "autógázzal üzemelő"
    else:
        return []

    start = lower.find(start_marker)
    if start < 0:
        return []
    end = lower.find(end_marker, start + len(start_marker))
    section = normalized[start: end if end > start else len(normalized)]

    ranges: list[tuple[int | None, int | None, float]] = []

    for min_cc, max_cc, val in re.findall(
        r"(\d{3,4})\s*-\s*(\d{3,4})\s*cm3-ig\s+(\d{1,2}[,.]\d)\s*liter\s*/?\s*100\s*kilométer",
        section,
        flags=re.IGNORECASE,
    ):
        ranges.append((int(min_cc), int(max_cc), _parse_decimal(val)))

    for max_cc, val in re.findall(
        r"(?<!-)(\d{3,4})\s*cm3-ig\s+(\d{1,2}[,.]\d)\s*liter\s*/?\s*100\s*kilométer",
        section,
        flags=re.IGNORECASE,
    ):
        ranges.append((None, int(max_cc), _parse_decimal(val)))

    for min_cc, val in re.findall(
        r"(\d{3,4})\s*cm3\s*felett\s+(\d{1,2}[,.]\d)\s*liter\s*/?\s*100\s*kilométer",
        section,
        flags=re.IGNORECASE,
    ):
        ranges.append((int(min_cc), None, _parse_decimal(val)))

    return ranges


def _select_range(ranges: list[tuple[int | None, int | None, float]], engine_cc: int) -> float | None:
    cc = int(engine_cc)
    for lo, hi, value in ranges:
        if lo is not None and hi is not None and lo <= cc <= hi:
            return value
    for lo, hi, value in ranges:
        if lo is None and hi is not None and cc <= hi:
            return value
    for lo, hi, value in ranges:
        if lo is not None and hi is None and cc >= lo:
            return value
    return None


async def async_fetch_nav_consumption_norm(
    session: ClientSession,
    *,
    fuel_type: str,
    engine_cc: int,
) -> NavConsumptionNorm:
    key = _fuel_key(fuel_type)
    cc = int(engine_cc)
    if cc <= 0:
        raise ValueError(
            "A NAV fogyasztási norma automatikus számításához add meg a hengerűrtartalmat (cm³)."
        )

    if key in {"lpg", "cng"}:
        return NavConsumptionNorm(
            fuel_key=key,
            engine_cc=cc,
            consumption=_embedded_base_norm(key, cc),
            unit="l/100 km" if key == "lpg" else "Nm³/100 km",
            source_url=NAV_NORM_URL,
        )

    headers = {
        "Accept": "text/html,application/xhtml+xml",
        "User-Agent": "HomeAssistant-Utnyilvantartas/0.4.27",
    }

    try:
        async with session.get(NAV_NORM_URL, headers=headers, timeout=25) as response:
            response.raise_for_status()
            page = await response.text()

        parser = _TextParser()
        parser.feed(page)
        parsed = _select_range(_extract_ranges_from_nav_text(parser.text(), key), cc)
        if parsed is not None:
            return NavConsumptionNorm(
                fuel_key=key,
                engine_cc=cc,
                consumption=parsed,
                unit="l/100 km",
                source_url=NAV_NORM_URL,
            )
    except Exception:
        pass

    # Robust fallback: same NAV-published bands embedded locally.
    return NavConsumptionNorm(
        fuel_key=key,
        engine_cc=cc,
        consumption=_embedded_base_norm(key, cc),
        unit="l/100 km",
        source_url=NAV_NORM_URL,
    )
