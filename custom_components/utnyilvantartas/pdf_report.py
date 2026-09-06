from __future__ import annotations

import calendar
from datetime import datetime
from pathlib import Path
from typing import Any

import reportlab
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas


def _register_unicode_font() -> tuple[str, str]:
    # Home Assistant OS/container images use different font locations depending
    # on architecture. Try the common Debian and Alpine paths first. No font
    # file is bundled with the integration.
    candidates = [
        ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
        ("/usr/share/fonts/TTF/DejaVuSans.ttf", "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf"),
        ("/usr/share/fonts/dejavu/DejaVuSans.ttf", "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf"),
        ("/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf", "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf"),
        ("/usr/share/fonts/liberation/LiberationSans-Regular.ttf", "/usr/share/fonts/liberation/LiberationSans-Bold.ttf"),
        ("/usr/share/fonts/noto/NotoSans-Regular.ttf", "/usr/share/fonts/noto/NotoSans-Bold.ttf"),
        ("/usr/share/fonts/truetype/freefont/FreeSans.ttf", "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf"),
    ]
    # ReportLab's own Vera is still useful as a better fallback for most
    # Central-European characters; ő/ű are drawn manually below if absent.
    rl_fonts = Path(reportlab.__file__).parent / "fonts"
    candidates.append((str(rl_fonts / "Vera.ttf"), str(rl_fonts / "VeraBd.ttf")))
    for regular, bold in candidates:
        if Path(regular).exists() and Path(bold).exists():
            try:
                pdfmetrics.registerFont(TTFont("UtnySans", regular))
                pdfmetrics.registerFont(TTFont("UtnySansBold", bold))
                return "UtnySans", "UtnySansBold"
            except Exception:
                pass
    return "Helvetica", "Helvetica-Bold"


_SPECIAL_HU = {"ő": "o", "Ő": "O", "ű": "u", "Ű": "U"}


def _text_width(text: str, font: str, size: float) -> float:
    safe = "".join(_SPECIAL_HU.get(ch, ch) for ch in str(text or ""))
    return pdfmetrics.stringWidth(safe, font, size)


def _draw_text(c: canvas.Canvas, text: str, x: float, y: float, font: str, size: float,
               align: str = "left") -> None:
    """Draw Hungarian text even on HA images without a font containing ő/ű.

    ReportLab's base fonts do not contain U+0150/U+0151/U+0170/U+0171. When a
    suitable system TTF is unavailable, those four glyphs would otherwise show
    as an empty box. We draw the base o/u glyph plus the double acute accents.
    """
    text = str(text or "")
    total = _text_width(text, font, size)
    cursor = x
    if align == "right":
        cursor -= total
    elif align == "center":
        cursor -= total / 2.0

    c.setFont(font, size)
    buffer = ""

    def flush() -> None:
        nonlocal cursor, buffer
        if not buffer:
            return
        c.drawString(cursor, y, buffer)
        cursor += pdfmetrics.stringWidth(buffer, font, size)
        buffer = ""

    for ch in text:
        base = _SPECIAL_HU.get(ch)
        if base is None:
            buffer += ch
            continue
        flush()
        c.drawString(cursor, y, base)
        w = pdfmetrics.stringWidth(base, font, size)
        # Two thin acute strokes above the base letter. The placement scales
        # with the actual font size and works for both lower and upper case.
        accent_y = y + size * (0.78 if ch.islower() else 0.83)
        stroke = max(0.8, size * 0.13)
        gap = max(1.0, size * 0.18)
        left = cursor + w * 0.30
        c.saveState()
        c.setLineWidth(max(0.28, size * 0.035))
        c.line(left, accent_y, left + stroke * 0.55, accent_y + stroke)
        c.line(left + gap, accent_y, left + gap + stroke * 0.55, accent_y + stroke)
        c.restoreState()
        cursor += w
    flush()


