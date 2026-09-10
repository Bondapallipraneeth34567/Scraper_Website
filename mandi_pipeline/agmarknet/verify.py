"""Reconcile the per-commodity CSV artifacts / DB against AGMARKNET for one
arrival date, and optionally re-scrape whatever is short.

Two phases:

  OFFLINE  (always)  -- free, no network. For every commodity lines up
      csv    data rows in {CSV_OUTPUT_DIR}/{date}/{Commodity}.csv
      db     rows in market_prices_v3 for (commodity, arrival_date)
      prog   pipeline_progress.rows_upserted from the last sweep
    and flags: NO CSV / NO DB ROWS / CSV SHORT / CSV OVER / PROG!=DB.

  LIVE  (--live / --fix)  -- for each commodity that looks short (or the
    --only list), solves ONE CAPTCHA, pulls the live /report for the date,
    and diffs the site's rows against what we stored:
      missing  rows the site has that market_prices_v3 does NOT
      stale    rows we hold that the site no longer returns
    With --fix, any commodity the site is ahead on is re-scraped on the
    spot (upsert + CSV rebuilt from the DB); without --fix it only reports
    what it WOULD re-scrape.

    python -m mandi_pipeline.run --verify-csv --date 2026-09-06
    python -m mandi_pipeline.run --verify-csv --date 2026-09-06 --live
    python -m mandi_pipeline.run --verify-csv --date 2026-09-06 --fix
    python -m mandi_pipeline.run --verify-csv --date 2026-09-06 --fix --only Tomato,Onion

Exit code is 0 only when nothing is left short.
"""
from __future__ import annotations

import contextlib
import csv as _csv
import re
from collections import Counter
from datetime import date

from loguru import logger

from ..config import settings
from ..db import repository as repo

APPROACH = "agmarknet_api"
_KEY = ("state", "district", "market", "variety", "grade")

_norm_re = re.compile(r"[^a-z0-9]+")


def _norm(s: str) -> str:
    return _norm_re.sub("", (s or "").lower())


def _row_key(r: dict) -> tuple:
    return tuple((r.get(k) or "").strip() for k in _KEY)


def _csv_counts(day_dir) -> tuple[dict[str, int], list[str]]:
    """{commodity (as written in the file): data-row count} for every CSV in
    the day's directory, plus a list of files that couldn't be read."""
    counts: Counter[str] = Counter()
    bad: list[str] = []
    for path in sorted(day_dir.glob("*.csv")):
        try:
            with path.open("r", newline="", encoding="utf-8") as fh:
                rdr = _csv.DictReader(fh)
                seen = 0
                for row in rdr:
                    name = (row.get("Commodity") or path.stem).strip()
                    counts[name] += 1
                    seen += 1
                if seen == 0:
                    counts.setdefault(path.stem, 0)
        except (OSError, UnicodeDecodeError, _csv.Error) as e:
            bad.append(f"{path.name}: {e}")
    return dict(counts), bad


def _offline(target_date: date, source: str):
    """Returns (rows_for_table, flagged, totals) where flagged is a list of
    (name, csv_n, db_n, prog_rows, prog_status, verdict)."""
    day_dir = settings.csv_output_dir / target_date.isoformat()
    csv_counts, bad = _csv_counts(day_dir) if day_dir.is_dir() else ({}, [])
    if not day_dir.is_dir():
        logger.warning(f"no CSV directory for {target_date}: {day_dir}")

    with repo.connect() as conn:
        db_counts = repo.commodity_row_counts_for_date(conn, target_date, source)
        prog = repo.progress_detail_for_date(conn, APPROACH, target_date)
    prog_by_norm = {_norm(k): v for k, v in prog.items()}

    names = sorted(set(csv_counts) | set(db_counts), key=str.lower)
    flagged = []
    total_csv = total_db = 0
    for name in names:
        c = csv_counts.get(name, 0)
        d = db_counts.get(name, 0)
        total_csv += c
        total_db += d
        p = prog_by_norm.get(_norm(name))
        p_rows = p["rows_upserted"] if p else None
        p_status = p["status"] if p else "-"

        verdict = None
        if d > 0 and name not in csv_counts:
            verdict = "NO CSV"
        elif c > 0 and d == 0:
            verdict = "NO DB ROWS"
        elif c < d:
            verdict = f"CSV SHORT by {d - c}"
        elif c > d:
            verdict = f"CSV OVER by {c - d}"
        elif p_rows is not None and p_status == "done" and p_rows != d:
            verdict = f"PROG!=DB (prog {p_rows})"
        if verdict:
            flagged.append((name, c, d, p_rows if p_rows is not None else "-", p_status, verdict))

    return {
        "day_dir": day_dir, "csv_counts": csv_counts, "db_counts": db_counts,
        "bad": bad, "flagged": flagged, "total_csv": total_csv, "total_db": total_db,
    }


