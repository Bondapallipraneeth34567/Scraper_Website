"""Ingest already-downloaded AGMARKNET / data.gov.in price CSVs straight
into a target table (default mandi.market_prices_v4), then verify every
CSV row against the DB.

This does NOT touch any API -- you download the CSV(s) yourself (any date
range) and hand the file(s) to this script. No key, no 10k-row window.

Usage:
  python -m mandi_pipeline.csv_import.ingest PATH [PATH ...] [options]

  PATH             a .csv file, or a directory (all *.csv inside are used)
  --table T        target table            (default: mandi.market_prices_v4)
  --source S       value for `source` col  (default: csv_import)
  --dry-run        parse + verify against the CURRENT db, write nothing
  --verify-only    skip the load, just reconcile the CSV against the db
  --report FILE    also write a .docx reconciliation report
  --limit N        only the first N data rows per file (testing)
  --batch N        rows per upsert batch / pause every batch (default 5000)
  --pause SEC      sleep this long between batches ("slowly")   (default 0)

Reconciliation buckets, per file and overall:
  matched        key in db AND source_row_hash identical
  mismatched     key in db but a business field differs (samples printed)
  missing_in_db  CSV row that is not in the db
  extra_in_db    db rows for the CSV's date range that are not in the CSV
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
import time
from collections import Counter
from datetime import date
from pathlib import Path

from loguru import logger

from ..config import settings
from ..db import repository as repo
from ..logging_setup import setup_logging
from ..normalize_common import parse_date, parse_price, row_hash

# ---- tolerant column mapping -------------------------------------------------
# field -> candidate header names (matched case-insensitively, and with
# spaces/underscores/_x0020_ normalised away).
_CANDIDATES: dict[str, tuple[str, ...]] = {
    "state":            ("state", "state_name", "state/ut", "stateut", "st_name"),
    "district":         ("district", "district_name", "dist_name", "districtname"),
    "market":           ("market", "market_name", "marketname", "mkt_name", "market_center", "apmc"),
    "commodity_group":  ("commodity_group", "commoditygroup", "group", "group_name", "cmdt_grp_name"),
    "commodity":        ("commodity", "commodity_name", "commodityname", "cmdt_name"),
    "variety":          ("variety", "variety_name", "varietyname", "vrty_name"),
    "grade":            ("grade", "grade_name", "gradename", "grd_name"),
    "arrival_date":     ("arrival_date", "arrivaldate", "arrival_dt", "date", "reported_date", "reporteddate"),
    "price_date":       ("price_date", "pricedate", "price_dt"),
    "min_price":        ("min_price", "minprice", "min_prc", "minimum_price",
                         "min_price_rsquintal", "min_price_rs_quintal"),
    "max_price":        ("max_price", "maxprice", "max_prc", "maximum_price",
                         "max_price_rsquintal", "max_price_rs_quintal"),
    "modal_price":      ("modal_price", "modalprice", "model_price", "modelprice", "modal_prc",
                         "modal_price_rsquintal", "modal_price_rs_quintal"),
    "price_unit":       ("price_unit", "priceunit", "unit", "unit_name_price", "prc_unit"),
    "arrival_quantity": ("arrival_quantity", "arrivalquantity", "arrival_qty", "arrivals", "qty"),
    "arrival_unit":     ("arrival_unit", "arrivalunit", "unit_name_arrival", "qty_unit"),
}
_REQUIRED = ("state", "district", "market", "commodity", "arrival_date",
             "min_price", "max_price", "modal_price")
_KEY = ("state", "district", "market", "commodity", "variety", "grade", "arrival_date")


def _norm_header(h: str) -> str:
    h = (h or "").replace("_x0020_", " ").replace("﻿", "")
    h = re.sub(r"\(.*?\)", " ", h)                 # drop "(Rs./Quintal)" etc.
    return re.sub(r"[^a-z0-9]", "", h.lower())


def _build_colmap(fieldnames: list[str]) -> dict[str, str]:
    """field -> actual CSV header. Also picks up a price unit hidden in a
    header like 'Min Price (Rs./Quintal)'."""
    norm_to_real = {}
    for real in fieldnames:
        norm_to_real.setdefault(_norm_header(real), real)
    colmap: dict[str, str] = {}
    for field, cands in _CANDIDATES.items():
        for cand in cands:
            if cand in norm_to_real:
                colmap[field] = norm_to_real[cand]
                break
    return colmap


def _unit_from_headers(fieldnames: list[str]) -> str | None:
    for h in fieldnames:
        m = re.search(r"\(([^)]*(?:Rs|/)[^)]*)\)", h or "")
        if m:
            return m.group(1).strip()
    return None


# ---- read + normalise one CSV --------------------------------------------------
def load_csv_rows(path: Path, source: str, limit: int | None) -> tuple[list[dict], dict]:
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        headers = reader.fieldnames or []
        colmap = _build_colmap(headers)
        missing = [f for f in _REQUIRED if f not in colmap]
        if missing:
            raise SystemExit(
                f"{path.name}: could not map required column(s) {missing}. "
                f"Headers seen: {headers}"
            )
        header_unit = _unit_from_headers(headers) if "price_unit" not in colmap else None

        out: dict[tuple, dict] = {}
        stats = Counter()
        for i, raw in enumerate(reader):
            if limit and i >= limit:
                break
            stats["csv_rows"] += 1

            def g(field):
                col = colmap.get(field)
                return (raw.get(col) or "").strip() if col else ""

            arrival = parse_date(g("arrival_date"))
            mn, mx, md = parse_price(g("min_price")), parse_price(g("max_price")), parse_price(g("modal_price"))
            state, district, market, commodity = g("state"), g("district"), g("market"), g("commodity")
            if not all((state, district, market, commodity)) or arrival is None or None in (mn, mx, md):
                stats["dropped_incomplete"] += 1
                continue

            rec = {
                "state": state, "district": district, "market": market,
                "commodity_group": g("commodity_group") or None,
                "commodity": commodity,
                "variety": g("variety") or "",
                "grade": g("grade") or "",
                "arrival_date": arrival,
                "price_date": parse_date(g("price_date")) or arrival,
                "min_price": mn, "max_price": mx, "modal_price": md,
                "price_unit": g("price_unit") or header_unit or None,
                "arrival_quantity": parse_price(g("arrival_quantity")),
                "arrival_unit": g("arrival_unit") or None,
                "source": source,
            }
            rec["source_row_hash"] = row_hash(rec)
            key = tuple(rec[k] for k in _KEY)
            if key in out:
                stats["dupe_in_csv"] += 1
            out[key] = rec                            # last wins

        stats["unique_rows"] = len(out)
        return list(out.values()), dict(stats)


# ---- verify one file's rows against the db ----------------------------------
def verify(conn, rows: list[dict], table: str) -> dict:
    dates = {r["arrival_date"] for r in rows}
    db_hashes = repo.load_row_hashes(conn, table, min_arrival_date=min(dates))
    db_hashes = {k: v for k, v in db_hashes.items()
                 if k[-1] in dates}                   # k[-1] is arrival_date

    res = Counter()
    mismatches, missing = [], []
    csv_keys = set()
    for r in rows:
        key = tuple(r[k] for k in _KEY)
        csv_keys.add(key)
        if key not in db_hashes:
            res["missing_in_db"] += 1
            if len(missing) < 10:
                missing.append(r)
        elif db_hashes[key] == r["source_row_hash"]:
            res["matched"] += 1
        else:
            res["mismatched"] += 1
            if len(mismatches) < 10:
                mismatches.append(r)
    res["extra_in_db"] = len(set(db_hashes) - csv_keys)
    return {"counts": dict(res), "mismatch_samples": mismatches, "missing_samples": missing}


# ---- driver ---------------------------------------------------------------------
def _iter_csv_paths(args_paths: list[str]) -> list[Path]:
    out: list[Path] = []
    for p in args_paths:
        pp = Path(p)
        if pp.is_dir():
            out.extend(sorted(pp.glob("*.csv")))
        elif pp.is_file():
            out.append(pp)
        else:
            raise SystemExit(f"not found: {p}")
    if not out:
        raise SystemExit("no .csv files found")
    return out


def main(argv: list[str] | None = None) -> int:
    setup_logging()
    ap = argparse.ArgumentParser(prog="mandi_pipeline.csv_import.ingest")
    ap.add_argument("paths", nargs="+", help="CSV file(s) or directory(ies)")
    ap.add_argument("--table", default=settings.datagovin_target_table)
    ap.add_argument("--source", default="csv_import")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--verify-only", action="store_true")
    ap.add_argument("--report", default=None)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--batch", type=int, default=5000)
    ap.add_argument("--pause", type=float, default=0.0)
    args = ap.parse_args(argv)

    files = _iter_csv_paths(args.paths)
    logger.info(f"{len(files)} CSV file(s); target table {args.table}; "
                f"{'DRY RUN' if args.dry_run else ('VERIFY ONLY' if args.verify_only else 'LOAD + VERIFY')}")

    report_rows = []
    grand = Counter()
    with repo.connect() as conn:
        for f in files:
            rows, cstats = load_csv_rows(f, args.source, args.limit)
            logger.info(f"{f.name}: {cstats.get('csv_rows',0)} rows read, "
                        f"{cstats['unique_rows']} unique, "
                        f"{cstats.get('dropped_incomplete',0)} dropped, "
                        f"{cstats.get('dupe_in_csv',0)} dupes-in-csv")

            ins = upd = unch = 0
            if not args.dry_run and not args.verify_only:
                for i in range(0, len(rows), args.batch):
                    chunk = rows[i:i + args.batch]
                    a, b, c = repo.upsert_price_rows(conn, chunk, table=args.table, reset_stale=True)
                    ins += a; upd += b; unch += c
                    if args.pause:
                        time.sleep(args.pause)
                logger.info(f"{f.name}: upserted -> +{ins} new / ~{upd} changed / ={unch} unchanged")

            v = verify(conn, rows, args.table)
            vc = v["counts"]
            logger.info(f"{f.name}: VERIFY  matched={vc.get('matched',0)}  "
                        f"mismatched={vc.get('mismatched',0)}  "
                        f"missing_in_db={vc.get('missing_in_db',0)}  "
                        f"extra_in_db={vc.get('extra_in_db',0)}")
            for m in v["mismatch_samples"]:
                logger.warning(f"  mismatch: {m['state']}/{m['district']}/{m['market']}/"
                               f"{m['commodity']}/{m['variety']}/{m['grade']} {m['arrival_date']} "
                               f"min={m['min_price']} max={m['max_price']} modal={m['modal_price']}")

            for k, val in {**cstats, **vc, "inserted": ins, "updated": upd, "unchanged": unch}.items():
                grand[k] += val
            report_rows.append({"file": f.name, **cstats,
                                "inserted": ins, "updated": upd, "unchanged": unch, **vc})

    print("\n===== OVERALL =====")
    for k in ("csv_rows", "unique_rows", "dropped_incomplete", "dupe_in_csv",
              "inserted", "updated", "unchanged",
              "matched", "mismatched", "missing_in_db", "extra_in_db"):
        print(f"  {k:18} {grand.get(k, 0)}")

    ok = grand.get("mismatched", 0) == 0 and grand.get("missing_in_db", 0) == 0
    print(f"\n  RESULT: {'CSV and DB reconcile' if ok else 'DISCREPANCIES -- see above'}")

    if args.report:
        _write_report(Path(args.report), report_rows, dict(grand), ok, args.table)
        logger.info(f"report -> {args.report}")

    return 0 if ok else 1


def _write_report(path: Path, rows: list[dict], grand: dict, ok: bool, table: str) -> None:
    try:
        from docx import Document
        from docx.shared import Pt
    except ImportError:
        logger.warning("python-docx not installed -- skipping --report")
        return
    doc = Document()
    doc.add_heading("CSV → DB Reconciliation", level=0)
    doc.add_paragraph(f"Target table: {table}    Generated: {date.today().isoformat()}")
    p = doc.add_paragraph()
    r = p.add_run("RESULT: " + ("CSV and DB reconcile." if ok else "DISCREPANCIES found."))
    r.bold = True
    doc.add_heading("Overall", level=1)
    for k in ("csv_rows", "unique_rows", "dropped_incomplete", "dupe_in_csv",
              "inserted", "updated", "unchanged",
              "matched", "mismatched", "missing_in_db", "extra_in_db"):
        doc.add_paragraph(f"{k}: {grand.get(k, 0)}", style="List Bullet")
    doc.add_heading("Per file", level=1)
    cols = ["file", "csv_rows", "unique_rows", "inserted", "updated", "unchanged",
            "matched", "mismatched", "missing_in_db", "extra_in_db"]
    t = doc.add_table(rows=1, cols=len(cols))
    t.style = "Light Grid Accent 1"
    for i, c in enumerate(cols):
        t.rows[0].cells[i].paragraphs[0].add_run(c).bold = True
    for row in rows:
        cells = t.add_row().cells
        for i, c in enumerate(cols):
            cells[i].text = str(row.get(c, 0))
    doc.save(path)


if __name__ == "__main__":
    sys.exit(main())
