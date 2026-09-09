"""Commodity taxonomy from GET /v1/daily-price-arrival/filters (no CAPTCHA).

The web app calls this to populate its Commodity-Group / Commodity
dropdowns. We call it to know the (group_id, commodity_id) pairs to sweep.
Result is cached in mandi.agmarknet_taxonomy; refresh on demand.

Response shape seen in the 2026-09-03 probes:
  data.cmdt_data       = [ {cmdt_id, cmdt_name, cmdt_group_id}, ... ]   (~605)
  data.cmdt_group_data = [ {cmdt_group_id, cmdt_group_name}, ... ]
Key names are mapped tolerantly below in case they shift.
"""
from __future__ import annotations

from loguru import logger

from ..db import repository as repo
from .client import AgmarknetClient, SchemaError


def _first(d: dict, *names, default=None):
    for n in names:
        if n in d and d[n] not in (None, ""):
            return d[n]
    return default


def parse_filters(data: dict) -> list[dict]:
    """-> [{commodity_id, commodity_name, group_id, group_name}, ...]"""
    commodities = (
        data.get("cmdt_data")
        or data.get("commodity_data")
        or data.get("commodities")
        or []
    )
    groups = (
        data.get("cmdt_group_data")
        or data.get("group_data")
        or data.get("groups")
        or []
    )
    if not commodities:
        raise SchemaError(f"filters: no commodity list found (keys {sorted(data)})")

    gname: dict[int, str] = {}
    for g in groups:
        gid = _first(g, "cmdt_group_id", "group_id", "id")
        gn = _first(g, "cmdt_group_name", "group_name", "name")
        if gid is not None:
            gname[int(gid)] = str(gn) if gn else None

    out: list[dict] = []
    for c in commodities:
        cid = _first(c, "cmdt_id", "commodity_id", "id")
        cn = _first(c, "cmdt_name", "commodity_name", "name")
        gid = _first(c, "cmdt_group_id", "group_id")
        if cid is None or cn is None:
            continue
        gid_i = int(gid) if gid is not None else None
        out.append({
            "commodity_id": int(cid),
            "commodity_name": str(cn).strip(),
            "group_id": gid_i,
            "group_name": gname.get(gid_i) if gid_i is not None else None,
        })
    if not out:
        raise SchemaError("filters: commodity list present but nothing parsed")
    return out


def refresh(conn, client: AgmarknetClient) -> list[dict]:
    data = client.get_filters()
    rows = parse_filters(data)
    n = repo.save_taxonomy(conn, rows)
    logger.info(f"taxonomy refreshed: {n} commodities cached")
    return rows


def get_sweep_list(conn, client: AgmarknetClient, *, force_refresh: bool = False) -> list[dict]:
    """The list of commodities to sweep. Uses the cached taxonomy unless
    forced, or unless the cache is empty (first run)."""
    if not force_refresh:
        cached = repo.load_taxonomy(conn, active_only=True)
        if cached:
            return cached
        logger.info("taxonomy cache empty -> fetching from /filters")
    return refresh(conn, client)
