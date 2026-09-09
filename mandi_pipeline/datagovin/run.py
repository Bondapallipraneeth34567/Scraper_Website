"""data.gov.in pipeline (Approach 2) entry point.

  python -m mandi_pipeline.datagovin.run --once            # one cycle
  python -m mandi_pipeline.datagovin.run --once --force    # ignore the "unchanged" watermark
  python -m mandi_pipeline.datagovin.run --peek            # just print resource freshness
  python -m mandi_pipeline.datagovin.run --status          # recent runs

systemd runs `--once` every 4 hours (deploy/mandi-pipeline-datagovin.timer).
"""
from __future__ import annotations

import argparse
import sys

from loguru import logger

from ..db import repository as repo
from ..logging_setup import setup_logging
from .client import DataGovInClient
from .pipeline import APPROACH, run_cycle


def _cmd_peek() -> int:
    with DataGovInClient() as c:
        m = c.peek()
    print(f"resource      : {m.status}")
    print(f"total rows    : {m.total}")
    print(f"updated epoch : {m.updated_epoch}")
    print(f"updated date  : {m.updated_date}")
    print(f"fields        : {[f.get('name') for f in m.fields]}")
    return 0


def _cmd_status() -> int:
    with repo.connect() as conn:
        runs = repo.last_runs(conn, APPROACH, limit=10)
        state = repo.get_source_state(conn, APPROACH)
        total = repo.count_rows_datagovin(conn)
    print(f"\nRecent {APPROACH} runs:")
    for r in runs:
        print(f"  run{r['run_id']:<6} {str(r['started_at'])[:19]}  {r['status']:<14} "
              f"seen={r['rows_seen']} +{r['rows_inserted']}/~{r['rows_updated']}/"
              f"={r['rows_unchanged']} miss={r['rows_missing']}"
              + (f"  ERR: {r['error'][:60]}" if r.get('error') else ""))
    if state:
        print(f"\nwatermark: upstream_updated={state['upstream_updated']} "
              f"({state['upstream_updated_at']})  rows_last_seen={state['rows_last_seen']}  "
              f"last_success={state['last_success_at']}")
    print(f"market_prices_v4 holds {total} rows.\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    setup_logging()
    p = argparse.ArgumentParser(prog="mandi_pipeline.datagovin")
    p.add_argument("--once", action="store_true", help="run one cycle and exit")
    p.add_argument("--force", action="store_true", help="fetch even if the upstream watermark is unchanged")
    p.add_argument("--trigger", default="manual", choices=["timer", "manual", "catchup"])
    p.add_argument("--peek", action="store_true", help="print resource freshness and exit")
    p.add_argument("--status", action="store_true")
    args = p.parse_args(argv)

    if args.peek:
        return _cmd_peek()
    if args.status:
        return _cmd_status()
    if args.once:
        stats = run_cycle(trigger=args.trigger, force=args.force)
        return 1 if stats.status in ("failed", "aborted") else 0

    p.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
