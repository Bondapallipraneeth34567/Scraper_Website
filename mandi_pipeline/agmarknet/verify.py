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

  DEEP  (--deep, implies --live)  -- also reads OUR CSV file for each
    commodity and compares it to the site row-for-row AND cell-by-cell:
      only_site / only_csv   rows on one side but not the other
      field_diffs            matched rows whose commodity_group / min /
                             max / modal / price_unit / arrival_quantity /
                             arrival_unit disagree (site value vs csv value)
    Without --only it checks every commodity that has a CSV or DB rows for
    the day (one CAPTCHA each -- a full sweep's worth). With --fix, every
    commodity that differs is re-scraped and its CSV rebuilt.

    python -m mandi_pipeline.run --verify-csv --date 2026-09-06
    python -m mandi_pipeline.run --verify-csv --date 2026-09-06 --live
    python -m mandi_pipeline.run --verify-csv --date 2026-09-06 --fix
    python -m mandi_pipeline.run --verify-csv --date 2026-09-06 --deep --only Tomato,Onion
    python -m mandi_pipeline.run --verify-csv --date 2026-09-06 --deep --fix

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

# fields compared cell-by-cell in --deep (the key fields are already equal
# for a matched row). ("name", "num"|"str")
_CMP_FIELDS = (
    ("commodity_group", "str"),
    ("min_price", "num"),
    ("max_price", "num"),
    ("modal_price", "num"),
    ("price_unit", "str"),
    ("arrival_quantity", "num"),
    ("arrival_unit", "str"),
)

# CSV header text -> normalized-row key (inverse of csv_export._COLUMNS)
_CSV_HEADER_TO_KEY = {
    "Commodity Group": "commodity_group", "Commodity": "commodity",
    "Variety": "variety", "Grade": "grade", "Market": "market",
    "District": "district", "State/UT": "state", "Arrival Date": "arrival_date",
    "Min Price": "min_price", "Max Price": "max_price", "Modal Price": "modal_price",
    "Price Unit": "price_unit", "Arrival Quantity": "arrival_quantity",
    "Arrival Unit": "arrival_unit",
}

_norm_re = re.compile(r"[^a-z0-9]+")


def _norm(s: str) -> str:
    return _norm_re.sub("", (s or "").lower())


def _row_key(r: dict) -> tuple:
    return tuple((r.get(k) or "").strip() for k in _KEY)


def _as_num(v):
    if v in (None, "", "NR", "-"):
        return None
    try:
        return float(str(v).replace(",", "").strip())
    except ValueError:
        return None


def _field_equal(kind: str, a, b) -> bool:
    if kind == "num":
        na, nb = _as_num(a), _as_num(b)
        if na is None or nb is None:
            return na is None and nb is None
        return abs(na - nb) < 0.005
    return (str(a or "").strip().casefold()) == (str(b or "").strip().casefold())


def _read_our_csv(path) -> dict[tuple, dict]:
    """Our per-commodity CSV file -> {row_key: normalized-ish row dict},
    keyed the same way as the parsed site rows so the two can be joined."""
    out: dict[tuple, dict] = {}
    if not path.is_file():
        return out
    with path.open("r", newline="", encoding="utf-8") as fh:
        for raw in _csv.DictReader(fh):
            row = {key: (raw.get(hdr) or "").strip()
                   for hdr, key in _CSV_HEADER_TO_KEY.items()}
            out[_row_key(row)] = row
    return out


def _deep_diff(site_rows: list[dict], our_csv: dict[tuple, dict]) -> dict:
    """Cell-by-cell comparison of the live site rows against our CSV file.
    Returns {only_site, only_csv, field_diffs:[(key, field, site_val, csv_val)]}."""
    site = {_row_key(r): r for r in site_rows}
    only_site = sorted(set(site) - set(our_csv))
    only_csv = sorted(set(our_csv) - set(site))
    field_diffs: list[tuple] = []
    for k in sorted(set(site) & set(our_csv)):
        s, c = site[k], our_csv[k]
        for name, kind in _CMP_FIELDS:
            if not _field_equal(kind, s.get(name), c.get(name)):
                field_diffs.append((k, name, s.get(name), c.get(name)))
    return {"only_site": only_site, "only_csv": only_csv, "field_diffs": field_diffs}


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
        print("  OK: every commodity reconciles (csv == db).")
        return
    w = max(len(n) for n, *_ in flagged)
    print(f"\n  {len(flagged)} flagged:")
    print(f"    {'commodity'.ljust(w)}  {'csv':>6} {'db':>6} {'prog':>6}  {'status':<9} issue")
    print(f"    {'-' * w}  {'-' * 6} {'-' * 6} {'-' * 6}  {'-' * 9} {'-' * 20}")
    for name, c, d, pr, ps, verdict in flagged:
        print(f"    {name.ljust(w)}  {c:>6} {d:>6} {str(pr):>6}  {ps:<9} {verdict}")


def _live(target_date: date, source: str, o: dict, *, fix: bool, deep: bool,
          only: list[str] | None, limit: int | None) -> int:
    """Diff the live site against the DB (and, with deep=True, cell-by-cell
    against our CSV file) for the target commodities. Returns the count of
    commodities still discrepant after any --fix."""
    # local imports: only needed on the network path, and they pull in httpx
    from .client import AgmarknetClient
    from .captcha import build_solver
    from .csv_export import commodity_csv_path
    from .pipeline import SweepStats, probe_commodity, _store_commodity_rows, _single_instance_lock
    from . import taxonomy

    if only:
        targets = list(only)
    elif deep:
        # deep needs a site fetch per commodity; without an explicit list,
        # check every commodity that has either a CSV or DB rows for the day
        targets = sorted(set(o["csv_counts"]) | set(o["db_counts"]), key=str.lower)
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
                print("  !! a sweep is already running -- skipping --fix (try again later)")
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
                    print(f"  {name.ljust(w)}  XX fetch failed: {type(e).__name__}: {str(e)[:80]}")
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
                elif stale:
                    verdict = f"STALE {len(stale)} row(s) in db not on site"
                print(f"  {name.ljust(w)}  site={declared:<4} parsed={len(site_rows):<4} "
                      f"db={len(db_rows):<4} missing={len(missing):<3} stale={len(stale):<3}"
                      f"{drop_note}  {verdict}")

                deep_bad = False
                csv_n = only_csv_n = diff_n = None
                details: dict = {
                    "missing": ["/".join(k) for k in sorted(missing)[:50]],
                    "stale": ["/".join(k) for k in sorted(stale)[:50]],
                }
                if deep:
                    our_csv = _read_our_csv(commodity_csv_path(target_date, stored_name))
                    d = _deep_diff(site_rows, our_csv)
                    nd = len(d["field_diffs"])
                    csv_n, only_csv_n, diff_n = len(our_csv), len(d["only_csv"]), nd
                    details["only_csv"] = ["/".join(k) for k in d["only_csv"][:50]]
                    details["field_diffs"] = [
                        {"key": "/".join(k), "field": fld, "site": sv, "csv": cv}
                        for k, fld, sv, cv in d["field_diffs"][:100]]
                    deep_bad = bool(d["only_site"] or d["only_csv"] or nd)
                    if not our_csv:
                        print(f"  {' '.ljust(w)}  -> deep: no CSV file for this commodity")
                    elif not deep_bad:
                        print(f"  {' '.ljust(w)}  -> deep: CSV matches site exactly "
                              f"({len(our_csv)} rows, all fields)")
                    else:
                        print(f"  {' '.ljust(w)}  -> deep: only_site={len(d['only_site'])} "
                              f"only_csv={len(d['only_csv'])} field_diffs={nd}")
                        for k, fld, sv, cv in d["field_diffs"][:6]:
                            st, di, mk, vr, gr = k
                            print(f"  {' '.ljust(w)}      [{st}/{di}/{mk}/{vr}/{gr}] "
                                  f"{fld}: site={sv!r} csv={cv!r}")
                        if nd > 6:
                            print(f"  {' '.ljust(w)}      ... +{nd - 6} more field diffs")
                        for k in d["only_site"][:4]:
                            print(f"  {' '.ljust(w)}      only on site: {'/'.join(k)}")
                        for k in d["only_csv"][:4]:
                            print(f"  {' '.ljust(w)}      only in csv : {'/'.join(k)}")

                differs = bool(missing or stale or declared > len(db_rows) or deep_bad)
                fixed_now = False
                if differs:
                    if fix and site_rows:
                        # we have the full site response here (prep.raw rows,
                        # declared total) -- safe to mirror/prune stale rows
                        complete = declared <= prep.raw
                        pruned_before = stats.rows_pruned
                        ins, upd, unch = _store_commodity_rows(
                            conn, c["commodity_id"], stored_name, target_date, site_rows, stats,
                            complete=complete,
                        )
                        this_pruned = stats.rows_pruned - pruned_before
                        after = len(repo.rows_for_commodity_date(conn, stored_name, target_date, source))
                        pr = f" -{this_pruned} pruned" if this_pruned else ""
                        print(f"  {' '.ljust(w)}  -> re-scraped: +{ins} new / ~{upd} changed{pr}  "
                              f"-> db now {after}, CSV rebuilt")
                        if after != len(site_rows):
                            still_short += 1
                        else:
                            fixed_now = True
                            db_rows = repo.rows_for_commodity_date(conn, stored_name, target_date, source)
                    else:
                        still_short += 1

                try:
                    repo.save_comparison(
                        conn, target_date, stored_name,
                        site_declared=declared, site_parsed=len(site_rows), db_rows=len(db_rows),
                        csv_rows=csv_n, missing_in_db=len(missing), stale_in_db=len(stale),
                        only_in_csv=only_csv_n, field_diff_count=diff_n,
                        in_sync=(not differs) or fixed_now,
                        verdict=verdict if not fixed_now else f"fixed (was: {verdict})",
                        fixed=fixed_now, details=details,
                    )
                except Exception as e:  # noqa: BLE001 -- never fail a check over the dashboard cache
                    logger.warning(f"{name}: could not save comparison for the dashboard: {e}")

    print(f"\n  captchas solved this run: {stats.captcha_solves}   "
          f"throttle backoffs: {stats.throttle_events}")
    return still_short


def verify_day(target_date: date, *, source: str = "agmarknet_api",
               live: bool = False, fix: bool = False, deep: bool = False,
               only: list[str] | None = None, limit: int | None = None) -> int:
    print(f"\nVerify CSV / DB / site -- arrival_date {target_date}  (source={source})")
    o = _offline(target_date, source)
    _print_offline(o)

    exit_bad = 1 if o["flagged"] else 0
    if live or fix or deep:
        still = _live(target_date, source, o, fix=fix, deep=deep, only=only, limit=limit)
        noun = "commodity" if still == 1 else "commodities"
        what = "discrepant vs the site" if deep else "the site is ahead on"
        if fix:
            exit_bad = 1 if still else 0
            print("\nOK: nothing left discrepant." if not still
                  else f"\n{still} {noun} still discrepant -- see above.")
        else:
            exit_bad = exit_bad or (1 if still else 0)
            print(f"\n{still} {noun} {what} "
                  f"(run again with --fix to re-scrape).")
    print()
    return exit_bad
