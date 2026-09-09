"""Write one CSV per commodity per day -- the same artifact the old
browser scraper produced via the site's "Download CSV" button, except we
build it from the /report JSON (which is exactly what the site's own
front-end does).

Layout:  {CSV_OUTPUT_DIR}/{YYYY-MM-DD}/{Safe Commodity Name}.csv
Columns and header text match the site's export so anything that already
reads those files keeps working.
"""
from __future__ import annotations

import csv
import re
from datetime import date
from pathlib import Path

from loguru import logger

from ..config import settings
from ..storage import upload_csv

# (header text in the file, key in a normalized row dict)
_COLUMNS: list[tuple[str, str]] = [
    ("Commodity Group", "commodity_group"),
    ("Commodity", "commodity"),
    ("Variety", "variety"),
    ("Grade", "grade"),
    ("Market", "market"),
    ("District", "district"),
    ("State/UT", "state"),
    ("Arrival Date", "arrival_date"),
    ("Min Price", "min_price"),
    ("Max Price", "max_price"),
    ("Modal Price", "modal_price"),
    ("Price Unit", "price_unit"),
    ("Arrival Quantity", "arrival_quantity"),
    ("Arrival Unit", "arrival_unit"),
]

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_name(commodity: str) -> str:
    return _SAFE.sub("_", commodity.strip()).strip("_") or "commodity"


def commodity_csv_path(target_date: date, commodity: str) -> Path:
    return settings.csv_output_dir / target_date.isoformat() / f"{_safe_name(commodity)}.csv"


def _fmt(col: str, value) -> str:
    if value is None:
        return ""
    if col == "Arrival Date" and isinstance(value, date):
        return value.strftime("%d-%m-%Y")            # site's CSV format
    if col in ("Min Price", "Max Price", "Modal Price", "Arrival Quantity"):
        # plain number, no thousands separators
        return f"{float(value):g}"
    return str(value)


def write_commodity_csv(target_date: date, commodity: str, rows: list[dict]) -> Path | None:
    """rows = normalized dicts (repository.PRICE_COLS keys). Returns the path
    written, or None if disabled / no rows."""
    if not settings.csv_output_enabled or not rows:
        return None
    path = commodity_csv_path(target_date, commodity)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".csv.tmp")
    with tmp.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow([h for h, _ in _COLUMNS])
        for r in rows:
            w.writerow([_fmt(h, r.get(k)) for h, k in _COLUMNS])
    tmp.replace(path)                                 # atomic
    logger.info(f"📄 wrote {len(rows)} rows -> {path}")

    # Mirror to Azure Blob (durable copy). Local file is the staging copy;
    # keep it unless CSV_LOCAL_KEEP=false AND the upload is confirmed.
    uploaded = upload_csv(target_date, path)
    if uploaded and not settings.csv_local_keep:
        try:
            path.unlink()
            logger.debug(f"removed local {path} (mirrored to blob, CSV_LOCAL_KEEP=false)")
        except OSError as e:
            logger.debug(f"could not remove local {path}: {e}")
    return path
