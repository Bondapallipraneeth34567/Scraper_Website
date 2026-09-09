"""The data.gov.in cycle (Approach 2).

guard/start -> peek freshness -> (skip if unchanged) -> fetch all pages ->
snapshot to disk -> normalise -> diff vs existing -> incremental upsert ->
flag missing -> update watermark -> finish/notify.

Fast and cheap: one bulk paged pull of ~9.5k rows, no CAPTCHA, seconds not
hours. Safe to run every 4h; when the resource's `updated` epoch has not
moved it does the cheap peek and exits.
"""
from __future__ import annotations

import contextlib
import os
from dataclasses import asdict, dataclass

import httpx
from loguru import logger

from ..agmarknet.pipeline import _single_instance_lock  # reuse the flock helper
from ..config import settings
from ..db import repository as repo
from .client import AuthError, DataGovInClient, DataGovInError
from .parser import normalize_records
from .snapshot import save_snapshot

APPROACH = "data_gov_in"


@dataclass
class CycleStats:
    run_id: int = 0
    status: str = "running"
    rows_seen: int = 0          # records pulled from the API
    rows_inserted: int = 0      # NEW
    rows_updated: int = 0       # MODIFIED
    rows_unchanged: int = 0
    rows_missing: int = 0       # in a prior snapshot, absent now -> stale_at set
    window_shortfall: int = 0   # rows the 10k API window blocked (no usable filter)
    upstream_updated_at: str | None = None
    snapshot_path: str | None = None
    error: str | None = None


def run_cycle(*, trigger: str = "timer", force: bool = False) -> CycleStats:
    stats = CycleStats()
    try:
        with _single_instance_lock():   # NOTE: shares Approach 1's lock file
            return _run_locked(stats, trigger, force)
    except BlockingIOError:
        logger.warning("another mandi_pipeline sweep holds the lock -- exiting")
        with repo.connect() as conn:
            rid = repo.start_run(conn, APPROACH, trigger, None)
            repo.finish_run(conn, rid, "skipped_locked")
        stats.status = "skipped_locked"
        return stats


def _run_locked(stats: CycleStats, trigger: str, force: bool) -> CycleStats:
    with repo.connect() as conn:
        run_id = repo.start_run(conn, APPROACH, trigger, None)
        stats.run_id = run_id
        log = logger.bind(run_id=f"dgi{run_id}")
        log.info(f"▶  data.gov.in cycle start  trigger={trigger}  force={force}")

        try:
            with DataGovInClient() as client:
                # -- 2. peek freshness --
                meta = client.peek()
                stats.upstream_updated_at = meta.updated_date
                log.info(f"resource: total={meta.total}  updated={meta.updated_epoch} "
                         f"({meta.updated_date})")

                prev = repo.get_source_state(conn, APPROACH)
                if (not force and settings.datagovin_skip_if_unchanged and prev
                        and prev.get("upstream_updated")
                        and meta.updated_epoch is not None
                        and int(prev["upstream_updated"]) == int(meta.updated_epoch)):
                    log.info("upstream `updated` unchanged since last success -- nothing to do")
                    stats.status = "unchanged"
                    repo.finish_run(conn, run_id, "unchanged",
                                    **_counts(stats))
                    _notify(stats)
                    return stats

                # -- 3. fetch all --
                meta, records = client.fetch_all()
                stats.rows_seen = len(records)
                stats.window_shortfall = getattr(meta, "window_shortfall", 0)

                # -- store the raw pull --
                snap = save_snapshot(meta, records)
                stats.snapshot_path = str(snap)

            # -- 4. normalise --
            rows = normalize_records(records)
            log.info(f"normalised {len(rows)} unique rows from {len(records)} records")

            # -- 5. diff vs existing --
            min_date = min((r["arrival_date"] for r in rows), default=None)
            existing = repo.load_row_hashes(conn, settings.datagovin_target_table,
                                            min_arrival_date=min_date)
            snap_keys = set()
            new_ct = mod_ct = same_ct = 0
            for r in rows:
                k = (r["state"], r["district"], r["market"], r["commodity"],
                     r["variety"], r["grade"], r["arrival_date"])
                snap_keys.add(k)
                if k not in existing:
                    new_ct += 1
                elif existing[k] != r["source_row_hash"]:
                    mod_ct += 1
                else:
                    same_ct += 1
            missing_keys = [k for k in existing.keys() if k not in snap_keys]
            log.info(f"diff: NEW={new_ct}  MODIFIED={mod_ct}  UNCHANGED={same_ct}  "
                     f"MISSING={len(missing_keys)}")

            # -- 6. apply --
            ins, upd, unch = repo.upsert_price_rows(
                conn, rows, table=settings.datagovin_target_table, reset_stale=True,
            )
            stats.rows_inserted, stats.rows_updated, stats.rows_unchanged = ins, upd, unch
            stats.rows_missing = repo.mark_stale(conn, settings.datagovin_target_table, missing_keys)

            if stats.window_shortfall > 0:
                log.warning(f"{stats.window_shortfall} rows were beyond the API's 10k window and "
                            f"not retrieved this cycle")

            # -- 7. watermark --
            repo.set_source_state(
                conn, APPROACH,
                resource_id=settings.datagovin_resource_id,
                upstream_updated=meta.updated_epoch,
                upstream_updated_at=meta.updated_date,
                rows_last_seen=meta.total or len(records),
                last_run_id=run_id,
            )
            stats.status = "partial" if stats.window_shortfall > 0 else "success"

        except AuthError as e:
            stats.status = "failed"
            stats.error = f"AuthError: {e}"
            log.error(f"cycle failed (auth): {e}")
        except (DataGovInError, httpx.HTTPError) as e:
            stats.status = "failed"
            stats.error = f"{type(e).__name__}: {e}"
            log.error(f"cycle failed: {e}")
        except Exception as e:  # noqa: BLE001 -- top-level guard
            stats.status = "failed"
            stats.error = f"{type(e).__name__}: {e}"
            log.opt(exception=True).error(f"cycle aborted: {e}")

        repo.finish_run(conn, run_id, stats.status, error=stats.error, **_counts(stats))
        db_total = repo.count_rows_datagovin(conn)
        log.info(f"■  cycle {stats.status}  seen={stats.rows_seen}  +{stats.rows_inserted} new / "
                 f"~{stats.rows_updated} changed / ={stats.rows_unchanged} same / "
                 f"{stats.rows_missing} newly-missing  |  table now holds {db_total} rows")

    _notify(stats)
    return stats


def _counts(stats: CycleStats) -> dict:
    return {k: v for k, v in asdict(stats).items()
            if k in {"rows_seen", "rows_inserted", "rows_updated", "rows_unchanged", "rows_missing"}}


def _notify(stats: CycleStats) -> None:
    if stats.status in ("success", "unchanged") and settings.heartbeat_url:
        with contextlib.suppress(Exception):
            httpx.get(settings.heartbeat_url, timeout=10)
    if stats.status in ("failed", "partial") and settings.alert_webhook_url:
        with contextlib.suppress(Exception):
            httpx.post(settings.alert_webhook_url,
                       json={"event": "pipeline_run", "approach": APPROACH, **asdict(stats)},
                       timeout=10)
