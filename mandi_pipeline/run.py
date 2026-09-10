"""mandi_pipeline entry point.

  python -m mandi_pipeline.run --once                 # one sweep (resumes today)
  python -m mandi_pipeline.run --once --date 2026-09-03
  python -m mandi_pipeline.run --init-db              # apply db/schema.sql
  python -m mandi_pipeline.run --refresh-taxonomy     # re-pull commodity list
  python -m mandi_pipeline.run --dump-sample          # pin the /report row keys
  python -m mandi_pipeline.run --status               # recent runs + today's progress
  python -m mandi_pipeline.run --verify-csv --date 2026-09-06   # CSV files vs market_prices_v3

systemd runs `--once` every 4 hours (see deploy/). Each run only picks up
commodities still missing for the day, so overlapping/rapid re-runs are safe.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date

from loguru import logger

from .agmarknet.captcha import build_solver
from .agmarknet.client import AgmarknetClient
from .agmarknet import taxonomy
from .agmarknet.pipeline import APPROACH, run_sweep
from .config import settings
from .db import repository as repo
from .logging_setup import setup_logging


def _cmd_init_db() -> int:
    repo.apply_schema()
    logger.info("schema applied (mandi.market_prices_v3, pipeline_runs, pipeline_progress, agmarknet_taxonomy)")
    return 0


def _cmd_refresh_taxonomy() -> int:
    with repo.connect() as conn, AgmarknetClient() as client:
        rows = taxonomy.refresh(conn, client)
    logger.info(f"{len(rows)} commodities cached in mandi.agmarknet_taxonomy")
    return 0


def _cmd_dump_sample(cname_wanted: str | None) -> int:
    """Mint ONE CAPTCHA (via the configured solver), fetch ONE commodity,
    and print the real row keys so parser._CANDIDATES can be pinned."""
    with repo.connect() as conn, AgmarknetClient() as client:
        commodities = taxonomy.get_sweep_list(conn, client)
        pick = next(
            (c for c in commodities if cname_wanted and c["commodity_name"].lower() == cname_wanted.lower()),
            commodities[0],
        )
        logger.info(f"sample commodity: {pick['commodity_name']} (id={pick['commodity_id']})")
        solver = build_solver()
        challenge = client.new_captcha()
        answer = solver.solve(challenge, {"commodity_name": pick["commodity_name"],
                                          "commodity_id": pick["commodity_id"],
                                          "purpose": "dump-sample"})
        result = client.fetch_report(
            group_id=pick["group_id"], commodity_id=pick["commodity_id"],
            target_date=max(date.today(), settings.data_min_date),
            captcha_text=answer, captcha_key=challenge.key,
        )
    out = settings.state_dir / "sample_report.json"
    out.write_text(json.dumps({
        "commodity": pick,
        "total_count": result.total_count,
        "total_pages": result.total_pages,
        "row_count": len(result.rows),
        "first_row_keys": sorted(result.raw_first_row.keys()) if result.raw_first_row else [],
        "first_row": result.raw_first_row,
        "report_access": result.extra.get("report_access"),
    }, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    logger.info(f"wrote {out}")
    if result.raw_first_row:
        logger.info("FIRST ROW KEYS: " + ", ".join(sorted(result.raw_first_row.keys())))
    return 0


def _cmd_remirror_csv(target_date: date) -> int:
    from .storage import upload_csv

    day_dir = settings.csv_output_dir / target_date.isoformat()
    files = sorted(day_dir.glob("*.csv"))
    if not files:
        logger.warning(f"no local CSVs under {day_dir}")
        return 1
    ok = 0
    for f in files:
        if upload_csv(target_date, f):
            ok += 1
    logger.info(f"re-mirrored {ok}/{len(files)} CSVs for {target_date}")
    return 0 if ok == len(files) else 1


def _cmd_verify_csv(target_date: date | None, *, live: bool, fix: bool,
                    only: list[str] | None, limit: int | None) -> int:
    from .agmarknet.verify import verify_day

    tgt = target_date or max(date.today(), settings.data_min_date)
    return verify_day(tgt, live=live or fix, fix=fix, only=only, limit=limit)


def _cmd_status() -> int:
    with repo.connect() as conn:
        runs = repo.last_runs(conn, APPROACH, limit=8)
        today = max(date.today(), settings.data_min_date)
        prog = repo.load_progress(conn, APPROACH, today)
        rows_today = repo.count_rows_for_date(conn, today)
    print(f"\nRecent {APPROACH} runs:")
    for r in runs:
        print(f"  run{r['run_id']:<6} {str(r['started_at'])[:19]}  {r['status']:<14} "
              f"done={r['commodities_done']}/{r['commodities_total']} "
              f"rows+{r['rows_inserted']}/~{r['rows_updated']} "
              f"missing={r.get('rows_missing') or 0} "
              f"captchas={r['captcha_solves']} throttles={r['throttle_events']}"
              + (f"  ERR: {r['error'][:60]}" if r.get("error") else ""))
    done = sum(1 for s in prog.values() if s in ("done", "empty"))
    print(f"\nToday ({today}): {done} commodities recorded, {rows_today} rows in v3.\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    setup_logging()
    p = argparse.ArgumentParser(prog="mandi_pipeline")
    p.add_argument("--once", action="store_true", help="run one sweep and exit")
    p.add_argument("--date", type=date.fromisoformat, default=None, help="target arrival date (YYYY-MM-DD)")
    p.add_argument("--trigger", default="manual", choices=["timer", "manual", "catchup"])
    p.add_argument("--refresh-taxonomy-first", action="store_true", help="re-pull commodity list before the sweep")
    p.add_argument("--init-db", action="store_true")
    p.add_argument("--refresh-taxonomy", action="store_true")
    p.add_argument("--dump-sample", action="store_true")
    p.add_argument("--sample-commodity", default="Wheat")
    p.add_argument("--status", action="store_true")
    p.add_argument("--verify-csv", action="store_true",
                   help="reconcile the per-commodity CSV files under CSV_OUTPUT_DIR/<date>/ "
                        "against market_prices_v3 for --date (default: today); exit 1 on any mismatch")
    p.add_argument("--live", action="store_true",
                   help="with --verify-csv: also pull the live AGMARKNET /report for each "
                        "short commodity (1 CAPTCHA each) and diff the site against the DB (dry-run)")
    p.add_argument("--fix", action="store_true",
                   help="with --verify-csv: like --live, but re-scrape + upsert + rebuild the CSV "
                        "for every commodity the site is ahead on (holds the sweep lock)")
    p.add_argument("--ship-logs", action="store_true",
                   help="upload rotated scrape-log archives to blob (BLOB_PREFIX/logs/<YYYY-MM>/) and prune local")
    p.add_argument("--remirror-csv", metavar="YYYY-MM-DD", default=None,
                   help="upload every local CSV under CSV_OUTPUT_DIR/<date>/ to Azure Blob")
    p.add_argument("--limit-commodities", type=int, default=None,
                   help="sweep only the first N commodities (testing)")
    p.add_argument("--only", default=None,
                   help="comma-separated commodity names to sweep (testing), e.g. --only Wheat,Onion")
    p.add_argument("--catchup-days", type=int, default=0, metavar="N",
                   help="also re-sweep the previous N arrival dates, newest first, to pick up "
                        "mandis that entered prices late (AGMARKNET data settles over 24-48h). "
                        "0 = target date only (default). The daily catch-up timer uses 3.")
    args = p.parse_args(argv)

    if args.init_db:
        return _cmd_init_db()
    if args.refresh_taxonomy:
        return _cmd_refresh_taxonomy()
    if args.dump_sample:
        return _cmd_dump_sample(args.sample_commodity)
    if args.status:
        return _cmd_status()
    if args.verify_csv:
        return _cmd_verify_csv(
            args.date, live=args.live, fix=args.fix,
            only=[s for s in (args.only or "").split(",") if s.strip()] or None,
            limit=args.limit_commodities,
        )
    if args.ship_logs:
        from .storage import ship_logs
        n = ship_logs()
        logger.info(f"shipped {n} log file(s) to blob")
        return 0
    if args.remirror_csv:
        return _cmd_remirror_csv(date.fromisoformat(args.remirror_csv))
    if args.once:
        stats = run_sweep(
            trigger=args.trigger,
            target_date=args.date,
            force_taxonomy=args.refresh_taxonomy_first,
            limit_commodities=args.limit_commodities,
            only=[s for s in (args.only or "").split(",") if s.strip()] or None,
            catchup_days=args.catchup_days,
        )
        # exit non-zero on a fully failed run so systemd/monitoring notices;
        # 'partial' is exit 0 (progress was made, next timer picks up the rest).
        return 1 if stats.status in ("failed", "aborted") else 0

    p.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
