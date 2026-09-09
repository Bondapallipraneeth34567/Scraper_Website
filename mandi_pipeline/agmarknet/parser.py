"""Turn one /report response's row objects into rows ready for
mandi.market_prices_v3.

The KEY NAMES below were VERIFIED against a live /report response on
2026-09-04 (Wheat, 119 rows). A real row looks like:

  {state_name, district_name, market_name, cmdt_grp_name, cmdt_name,
   variety_name, grade_name, arrival_date, min_price, max_price,
   model_price,          # note: "model", not "modal"
   arrival_qty, unit_name_price, unit_name_arrival}

There is no price_date field -> we fall back to arrival_date. The extra
candidate names are kept so a future rename degrades gracefully; if EVERY
row in a non-empty response is unmapped we raise SchemaError.
`run.py --dump-sample` re-dumps a live row to re-confirm.
"""
from __future__ import annotations

import hashlib
from datetime import date, datetime
from typing import Any

from loguru import logger

from .client import SchemaError

# field -> candidate keys. First entry = the VERIFIED 2026-09-04 live key.
_CANDIDATES: dict[str, tuple[str, ...]] = {
    "state":            ("state_name", "state", "State/UT", "state_ut", "st_name"),
    "district":         ("district_name", "district", "District", "dist_name"),
    "market":           ("market_name", "market", "Market", "mkt_name", "apmc"),
    "commodity_group":  ("cmdt_grp_name", "commodity_group", "group_name", "cmdt_group_name", "Commodity Group"),
    "commodity":        ("cmdt_name", "commodity", "commodity_name", "Commodity"),
    "variety":          ("variety_name", "variety", "Variety", "vrty_name"),
    "grade":            ("grade_name", "grade", "Grade", "grd_name"),
    "arrival_date":     ("arrival_date", "arrivaldate", "Arrival Date", "arrival_dt", "date"),
    "price_date":       ("price_date", "pricedate", "Price Date", "price_dt"),
    "min_price":        ("min_price", "minprice", "Min Price", "min_prc", "minimum_price"),
    "max_price":        ("max_price", "maxprice", "Max Price", "max_prc", "maximum_price"),
    "modal_price":      ("model_price", "modal_price", "modalprice", "Modal Price", "modal_prc"),
    "price_unit":       ("unit_name_price", "price_unit", "priceunit", "Price Unit", "prc_unit"),
    "arrival_quantity": ("arrival_qty", "arrival_quantity", "Arrival Quantity", "arrivals", "qty"),
    "arrival_unit":     ("unit_name_arrival", "arrival_unit", "arrivalunit", "Arrival Unit", "qty_unit"),
}
_REQUIRED = ("state", "district", "market", "commodity", "arrival_date",
             "min_price", "max_price", "modal_price")

_DATE_FORMATS = ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d-%b-%Y", "%d %b %Y", "%d %B %Y", "%Y/%m/%d")


def _get(row: dict, field: str) -> Any:
    for k in _CANDIDATES[field]:
        if k in row and row[k] not in (None, ""):
            return row[k]
    # case-insensitive last resort
    low = {str(k).lower(): v for k, v in row.items()}
    for k in _CANDIDATES[field]:
        if k.lower() in low and low[k.lower()] not in (None, ""):
            return low[k.lower()]
    return None


def _num(v: Any) -> float | None:
    if v in (None, "", "NR", "-"):
        return None
    try:
        return float(str(v).replace(",", "").strip())
    except ValueError:
        return None


def _parse_date(v: Any) -> date | None:
    if not v:
        return None
    s = str(v).strip()
    for f in _DATE_FORMATS:
        try:
            return datetime.strptime(s, f).date()
        except ValueError:
            continue
    return None


def _row_hash(d: dict) -> str:
    canon = "|".join(str(d.get(k, "")) for k in (
        "state", "district", "market", "commodity_group", "commodity", "variety", "grade",
        "arrival_date", "price_date", "min_price", "max_price", "modal_price",
        "price_unit", "arrival_quantity", "arrival_unit",
    ))
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def normalize_rows(
    raw_rows: list[dict],
    *,
    fallback_commodity: str,
    fallback_group: str | None,
    source: str = "agmarknet_api",
) -> list[dict]:
    """Map + clean + hash. Returns rows keyed exactly by repository.PRICE_COLS.
    De-duplicates on the v3 unique key (last row wins)."""
    if not raw_rows:
        return []

    out: dict[tuple, dict] = {}
    dropped = 0
    for raw in raw_rows:
        if not isinstance(raw, dict):
            dropped += 1
            continue
        arrival = _parse_date(_get(raw, "arrival_date"))
        mn, mx, md = _num(_get(raw, "min_price")), _num(_get(raw, "max_price")), _num(_get(raw, "modal_price"))
        state, district, market = _get(raw, "state"), _get(raw, "district"), _get(raw, "market")
        commodity = _get(raw, "commodity") or fallback_commodity

        if not all((state, district, market, commodity)) or arrival is None or None in (mn, mx, md):
            dropped += 1
            continue

        rec = {
            "state": str(state).strip(),
            "district": str(district).strip(),
            "market": str(market).strip(),
            "commodity_group": (str(_get(raw, "commodity_group")).strip()
                                if _get(raw, "commodity_group") else (fallback_group or None)),
            "commodity": str(commodity).strip(),
            "variety": (str(_get(raw, "variety")).strip() if _get(raw, "variety") else ""),
            "grade": (str(_get(raw, "grade")).strip() if _get(raw, "grade") else ""),
            "arrival_date": arrival,
            "price_date": _parse_date(_get(raw, "price_date")) or arrival,
            "min_price": mn,
            "max_price": mx,
            "modal_price": md,
            "price_unit": (str(_get(raw, "price_unit")).strip() if _get(raw, "price_unit") else None),
            "arrival_quantity": _num(_get(raw, "arrival_quantity")),
            "arrival_unit": (str(_get(raw, "arrival_unit")).strip() if _get(raw, "arrival_unit") else None),
            "source": source,
        }
        rec["source_row_hash"] = _row_hash(rec)
        key = (rec["state"], rec["district"], rec["market"], rec["commodity"],
               rec["variety"], rec["grade"], rec["arrival_date"])
        out[key] = rec

    if dropped and not out:
        raise SchemaError(
            f"every one of {len(raw_rows)} rows failed to map -- the /report row "
            f"schema likely changed. Sample raw row: {raw_rows[0]!r}"
        )
    if dropped:
        logger.warning(f"parser: dropped {dropped}/{len(raw_rows)} unmappable/incomplete rows "
                       f"for {fallback_commodity!r}")
    return list(out.values())
