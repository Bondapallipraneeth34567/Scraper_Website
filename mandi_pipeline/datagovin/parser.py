"""data.gov.in record -> row for mandi.market_prices_v4.

Verified record shape (2026-09-04):
  { "state":"Haryana", "district":"Kurukshetra", "market":"Shahabad APMC",
    "commodity":"Orange", "variety":"Orange", "grade":"Medium",
    "arrival_date":"04/09/2026",             # DD/MM/YYYY
    "min_price":10000, "max_price":10000, "modal_price":10000 }

This resource carries NO commodity group, price unit, or arrival quantity
-- those columns are left NULL (the AGMARKNET direct API, Approach 1, does
carry them).
"""
from __future__ import annotations

from loguru import logger

from ..normalize_common import parse_date, parse_price, row_hash
from .client import SchemaError

_REQUIRED = ("state", "district", "market", "commodity")


def _s(v) -> str:
    return "" if v is None else str(v).strip()


def normalize_records(records: list[dict], *, source: str = "data_gov_in") -> list[dict]:
    """Map + clean + hash + de-dupe on the unique key (last row wins)."""
    if not records:
        return []

    out: dict[tuple, dict] = {}
    dropped = 0
    for rec in records:
        if not isinstance(rec, dict):
            dropped += 1
            continue
        low = {str(k).lower(): v for k, v in rec.items()}
        arrival = parse_date(low.get("arrival_date"))
        mn = parse_price(low.get("min_price"))
        mx = parse_price(low.get("max_price"))
        md = parse_price(low.get("modal_price"))
        if (not all(_s(low.get(k)) for k in _REQUIRED)) or arrival is None or None in (mn, mx, md):
            dropped += 1
            continue

        rec_out = {
            "state": _s(low.get("state")),
            "district": _s(low.get("district")),
            "market": _s(low.get("market")),
            "commodity_group": None,
            "commodity": _s(low.get("commodity")),
            "variety": _s(low.get("variety")),
            "grade": _s(low.get("grade")),
            "arrival_date": arrival,
            "price_date": arrival,
            "min_price": mn,
            "max_price": mx,
            "modal_price": md,
            "price_unit": None,
            "arrival_quantity": None,
            "arrival_unit": None,
            "source": source,
        }
        rec_out["source_row_hash"] = row_hash(rec_out)
        key = (rec_out["state"], rec_out["district"], rec_out["market"], rec_out["commodity"],
               rec_out["variety"], rec_out["grade"], rec_out["arrival_date"])
        out[key] = rec_out

    if dropped and not out:
        raise SchemaError(
            f"all {len(records)} records failed to map -- the resource schema likely changed. "
            f"Sample: {records[0]!r}"
        )
    if dropped:
        logger.warning(f"datagovin parser: dropped {dropped}/{len(records)} incomplete records")
    return list(out.values())