def _num(value: Any, digits: int = 0) -> str:
    try:
        number = float(value)
    except Exception:
        number = 0.0
    if digits == 0:
        return f"{int(round(number)):,}".replace(",", " ")
    return f"{number:,.{digits}f}".replace(",", "X").replace(".", ",").replace("X", " ")


def _period(month: str) -> tuple[str, str, str]:
    try:
        year, mon = [int(x) for x in month.split("-", 1)]
        last = calendar.monthrange(year, mon)[1]
        return f"{year:04d}.{mon:02d}.01.", f"{year:04d}.{mon:02d}.{last:02d}.", f"{year:04d}.{mon:02d}."
    except Exception:
        return month, month, month


def _date_hu(value: str) -> str:
    try:
        dt = datetime.strptime(value[:10], "%Y-%m-%d")
        return dt.strftime("%Y. %m. %d.")
    except Exception:
        return value


def _fit_text(c: canvas.Canvas, text: str, font: str, size: float, max_width: float, min_size: float = 5.5) -> float:
    current = size
    while current > min_size and _text_width(text, font, current) > max_width:
        current -= 0.2
    return max(current, min_size)


def _ellipsize(text: str, font: str, size: float, max_width: float) -> str:
    text = str(text or "")
    if _text_width(text, font, size) <= max_width:
        return text
    suffix = "..."
    if _text_width(suffix, font, size) > max_width:
        return ""
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        candidate = text[:mid].rstrip() + suffix
        if _text_width(candidate, font, size) <= max_width:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo].rstrip() + suffix


def _draw_fit(c: canvas.Canvas, text: str, x: float, y: float, max_width: float, font: str, size: float,
              align: str = "left", min_size: float = 5.5) -> None:
    text = str(text or "")
    fs = _fit_text(c, text, font, size, max_width, min_size)
    safe_text = _ellipsize(text, font, fs, max_width)
    _draw_text(c, safe_text, x, y, font, fs, align)


def _build_trip_rows(monthly: dict[str, Any], meta: dict[str, Any]) -> list[dict[str, Any]]:
    one_way = float(meta.get("commute_one_way_km") or 0.0)
    consumption = float(meta.get("fuel_consumption_l_100km") or 0.0)
    fuel_price = float(meta.get("fuel_price_huf_l") or 0.0)
    home_label = str(meta.get("home_label") or "Lakás")
    home_address = str(meta.get("home_address") or "")
    company = str(meta.get("company_name") or "")
    company_address = str(meta.get("company_address") or "")

    # These rows are private Home <-> Work commuting rows.
    # Fuel reimbursement belongs only to company/business trips, therefore
    # private rows always carry 0 liters / 0 Ft even though the NAV price and
    # consumption norm remain visible in the report header.
    liters = 0.0
    fuel_cost = 0.0
    rows: list[dict[str, Any]] = []
    for rec in monthly.get("records") or []:
        if not rec.get("kelio_present"):
            continue

        date = str(rec.get("date") or "")
        common = {
            "date": _date_hu(date),
            "distance": one_way,
            "liters": liters,
            "fuel_cost": fuel_cost,
            "fuel_price": fuel_price,
        }

        # Leg-based accounting:
        # morning Home -> Work and evening Work -> Home are independent.
        if rec.get("morning_commute_eligible") is True:
            rows.append({
                **common,
                "from_name": home_label,
                "from_address": home_address,
                "to_name": company,
                "to_address": company_address,
            })

        if rec.get("evening_commute_eligible") is True:
            rows.append({
                **common,
                "from_name": company,
                "from_address": company_address,
                "to_name": home_label,
                "to_address": home_address,
            })
    return rows