def _print_offline(o: dict) -> None:
    print(f"\nOFFLINE  csv dir : {o['day_dir']}")
    print(f"  commodities: {len(o['csv_counts'])} with a CSV, {len(o['db_counts'])} with DB rows")
    print(f"  rows      : {o['total_csv']} in CSVs, {o['total_db']} in DB")
    for b in o["bad"]:
        print(f"  ! unreadable CSV: {b}")
    flagged = o["flagged"]
    if not flagged:
        print("  ✓ every commodity reconciles (csv == db).")
        return
    w = max(len(n) for n, *_ in flagged)
    print(f"\n  {len(flagged)} flagged:")
    print(f"    {'commodity'.ljust(w)}  {'csv':>6} {'db':>6} {'prog':>6}  {'status':<9} issue")
    print(f"    {'-' * w}  {'-' * 6} {'-' * 6} {'-' * 6}  {'-' * 9} {'-' * 20}")
    for name, c, d, pr, ps, verdict in flagged:
        print(f"    {name.ljust(w)}  {c:>6} {d:>6} {str(pr):>6}  {ps:<9} {verdict}")


def _live(target_date: date, source: str, o: dict, *, fix: bool,
          only: list[str] | None, limit: int | None) -> int:
    """Diff the live site against the DB for the target commodities.
    Returns the count still short after any --fix."""
    # local imports: only needed on the network path, and they pull in httpx
    from .client import AgmarknetClient
    from .captcha import build_solver
    from .pipeline import SweepStats, probe_commodity, _store_commodity_rows, _single_instance_lock
    from . import taxonomy

    if only:
        targets = list(only)
    else:
        # everything the offline pass thinks the DB might be behind on
        targets = [n for (n, c, d, *_rest, v) in o["flagged"]
                   if v.startswith(("CSV SHORT", "NO CSV", "PROG!=DB"))]
    if limit:
        targets = targets[:limit]

    print(f"\nLIVE  {'re-scrape' if fix else 'dry-run'}  --  {len(targets)} commodit"
          f"{'y' if len(targets) == 1 else 'ies'} to check against the site")
    if not targets:
        return 0

    stats = SweepStats(target_date=target_date.isoformat())
    still_short = 0

    with contextlib.ExitStack() as stack:
        if fix:
            # don't run a re-scrape on top of a live sweep
            try:
                stack.enter_context(_single_instance_lock())
            except BlockingIOError:
                print("  ⚠ a sweep is already running -- skipping --fix (try again later)")
                return len(targets)
        with repo.connect() as conn, AgmarknetClient() as client:
            tax = {c["commodity_name"]: c for c in taxonomy.get_sweep_list(conn, client)}
            tax_by_norm = {_norm(k): v for k, v in tax.items()}
            solver = build_solver()

            w = max((len(t) for t in targets), default=10)
            for name in targets:
                c = tax.get(name) or tax_by_norm.get(_norm(name))
                if not c:
                    print(f"  {name.ljust(w)}  ? not in taxonomy -- skipped")
                    continue
                try:
                    declared, site_rows, prep = probe_commodity(
                        client, solver, c["commodity_id"], c["group_id"],
                        c["commodity_name"], target_date, stats,
                    )
                except Exception as e:  # noqa: BLE001 -- report & move on
                    print(f"  {name.ljust(w)}  ✗ fetch failed: {type(e).__name__}: {str(e)[:80]}")
                    still_short += 1
                    continue

                stored_name = site_rows[0]["commodity"] if site_rows else c["commodity_name"]
                db_rows = repo.rows_for_commodity_date(conn, stored_name, target_date, source)
                site_keys = {_row_key(r) for r in site_rows}
                db_keys = {_row_key(r) for r in db_rows}
                missing = site_keys - db_keys
                stale = db_keys - site_keys
                drop_note = f" +{prep.dropped} unparsable" if prep.dropped else ""

                verdict = "in sync"
                if missing:
                    verdict = f"MISSING {len(missing)} rows vs site"
                elif declared > len(db_rows):
                    verdict = f"site declares {declared}, db has {len(db_rows)}"
                print(f"  {name.ljust(w)}  site={declared:<4} parsed={len(site_rows):<4} "
                      f"db={len(db_rows):<4} missing={len(missing):<3} stale={len(stale):<3}"
                      f"{drop_note}  {verdict}")

                if missing or declared > len(db_rows):
                    if fix and site_rows:
                        ins, upd, unch = _store_commodity_rows(
                            conn, c["commodity_id"], stored_name, target_date, site_rows, stats,
                        )
                        after = len(repo.rows_for_commodity_date(conn, stored_name, target_date, source))
                        print(f"  {' '.ljust(w)}  ↳ re-scraped: +{ins} new / ~{upd} changed  "
                              f"-> db now {after}, CSV rebuilt")
                        if after < declared:
                            still_short += 1
                    else:
                        still_short += 1

    print(f"\n  captchas solved this run: {stats.captcha_solves}   "
          f"throttle backoffs: {stats.throttle_events}")
    return still_short


def verify_day(target_date: date, *, source: str = "agmarknet_api",
               live: bool = False, fix: bool = False,
               only: list[str] | None = None, limit: int | None = None) -> int:
    print(f"\nVerify CSV / DB / site -- arrival_date {target_date}  (source={source})")
    o = _offline(target_date, source)
    _print_offline(o)

    exit_bad = 1 if o["flagged"] else 0
    if live or fix:
        still = _live(target_date, source, o, fix=fix, only=only, limit=limit)
        noun = "commodity" if still == 1 else "commodities"
        if fix:
            exit_bad = 1 if still else 0
            print("\n✓ nothing left short." if not still
                  else f"\n{still} {noun} still short -- see above.")
        else:
            exit_bad = exit_bad or (1 if still else 0)
            print(f"\n{still} {noun} the site is ahead on "
                  f"(run again with --fix to re-scrape).")
    print()
    return exit_bad
