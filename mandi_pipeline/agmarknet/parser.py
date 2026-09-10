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
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from loguru import logger

from ..config import settings
from .client import SchemaError


@dataclass
class ParseReport:
    """What normalize_rows did -- so the sweep can tell 'this commodity
    genuinely had 3 rows' from 'we fetched 300 and silently threw 297 away'.

      raw           rows in the /report response
      kept          rows returned (go to both the DB and the CSV)
      dropped       rows discarded -- no usable price at all, or a missing
                    key field (state/district/market/commodity/date)
      partial_price rows KEPT with at least one of min/max/modal NULL --
                    stored EXACTLY as the site shows them (a blank cell is
                    not guessed from the other two). Auditable, not a loss.
      drop_reasons  {reason: count}
    """
    raw: int = 0
    kept: int = 0
    dropped: int = 0
    partial_price: int = 0
    drop_reasons: dict[str, int] = field(default_factory=dict)

    @property
    def missing(self) -> int:
        return self.dropped

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
    canon = "|".join(
        "" if d.get(k) is None else str(d.get(k))
        for k in (
            "state", "district", "market", "commodity_group", "commodity", "variety", "grade",
            "arrival_date", "price_date", "min_price", "max_price", "modal_price",
            "price_unit", "arrival_quantity", "arrival_unit",
        )
    )
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def normalize_rows(
    raw_rows: list[dict],
    *,
    fallback_commodity: str,
    fallback_group: str | None,
    source: str = "agmarknet_api",
) -> tuple[list[dict], ParseReport]:
    """Map + clean + hash. Returns (rows, report) -- rows keyed exactly by
    repository.PRICE_COLS, de-duplicated on the v3 unique key (last row wins),
    and a ParseReport describing what was kept / partial / dropped so the
    caller can surface silent data loss.

    A row is dropped ONLY when a key field (state/district/market/commodity/
    arrival_date) is missing or when NONE of min/max/modal price parsed.
    A row with 1 or 2 of the three prices is KEPT and stored EXACTLY as the
    site shows it -- the blank price columns stay NULL (not guessed from the
    others), so the row is a faithful copy. Set CSV_BACKFILL_PARTIAL_PRICES
    to fill the gaps from the present price(s) instead."""
    rep = ParseReport(raw=len(raw_rows))
    if not raw_rows:
        return [], rep

    def _drop(reason: str) -> None:
        rep.dropped += 1
        rep.drop_reasons[reason] = rep.drop_reasons.get(reason, 0) + 1

    out: dict[tuple, dict] = {}
    for raw in raw_rows:
        if not isinstance(raw, dict):
            _drop("not_a_dict")
            continue
        arrival = _parse_date(_get(raw, "arrival_date"))
        mn, mx, md = _num(_get(raw, "min_price")), _num(_get(raw, "max_price")), _num(_get(raw, "modal_price"))
        state, district, market = _get(raw, "state"), _get(raw, "district"), _get(raw, "market")
        commodity = _get(raw, "commodity") or fallback_commodity

        if not all((state, district, market, commodity)):
            _drop("missing_key_field")
            continue
        if arrival is None:
            _drop("unparseable_arrival_date")
            continue

        if None in (mn, mx, md):
            if all(p is None for p in (mn, mx, md)):
                _drop("no_price")
                continue
            rep.partial_price += 1
            if settings.csv_backfill_partial_prices:
                # opt-in: fill the blanks from whatever price(s) are present
                if mn is None:
                    mn = mx if mx is not None else md
                if mx is None:
                    mx = mn if mn is not None else md
                if md is None:
                    md = mn
            # default: leave the missing price(s) as NULL -- store what the
            # site actually shows.

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

    if rep.dropped and not out:
        raise SchemaError(
            f"every one of {len(raw_rows)} rows failed to map -- the /report row "
            f"schema likely changed. Sample raw row: {raw_rows[0]!r}"
        )
    rep.kept = len(out)
    if rep.dropped:
        logger.warning(f"parser: dropped {rep.dropped}/{rep.raw} unmappable/incomplete rows "
                       f"for {fallback_commodity!r} ({rep.drop_reasons})")
    if rep.partial_price:
        how = "filled from present price(s)" if settings.csv_backfill_partial_prices else "stored as-is (NULL kept)"
        logger.info(f"parser: {rep.partial_price}/{rep.raw} rows for {fallback_commodity!r} "
                    f"had a partial price -- {how}")
    return list(out.values()), rep