def _split_pages(rows: list[dict[str, Any]]) -> list[tuple[list[dict[str, Any]], bool]]:
    """Fill pages from the first page onward; summary is only on the last page.

    The old algorithm intentionally reserved 11 rows for the last page, which
    could leave the first page almost empty (e.g. 4 rows + 11 rows). Here the
    first and continuation pages are filled first, then the final page carries
    the remaining rows and the summary.
    """
    if not rows:
        return [([], True)]

    # 17 rows keeps a safe gap above the footer even on continuation pages.
    full_capacity = 17
    summary_capacity = 17
    if len(rows) <= summary_capacity:
        return [(rows, True)]

    pages: list[tuple[list[dict[str, Any]], bool]] = []
    remaining = list(rows)
    while len(remaining) > summary_capacity:
        pages.append((remaining[:full_capacity], False))
        remaining = remaining[full_capacity:]
    pages.append((remaining, True))
    return pages


def _draw_header(c: canvas.Canvas, font: str, bold: str, monthly: dict[str, Any], meta: dict[str, Any],
                 start_odometer: float, end_odometer: float) -> None:
    width, height = A4
    company = str(meta.get("company_name") or "")
    company_address = str(meta.get("company_address") or "")
    tax_number = str(meta.get("tax_number") or "")
    employee = str(meta.get("employee_name") or "")
    plate = str(meta.get("private_vehicle") or "")
    vehicle_type = str(meta.get("vehicle_type") or "")
    fuel_type = str(meta.get("fuel_type") or "")
    consumption = float(meta.get("fuel_consumption_l_100km") or 0.0)
    nature = str(meta.get("trip_nature") or "csak magán utak")
    start_date, end_date, _ = _period(str(monthly.get("month") or ""))

    # Company block, top-left.
    x = 6 * mm
    y = height - 20 * mm
    c.setFillColorRGB(0, 0, 0)
    _draw_fit(c, company, x, y, 92 * mm, font, 9.5, min_size=7.0)
    _draw_fit(c, company_address, x, y - 5.2 * mm, 92 * mm, font, 9.5, min_size=6.8)
    _draw_fit(c, f"Adószám: {tax_number}", x, y - 10.4 * mm, 92 * mm, font, 9.5, min_size=7.0)

    # Main title, date range, nature and driver.
    title_x = 145 * mm
    _draw_fit(c, "ÚTNYILVÁNTARTÁS", title_x, height - 18.5 * mm, 105 * mm, bold, 17.5, "center", 13.0)
    _draw_fit(c, f"{start_date} - {end_date}", title_x, height - 27.0 * mm, 105 * mm, bold, 11.5, "center", 8.5)
    _draw_fit(c, f"Jelleg: {nature}", title_x, height - 34.2 * mm, 105 * mm, bold, 8.8, "center", 6.8)
    _draw_fit(c, f"Sofőr: {employee}", title_x, height - 39.2 * mm, 105 * mm, bold, 8.8, "center", 6.8)

    # Vehicle block.
    label_x = 62 * mm
    y0 = height - 64 * mm
    _draw_text(c, "Rendszám:", label_x, y0, font, 9.2, "right")
    _draw_fit(c, plate, label_x + 2 * mm, y0, 68 * mm, bold, 9.4, min_size=7.0)
    _draw_text(c, "Típus:", label_x, y0 - 6.4 * mm, font, 9.2, "right")
    engine_cc = int(meta.get("engine_cc") or 0)
    vehicle_type_text = vehicle_type
    if engine_cc > 0:
        vehicle_type_text = f"{vehicle_type} · {engine_cc} cm³"
    _draw_fit(c, vehicle_type_text, label_x + 2 * mm, y0 - 6.4 * mm, 68 * mm, bold, 9.4, min_size=6.6)
    _draw_text(c, "Üzemanyag:", label_x, y0 - 12.8 * mm, font, 9.2, "right")
    _draw_fit(c, fuel_type, label_x + 2 * mm, y0 - 12.8 * mm, 68 * mm, bold, 9.4, min_size=6.8)
    _draw_text(c, "Üa. fogyasztás:", label_x, y0 - 19.2 * mm, font, 9.2, "right")
    consumption_text = f"{_num(consumption, 1) if consumption % 1 else _num(consumption)} liter / 100km"
    consumption_source = str(meta.get("fuel_consumption_source") or "")
    if consumption_source.startswith("NAV"):
        consumption_text += " (NAV)"
    _draw_fit(c, consumption_text, label_x + 2 * mm, y0 - 19.2 * mm, 68 * mm, bold, 9.4, min_size=6.4)

    fuel_price = float(meta.get("fuel_price_huf_l") or 0.0)
    fuel_price_source = str(meta.get("fuel_price_source") or "")
    if fuel_price > 0:
        source_suffix = " (NAV)" if fuel_price_source == "NAV" else ""
        _draw_text(c, "Üa. ár:", label_x, y0 - 25.2 * mm, font, 8.2, "right")
        _draw_fit(c, f"{_num(fuel_price)} Ft/l{source_suffix}", label_x + 2 * mm, y0 - 25.2 * mm, 68 * mm, bold, 8.4, min_size=6.4)

    # Odometer block.
    odo_x = 179 * mm
    _draw_text(c, "Kezdő óraállás:", odo_x, y0, font, 9.5, "right")
    _draw_text(c, f"{_num(start_odometer)} km", odo_x, y0 - 6.2 * mm, bold, 10.5, "right")
    _draw_text(c, "Befejező óraállás:", odo_x, y0 - 13.0 * mm, font, 9.5, "right")
    _draw_text(c, f"{_num(end_odometer)} km", odo_x, y0 - 19.2 * mm, bold, 10.5, "right")


