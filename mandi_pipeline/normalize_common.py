"""Shared normalisation helpers used by more than one method's parser
(agmarknet/, datagovin/, ...). Method-specific mapping stays in each
method's own parser; only the format-agnostic bits live here.
"""
from __future__ import annotations

import hashlib
from datetime import date, datetime
from typing import Any

_DATE_FORMATS = (
    "%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%m/%d/%Y",
    "%d-%b-%Y", "%d %b %Y", "%d %B %Y", "%Y/%m/%d",
)

# The business fields whose combination defines "the same price observation".
# Order matters -- it's the canonical string the hash is taken over.
HASH_FIELDS = (
    "state", "district", "market", "commodity_group", "commodity", "variety", "grade",
    "arrival_date", "price_date", "min_price", "max_price", "modal_price",
    "price_unit", "arrival_quantity", "arrival_unit",
)


def parse_price(v: Any) -> float | None:
    if v in (None, "", "NR", "-", "NA", "N/A"):
        return None
    try:
        return float(str(v).replace(",", "").strip())
    except ValueError:
        return None


def parse_date(v: Any) -> date | None:
    if not v:
        return None
    s = str(v).strip()
    for f in _DATE_FORMATS:
        try:
            return datetime.strptime(s, f).date()
        except ValueError:
            continue
    return None


def row_hash(row: dict) -> str:
    canon = "|".join(str(row.get(k, "")) for k in HASH_FIELDS)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()
