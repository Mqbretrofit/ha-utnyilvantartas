from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
import re
from typing import Any

from aiohttp import ClientSession


NAV_YEAR_URL = (
    "https://nav.gov.hu/print/ugyfeliranytu/uzemanyag/"
    "{year}-ban-alkalmazhato-uzemanyagarak"
)

_MONTHS_HU = {
    1: "január",
    2: "február",
    3: "március",
    4: "április",
    5: "május",
    6: "június",
    7: "július",
    8: "augusztus",
    9: "szeptember",
    10: "október",
    11: "november",
    12: "december",
}


@dataclass(frozen=True)
class NavFuelPrice:
    year: int
    month: int
    fuel_key: str
    price_huf: float
    unit: str
    source_url: str

    @property
    def source_label(self) -> str:
        return "NAV"


class _TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[str]] = []
        self._in_tr = False
        self._in_cell = False
        self._cell_parts: list[str] = []
        self._row: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag == "tr":
            self._in_tr = True
            self._row = []
        elif self._in_tr and tag in {"td", "th"}:
            self._in_cell = True
            self._cell_parts = []
        elif self._in_cell and tag == "br":
            self._cell_parts.append(" ")

    def handle_data(self, data: str) -> None:
        if self._in_cell:
            self._cell_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if self._in_cell and tag in {"td", "th"}:
            value = " ".join("".join(self._cell_parts).split())
            self._row.append(value)
            self._in_cell = False
            self._cell_parts = []
        elif self._in_tr and tag == "tr":
            if self._row:
                self.rows.append(self._row)
            self._row = []
            self._in_tr = False


def _fuel_key(fuel_type: str) -> str:
    value = (fuel_type or "").strip().lower()
    if any(x in value for x in ("gázolaj", "gazolaj", "dízel", "dizel", "diesel")):
        return "diesel"
    if "cng" in value:
        return "cng"
    if "lpg" in value or "autógáz" in value or "autogaz" in value:
        return "lpg"
    if "keverék" in value or "keverek" in value:
        return "mix"
    if any(x in value for x in ("benzin", "esz-95", "esz95", "95")):
        return "petrol"
    raise ValueError(f"Ismeretlen NAV üzemanyagtípus: {fuel_type}")


def _number(cell: str) -> float | None:
    value = (cell or "").replace("\xa0", " ").strip()
    if not value or value == "-":
        return None
    match = re.search(r"(\d[\d\s]*)", value)
    if not match:
        return None
    return float(match.group(1).replace(" ", ""))


def parse_nav_fuel_price_html(
    html_text: str,
    *,
    year: int,
    month: int,
    fuel_type: str,
    source_url: str,
) -> NavFuelPrice:
    parser = _TableParser()
    parser.feed(html_text)

    month_name = _MONTHS_HU[month]
    target: list[str] | None = None
    for row in parser.rows:
        if not row:
            continue
        first = row[0].strip().lower()
        if first == month_name or first.startswith(month_name + " "):
            target = row
            break

    if target is None:
        raise ValueError(f"A NAV táblában nem található: {year}-{month:02d}")

    key = _fuel_key(fuel_type)

    # 2026-tól a NAV egyes hónapoknál külön védett/piaci oszlopokat közöl.
    # A költségelszámoláshoz a piaci árszabás oszlopát használjuk.
    if len(target) >= 8:
        index_map = {
            "petrol": 2,   # ESZ-95 piaci
            "diesel": 4,   # Gázolaj piaci
            "mix": 5,
            "lpg": 6,
            "cng": 7,
        }
    elif len(target) >= 6:
        index_map = {
            "petrol": 1,
            "diesel": 2,
            "mix": 3,
            "lpg": 4,
            "cng": 5,
        }
    else:
        raise ValueError(f"Ismeretlen NAV üzemanyagár-tábla formátum: {target!r}")

    idx = index_map[key]
    if idx >= len(target):
        raise ValueError(f"Hiányzó NAV ár oszlop: {key}")

    price = _number(target[idx])
    if price is None:
        raise ValueError(f"Nincs NAV ár ehhez: {year}-{month:02d}, {fuel_type}")

    return NavFuelPrice(
        year=year,
        month=month,
        fuel_key=key,
        price_huf=price,
        unit="Ft/kg" if key == "cng" else "Ft/l",
        source_url=source_url,
    )


async def async_fetch_nav_fuel_price(
    session: ClientSession,
    *,
    month_value: str,
    fuel_type: str,
) -> NavFuelPrice:
    year_s, month_s = month_value.split("-", 1)
    year = int(year_s)
    month = int(month_s)
    url = NAV_YEAR_URL.format(year=year)

    headers = {
        "Accept": "text/html,application/xhtml+xml",
        "User-Agent": "HomeAssistant-Utnyilvantartas/0.4.26",
    }
    async with session.get(url, headers=headers, timeout=25) as response:
        response.raise_for_status()
        text = await response.text()

    return parse_nav_fuel_price_html(
        text,
        year=year,
        month=month,
        fuel_type=fuel_type,
        source_url=url,
    )