def _draw_table(c: canvas.Canvas, font: str, bold: str, rows: list[dict[str, Any]], *, show_summary: bool = False) -> float:
    width, height = A4
    x0 = 4 * mm
    y_top = height - 95 * mm
    widths = [28 * mm, 68 * mm, 58 * mm, 23 * mm, 25 * mm]
    xs = [x0]
    for w in widths:
        xs.append(xs[-1] + w)
    header_h = 6 * mm
    # On the last page we compact rows as needed so a normal work month can
    # remain on one page together with the summary. Non-summary pages keep the
    # more spacious AXEL-like row height.
    count = len(rows)
    if show_summary and count > 14:
        row_h = 6.6 * mm
    elif show_summary and count > 11:
        row_h = 8.2 * mm
    else:
        row_h = 10.2 * mm
    dense = row_h < 9 * mm

    headers = ["Dátum:", "Honnan:", "Hova:", "Távolság:", "Üzemanyag:"]
    for i, h in enumerate(headers):
        if i in (3, 4):
            _draw_text(c, h, (xs[i] + xs[i + 1]) / 2, y_top + 1.0 * mm, bold, 7.8, "center")
        else:
            _draw_text(c, h, xs[i] + 1.0 * mm, y_top + 1.0 * mm, bold, 7.8)

    table_top = y_top - 0.8 * mm
    table_bottom = table_top - len(rows) * row_h
    c.setLineWidth(0.35)
    # Vertical borders.
    for x in xs:
        c.line(x, table_top, x, table_bottom)
    # Horizontal borders.
    c.line(x0, table_top, xs[-1], table_top)
    for idx in range(1, len(rows) + 1):
        y = table_top - idx * row_h
        c.line(x0, y, xs[-1], y)

    for idx, row in enumerate(rows):
        y1 = table_top - idx * row_h
        base = y1 - (2.8 * mm if dense else 3.5 * mm)
        # Date
        _draw_fit(c, row["date"], xs[0] + 1 * mm, base, widths[0] - 2 * mm, font, 6.2 if dense else 7.0)
        # From
        _draw_fit(c, row["from_name"], xs[1] + 1.2 * mm, base, widths[1] - 2.4 * mm, font, 6.2 if dense else 7.1)
        _draw_fit(c, row["from_address"], xs[1] + 1.2 * mm, base - (2.6 * mm if dense else 3.3 * mm), widths[1] - 2.4 * mm, font, 4.9 if dense else 5.7)
        # To
        _draw_fit(c, row["to_name"], xs[2] + 1.2 * mm, base, widths[2] - 2.4 * mm, font, 6.2 if dense else 7.1)
        _draw_fit(c, row["to_address"], xs[2] + 1.2 * mm, base - (2.6 * mm if dense else 3.3 * mm), widths[2] - 2.4 * mm, font, 4.9 if dense else 5.7)
        # Distance
        distance = float(row.get("distance") or 0.0)
        distance_text = f"{_num(distance, 1) if distance % 1 else _num(distance)} km"
        _draw_fit(c, distance_text, xs[4] - 1.2 * mm, base, widths[3] - 2.4 * mm, font, 6.4 if dense else 7.0, "right", 5.4)
        _draw_fit(c, "magán", xs[4] - 1.2 * mm, base - (2.6 * mm if dense else 3.4 * mm), widths[3] - 2.4 * mm, font, 6.2 if dense else 7.0, "right", 5.4)
        # Fuel
        liters = float(row.get("liters") or 0.0)
        fuel_cost = float(row.get("fuel_cost") or 0.0)
        fuel_price = float(row.get("fuel_price") or 0.0)
        liters_text = f"{_num(liters, 2).rstrip('0').rstrip(',') if liters else '0'} liter"
        _draw_fit(c, liters_text, xs[5] - 1.2 * mm, base, widths[4] - 2.4 * mm, font, 6.4 if dense else 7.0, "right", 5.4)
        price_text = f"({_num(fuel_price)} Ft/l) {_num(fuel_cost)} Ft" if fuel_price > 0 else f"{_num(fuel_cost)} Ft"
        _draw_fit(c, price_text, xs[5] - 1.2 * mm, base - (2.6 * mm if dense else 3.4 * mm), widths[4] - 2.4 * mm, font, 5.8 if dense else 6.2, align="right", min_size=4.8)

    return table_bottom


