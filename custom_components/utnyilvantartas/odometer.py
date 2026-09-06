from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _load(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _write(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def update_odometer_ledger(
    path: str | Path,
    month: str,
    configured_base_km: float,
    private_km: float,
) -> dict[str, float | str]:
    """Keep AXEL-like continuous odometer values month to month.

    The configured starting odometer is the base for the first month ever stored
    in the ledger. Every following stored month starts at the previous stored
    month's ending odometer and adds its own private-trip kilometres.
    """
    path = Path(path)
    ledger = _load(path)
    entries = ledger.get("months")
    if not isinstance(entries, dict):
        entries = {}

    base_month = str(ledger.get("base_month") or "")
    base_km_raw = ledger.get("base_odometer_km")
    try:
        base_km = float(base_km_raw)
    except (TypeError, ValueError):
        base_km = float(configured_base_km)

    if not base_month:
        base_month = month
        base_km = float(configured_base_km)
    elif month < base_month:
        # Historical backfill: the newly added earlier month becomes the first
        # month in the continuous AXEL-like chain. Later months are rebuilt.
        base_month = month

    # If the configured base odometer changes, apply it to the ledger base.
    # This lets the user correct the starting value from Integration options.
    if abs(base_km - float(configured_base_km)) > 0.0001:
        base_km = float(configured_base_km)

    current = entries.get(month)
    if not isinstance(current, dict):
        current = {}
    current["private_km"] = round(float(private_km), 3)
    entries[month] = current

    # Rebuild the continuous odometer chain in chronological order, beginning
    # with the ledger base month. Months earlier than the base are retained but
    # do not alter the base chain.
    running = float(base_km)
    ordered = sorted(key for key in entries if isinstance(key, str) and key >= base_month)
    for key in ordered:
        item = entries.get(key) or {}
        try:
            km = float(item.get("private_km") or 0.0)
        except (TypeError, ValueError):
            km = 0.0
        item["start_km"] = round(running, 3)
        running += km
        item["end_km"] = round(running, 3)
        entries[key] = item

    ledger = {
        "version": 1,
        "base_month": base_month,
        "base_odometer_km": round(base_km, 3),
        "months": entries,
    }
    _write(path, ledger)

    result = entries[month]
    return {
        "month": month,
        "private_km": float(result.get("private_km") or 0.0),
        "start_km": float(result.get("start_km") or configured_base_km),
        "end_km": float(result.get("end_km") or (configured_base_km + private_km)),
    }