def _draw_summary(c: canvas.Canvas, font: str, bold: str, table_bottom: float, private_km: float,
                  fuel_liters: float, fuel_cost: float, reimbursement: float, rate: float,
                  *, compact: bool = False) -> None:
    x_left = 5 * mm
    x_right = 205 * mm
    title_y = table_bottom - (4.2 * mm if compact else 10 * mm)
    _draw_text(c, "Összesítés:", 105 * mm, title_y, bold, 11.8 if compact else 13.5, "center")
    c.setLineWidth(0.35)
    c.line(x_left, title_y - (2.0 * mm if compact else 3.0 * mm), x_right, title_y - (2.0 * mm if compact else 3.0 * mm))

    head_y = title_y - (7.0 * mm if compact else 12 * mm)
    private_x = 127 * mm
    company_x = 174 * mm
    _draw_text(c, "Magán utak", private_x, head_y, font, 9.5, "center")
    _draw_text(c, "Céges utak", company_x, head_y, font, 9.5, "center")

    labels = [
        "Távolság:",
        "Üzemanyag fogyasztás:",
        "Üzemanyag költség:",
        f"Átalány ({_num(rate)} Ft/km):",
        "Összes költség:",
    ]
    private_values = [
        f"{_num(private_km)} km",
        "0 liter",
        "0 Ft",
        f"{_num(reimbursement)} Ft",
        f"{_num(reimbursement)} Ft",
    ]
    # Company-trip fuel will be shown/calculated only when company-trip rows
    # are included in the report. The current report is 'csak magán utak'.
    company_values = ["0 km", "0 liter", "0 Ft", "0 Ft", "0 Ft"]

    label_x = 90 * mm
    y = head_y - (5.5 * mm if compact else 9 * mm)
    for i, label in enumerate(labels):
        _draw_text(c, label, label_x, y, font, 9.0, "right")
        _draw_text(c, private_values[i], private_x + 7 * mm, y, bold, 9.2, "right")
        _draw_text(c, company_values[i], company_x + 7 * mm, y, bold, 9.2, "right")
        y -= (4.5 * mm if compact else 6.3 * mm)

    sig_y = y - (7.0 * mm if compact else 15 * mm)
    c.setLineWidth(0.25)
    c.setDash(1.0, 1.5)
    c.line(127 * mm, sig_y, 179 * mm, sig_y)
    c.setDash()
    _draw_text(c, "Aláírás", 153 * mm, sig_y - 5 * mm, font, 8.3, "center")


def _draw_footer(c: canvas.Canvas, font: str, page_no: int, total_pages: int) -> None:
    c.setLineWidth(0.35)
    c.line(5 * mm, 13 * mm, 205 * mm, 13 * mm)
    _draw_text(c, "nyomtatvány a Home Assistant Útnyilvántartás integrációval készült.", 5 * mm, 8 * mm, font, 5.8)
    _draw_text(c, f"Oldalszám: {page_no}/{total_pages}", 205 * mm, 7.5 * mm, font, 8.5, "right")


def generate_monthly_pdf(path: str | Path, monthly: dict[str, Any], meta: dict[str, Any]) -> dict[str, Any]:
    """Generate an AXEL-PRO-like monthly private-trip log, populated from eligible Kelio/GPS days."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    font, bold = _register_unicode_font()

    rows = _build_trip_rows(monthly, meta)
    pages = _split_pages(rows)
    one_way = float(meta.get("commute_one_way_km") or 0.0)
    rate = float(meta.get("reimbursement_huf_per_km") or 0.0)
    consumption = float(meta.get("fuel_consumption_l_100km") or 0.0)
    fuel_price = float(meta.get("fuel_price_huf_l") or 0.0)
    start_odometer = float(meta.get("start_odometer_km") or 0.0)
    private_km = sum(float(r.get("distance") or 0.0) for r in rows)
    explicit_end = meta.get("end_odometer_km")
    end_odometer = float(explicit_end) if explicit_end is not None else start_odometer + private_km
    # Fuel cost is not reimbursed for private commuting. It belongs only to
    # company/business trips. This PDF currently lists only private commute rows.
    fuel_liters = 0.0
    fuel_cost = 0.0
    reimbursement = private_km * rate if rate > 0 else 0.0

    c = canvas.Canvas(str(path), pagesize=A4)
    c.setTitle(f"Útnyilvántartás {monthly.get('month') or ''}")
    c.setAuthor(str(meta.get("employee_name") or ""))

    for page_no, (page_rows, show_summary) in enumerate(pages, start=1):
        _draw_header(c, font, bold, monthly, meta, start_odometer, end_odometer)
        table_bottom = _draw_table(c, font, bold, page_rows, show_summary=show_summary)
        if show_summary:
            _draw_summary(c, font, bold, table_bottom, private_km, fuel_liters, fuel_cost, reimbursement, rate, compact=len(page_rows) > 11)
        _draw_footer(c, font, page_no, len(pages))
        c.showPage()
    c.save()

    return {
        "file": str(path),
        "month": str(monthly.get("month") or ""),
        "eligible_days": float(monthly.get("eligible_days") or 0),
        "eligible_legs": int(monthly.get("eligible_legs") or 0),
        "private_commute_km": round(private_km, 2),
        "reimbursement_huf": round(reimbursement, 0),
        "fuel_liters": round(fuel_liters, 2),
        "fuel_cost_huf": round(fuel_cost, 0),
        "total_reimbursable_huf": round(reimbursement, 0),
        "fuel_price_huf_l": round(fuel_price, 0),
        "fuel_price_source": str(meta.get("fuel_price_source") or ""),
        "fuel_consumption_l_100km": round(consumption, 2),
        "fuel_consumption_source": str(meta.get("fuel_consumption_source") or ""),
        "engine_cc": int(meta.get("engine_cc") or 0),
        "pages": len(pages),
    }
