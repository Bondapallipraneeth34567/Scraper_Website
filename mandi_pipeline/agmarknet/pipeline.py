"""The AGMARKNET direct-API sweep.

One "sweep" = for the target date, for every commodity in the taxonomy
that is not already done for that date:
    mint CAPTCHA -> authorised human (or OCR) solves it -> /report ->
    parse rows -> upsert into mandi.market_prices_v3.

Everything is checkpointed in mandi.pipeline_progress, so:
  * a crash resumes where it stopped;
  * the 4-hourly timer re-runs this and only picks up commodities still
    missing / previously failed (it does not redo the day);
  * a single-instance lock stops two sweeps overlapping.

Rate limiting: the API throttle sends no headers, so a 429 / "too many
requests" body / hung socket are all treated the same -- exponential
backoff (BACKOFF_BASE_SEC, doubling, capped at BACKOFF_MAX_SEC), up to
MAX_RETRIES per call. Between commodities we always wait
REQUEST_MIN_INTERVAL_SEC (enforced in client._pace_report).
"""
from __future__ import annotations

import contextlib
import os
import time
from dataclasses import dataclass, asdict
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
from loguru import logger

from ..config import settings
from ..db import repository as repo
from . import taxonomy
from .captcha import CaptchaAborted, CaptchaUnreadable, ManualSolver, build_solver
from .csv_export import write_commodity_csv
from .client import (
    AgmarknetClient,
    CaptchaRequired,
    RateLimited,
    SchemaError,
    TransientError,
)
from .llm_classifier import classify
from .parser import normalize_rows

APPROACH = "agmarknet_api"
_IST = ZoneInfo("Asia/Kolkata")
_MAX_CAPTCHA_ATTEMPTS = 4          # fresh-image retries when a solve is rejected by the server
_LOCK_PATH = settings.state_dir / "pipeline.lock"


@dataclass
class SweepStats:
    run_id: int = 0
    target_date: str = ""
    commodities_total: int = 0
    commodities_done: int = 0
    commodities_failed: int = 0
    rows_seen: int = 0
    rows_inserted: int = 0
    rows_updated: int = 0
    rows_unchanged: int = 0
    # rows the API said exist for a commodity but we never stored: pages the
    # API promised then didn't return + rows the parser had to drop (no
    # usable price / missing key field). Surfaced in --status and pipeline_runs.
    rows_missing: int = 0
    # rows stored with >=1 of min/max/modal NULL (site reported a partial
    # price). Not a loss -- a faithful copy. Auditable.
    rows_partial: int = 0
    # stale rows deleted so v3 mirrors the site exactly (see repo.sync_commodity_date).
    rows_pruned: int = 0
    captcha_solves: int = 0
    throttle_events: int = 0
    status: str = "running"
    error: str | None = None


# --------------------------------------------------------------- single instance
@contextlib.contextmanager
def _single_instance_lock():
    """flock on POSIX; best-effort pidfile elsewhere (dev only)."""
    _LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    use_pidfile = False
    try:
        import fcntl
    except ImportError:
        fcntl = None  # type: ignore[assignment]
        use_pidfile = True

    if use_pidfile:
        # Windows dev box only: best-effort stale-pidfile check.
        prev = _LOCK_PATH.read_text().strip() if _LOCK_PATH.exists() else ""
        if prev and prev.isdigit():
            raise BlockingIOError(f"pidfile held by {prev}")

    fh = open(_LOCK_PATH, "w")
    try:
        if fcntl is not None:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)  # raises BlockingIOError if held
        fh.write(str(os.getpid()))
        fh.flush()
        yield
    finally:
        if fcntl is not None:
            with contextlib.suppress(Exception):
                fcntl.flock(fh, fcntl.LOCK_UN)
        fh.close()
        if use_pidfile:
            _LOCK_PATH.unlink(missing_ok=True)


# --------------------------------------------------------------- date resolution
def resolve_target_date(explicit: date | None) -> date:
    if explicit:
        if explicit < settings.data_min_date:
            raise ValueError(f"{explicit} is before AGMARKNET data start {settings.data_min_date}")
        return explicit
    today_ist = datetime.now(_IST).date()
    return max(today_ist, settings.data_min_date)


# --------------------------------------------------------------- backoff helper
def _sleep_backoff(attempt: int, stats: SweepStats, why: str) -> None:
    delay = min(settings.backoff_base_sec * (2 ** attempt), settings.backoff_max_sec)
    stats.throttle_events += 1
    logger.warning(f"{why} -> backoff {delay:.0f}s (attempt {attempt + 1}/{settings.max_retries})")
    time.sleep(delay)


# --------------------------------------------------------------- one commodity
def _process_commodity(
    client: AgmarknetClient,
    solver,
    conn,
    commodity: dict,
    target_date: date,
    stats: SweepStats,
) -> str:
    """Returns 'done' | 'empty' | 'failed'. Never raises for a single-commodity
    problem -- it records progress and returns 'failed' so the sweep continues."""
    cid = commodity["commodity_id"]
    gid = commodity["group_id"]
    cname = commodity["commodity_name"]
    gname = commodity.get("group_name")

    if gid is None:
        repo.record_progress(conn, APPROACH, target_date, cid, cname, "failed",
                             last_error="taxonomy row has no group_id")
        logger.error(f"✗ {cname}: no group_id in taxonomy -- refresh --refresh-taxonomy")
        return "failed"
    ctx = {"commodity_id": cid, "commodity_name": cname, "group_id": gid,
           "target_date": target_date.isoformat()}

    try:
        first = _solve_and_fetch(client, solver, cid, gid, cname, target_date, ctx, stats,
                                 _MAX_CAPTCHA_ATTEMPTS)
    except _CaptchaExhausted as e:
        fallback = _build_fallback_solver(solver)
        if fallback is None:
            repo.record_progress(conn, APPROACH, target_date, cid, cname, "failed", last_error=str(e))
            logger.error(f"✗ {cname}: {e}")
            return "failed"
        logger.warning(f"⚠️  {cname}: automated CAPTCHA solving exhausted ({e}) -> "
                       f"escalating to manual fallback (there is no audio alternative -- "
                       f"verified, AGMARKNET's CAPTCHA is image-only)")
        try:
            first = _solve_and_fetch(client, fallback, cid, gid, cname, target_date, ctx, stats, 1)
        except _CaptchaExhausted as e2:
            repo.record_progress(conn, APPROACH, target_date, cid, cname, "failed",
                                 last_error=f"manual fallback also failed: {e2}")
            logger.error(f"✗ {cname}: manual fallback also failed: {e2}")
            return "failed"
        except (SchemaError, RateLimited, TransientError) as e2:
            repo.record_progress(conn, APPROACH, target_date, cid, cname, "failed",
                                 last_error=f"manual fallback: {e2}")
            logger.error(f"✗ {cname}: manual fallback: {e2}")
            return "failed"
    except SchemaError as e:
        repo.record_progress(conn, APPROACH, target_date, cid, cname, "failed",
                             last_error=f"schema: {e}")
        logger.error(f"✗ {cname}: unrecognised /report response: {e}")
        return "failed"
    except (RateLimited, TransientError) as e:
        repo.record_progress(conn, APPROACH, target_date, cid, cname, "failed",
                             last_error=f"exhausted retries: {e}")
        logger.error(f"✗ {cname}: gave up after retries: {e}")
        return "failed"

    # --- success: page (usually 1), parse, upsert ---
    try:
        raw_rows = client.fetch_all_pages(
            first=first, group_id=gid, commodity_id=cid, target_date=target_date,
        )
    except (RateLimited, TransientError, SchemaError) as e:
        repo.record_progress(conn, APPROACH, target_date, cid, cname, "failed",
                             last_error=f"pagination failed: {e}")
        logger.error(f"✗ {cname}: pagination failed: {e}")
        return "failed"

    stats.rows_seen += len(raw_rows)
    rows, prep = normalize_rows(raw_rows, fallback_commodity=cname, fallback_group=gname)
    stats.rows_partial += prep.partial_price

    # Completeness check: the API tells us (total_count) how many rows exist
    # for this commodity/date. Anything we didn't fetch, plus anything the
    # parser had to drop, is genuine missing data -- count it and log it
    # rather than let it vanish.
    declared = first.total_count or len(raw_rows)
    shortfall = max(0, declared - len(raw_rows))
    if shortfall or prep.dropped:
        stats.rows_missing += shortfall + prep.dropped
        logger.warning(
            f"⚠️  {cname}: API declared {declared} rows, fetched {len(raw_rows)}, "
            f"stored {len(rows)}  (short {shortfall} from paging, "
            f"dropped {prep.dropped} in parse {dict(prep.drop_reasons) or ''})"
        )

    if not rows:
        repo.record_progress(conn, APPROACH, target_date, cid, cname, "empty", 0)
        logger.info(f"·  {cname}: no rows for {target_date} (recorded as empty)")
        return "empty"

    # A fetch is "complete" -- safe to mirror/prune against -- only when we
    # retrieved every row the API declared and the parser dropped nothing
    # for a transport/schema reason (unmappable rows can't be mirrored
    # either way, so a 'no_price'/'missing_key_field' drop doesn't block it).
    transport_drops = sum(v for k, v in prep.drop_reasons.items()
                          if k in ("not_a_dict", "unparseable_arrival_date"))
    complete = shortfall == 0 and transport_drops == 0

    ins, upd, unch = _store_commodity_rows(conn, cid, cname, target_date, rows, stats,
                                           complete=complete)
    logger.info(f"✓  {cname}: {len(rows)} rows this sweep  (+{ins} new / ~{upd} changed / ={unch} same)")
    return "done"


def _store_commodity_rows(conn, cid: int, cname: str, target_date: date,
                          rows: list[dict], stats: SweepStats, *,
                          complete: bool) -> tuple[int, int, int]:
    """Reconcile one commodity's rows into market_prices_v3, record progress,
    and rewrite its CSV from the DB. Shared by the sweep and by verify.py's
    --fix path.

    When `complete` (we have every row the site declared) and
    MIRROR_PRUNE_STALE is on, stale rows for this commodity/date that the
    site no longer returns are DELETED, so v3 -- and the CSV built from it
    -- is a faithful mirror of the site, not an ever-growing superset. The
    CSV is written from v3 *after* the reconcile, so it always matches. A
    CSV failure must not fail the committed DB write."""
    prune = complete and settings.mirror_prune_stale
    stored_name = rows[0]["commodity"]
    ins, upd, unch, pruned = repo.sync_commodity_date(
        conn, rows, commodity=stored_name, arrival_date=target_date, prune=prune,
        max_prune_ratio=settings.mirror_max_prune_ratio, prune_floor=settings.mirror_prune_floor,
    )
    stats.rows_inserted += ins
    stats.rows_updated += upd
    stats.rows_unchanged += unch
    stats.rows_pruned += pruned
    repo.record_progress(conn, APPROACH, target_date, cid, cname, "done", rows_upserted=len(rows))
    if pruned:
        logger.info(f"·  {cname}: pruned {pruned} stale row(s) to mirror the site")

    try:
        csv_rows = repo.rows_for_commodity_date(conn, stored_name, target_date) or rows
        write_commodity_csv(target_date, stored_name, csv_rows)
    except Exception as e:
        logger.warning(f"{cname}: CSV write failed (rows still upserted): {e}")
    return ins, upd, unch


def probe_commodity(client: AgmarknetClient, solver, cid: int, gid: int, cname: str,
                    target_date: date, stats: SweepStats) -> tuple[int, list[dict], object]:
    """Fetch + parse ONE commodity WITHOUT writing anything. Returns
    (declared_count, normalized_rows, ParseReport). Raises _CaptchaExhausted
    / SchemaError / RateLimited / TransientError on an unrecoverable fetch.
    Used by --verify-csv --live to compare the site against what we stored."""
    ctx = {"commodity_id": cid, "commodity_name": cname, "group_id": gid,
           "target_date": target_date.isoformat(), "purpose": "verify"}
    first = _solve_and_fetch(client, solver, cid, gid, cname, target_date, ctx, stats,
                             _MAX_CAPTCHA_ATTEMPTS)
    raw_rows = client.fetch_all_pages(
        first=first, group_id=gid, commodity_id=cid, target_date=target_date,
    )
    rows, prep = normalize_rows(raw_rows, fallback_commodity=cname, fallback_group=None)
    return (first.total_count or len(raw_rows)), rows, prep


class _CaptchaExhausted(Exception):
    """Every attempt with one solver failed to get an accepted CAPTCHA."""


def _solve_and_fetch(client, solver, cid, gid, cname, target_date, ctx, stats, max_attempts):
    """Mint + solve + submit, up to max_attempts times. Returns the first
    successful ReportResult. Raises _CaptchaExhausted if every attempt with
    this solver was rejected/unreadable/aborted; propagates SchemaError /
    RateLimited / TransientError from a non-CAPTCHA failure so the caller
    can record the real reason."""
    for attempt in range(max_attempts):
        try:
            challenge = _retry_call(lambda: client.new_captcha(), stats, f"captcha/generator [{cname}]")
        except Exception as e:
            raise _CaptchaExhausted(f"could not obtain a CAPTCHA image: {e}") from e

        try:
            answer = solver.solve(challenge, ctx)
        except CaptchaUnreadable:
            logger.info(f"{cname}: {type(solver).__name__} says image unreadable -> new image")
            continue
        except CaptchaAborted as e:
            raise _CaptchaExhausted(f"captcha skipped: {e}") from e

        stats.captcha_solves += 1

        try:
            return _retry_call(
                lambda: client.fetch_report(
                    group_id=gid, commodity_id=cid, target_date=target_date,
                    captcha_text=answer, captcha_key=challenge.key,
                ),
                stats, f"/report [{cname}]",
            )
        except CaptchaRequired as e:
            logger.warning(f"{cname}: server rejected CAPTCHA ({e}) -> retry "
                           f"{attempt + 1}/{max_attempts}  [{type(solver).__name__}]")
            continue
        except SchemaError as e:
            verdict = classify(str(e), 200)
            if verdict == "captcha_required":
                continue
            if verdict == "rate_limited":
                _sleep_backoff(0, stats, f"{cname}: LLM says rate-limited")
                continue
            raise

    raise _CaptchaExhausted(f"CAPTCHA not solved by {type(solver).__name__} in {max_attempts} tries")


def _build_fallback_solver(primary: "CaptchaSolver"):
    """The backup path when the configured (automated) solver exhausts its
    attempts. AGMARKNET's CAPTCHA has no audio alternative (verified:
    type=audio/format=audio params are ignored, /captcha/audio -> 404,
    /captcha/generator/audio -> 404) -- the only real backup is a human.
    Returns None if the primary solver already IS the manual one, or if the
    fallback is disabled."""
    if not settings.captcha_manual_fallback:
        return None
    if isinstance(primary, ManualSolver):
        return None  # already manual -- no further fallback
    return ManualSolver()


def _retry_call(fn, stats: SweepStats, label: str):
    """Run fn(); on RateLimited/TransientError back off and retry up to
    MAX_RETRIES. CaptchaRequired / SchemaError propagate immediately."""
    last: Exception | None = None
    for attempt in range(settings.max_retries):
        try:
            return fn()
        except (RateLimited, TransientError) as e:
            last = e
            _sleep_backoff(attempt, stats, f"{label}: {type(e).__name__}")
    raise last  # type: ignore[misc]


# --------------------------------------------------------------------- the sweep
def run_sweep(
    *,
    trigger: str = "timer",
    target_date: date | None = None,
    force_taxonomy: bool = False,
    limit_commodities: int | None = None,
    only: list[str] | None = None,
    catchup_days: int = 0,
) -> SweepStats:
    stats = SweepStats()
    try:
        with _single_instance_lock():
            return _run_sweep_locked(stats, trigger, target_date, force_taxonomy,
                                     limit_commodities, only, catchup_days)
    except BlockingIOError:
        logger.warning("another sweep is already running -- exiting (this is normal for the 4h timer)")
        with repo.connect() as conn:
            rid = repo.start_run(conn, APPROACH, trigger, target_date)
            repo.finish_run(conn, rid, "skipped_locked")
        stats.status = "skipped_locked"
        return stats


def _sweep_dates(tgt: date, catchup_days: int) -> list[date]:
    """The target date plus the previous `catchup_days` arrival dates, newest
    first, clamped to the AGMARKNET data-start date. catchup_days=0 -> [tgt]."""
    out: list[date] = []
    for i in range(max(catchup_days, 0) + 1):
        d = tgt - timedelta(days=i)
        if d >= settings.data_min_date:
            out.append(d)
    return out


def _sweep_one_date(bound, conn, client, solver, commodities: list[dict],
                    tgt: date, stats: SweepStats) -> None:
    """Resume + sweep one arrival date over the already-filtered commodity
    list. Accumulates into `stats` (counts are cumulative across dates)."""
    progress = repo.load_progress(
        conn, APPROACH, tgt,
        refresh_after_hours=settings.progress_refresh_after_hours,
    )
    todo = [c for c in commodities if progress.get(c["commodity_id"]) not in ("done", "empty")]
    already = len(commodities) - len(todo)
    bound.info(f"[{tgt}] {len(commodities)} commodities, {already} already done, {len(todo)} to do")

    for pass_no in range(1, settings.sweep_passes + 1):
        if not todo:
            break
        bound.info(f"[{tgt}] — pass {pass_no}/{settings.sweep_passes}: {len(todo)} commodities")
        still: list[dict] = []
        for c in todo:
            outcome = _process_commodity(client, solver, conn, c, tgt, stats)
            if outcome in ("done", "empty"):
                stats.commodities_done += 1
            else:
                still.append(c)
        todo = still
        if todo:
            bound.info(f"[{tgt}] — pass {pass_no} done: {len(todo)} still missing")

    stats.commodities_failed += len(todo)


def _run_sweep_locked(stats, trigger, target_date, force_taxonomy,
                      limit_commodities=None, only=None, catchup_days=0) -> SweepStats:
    tgt = resolve_target_date(target_date)
    dates = _sweep_dates(tgt, catchup_days)
    stats.target_date = ",".join(d.isoformat() for d in dates)

    with repo.connect() as conn:
        # We hold the single-instance lock, so any 'running' row is a zombie
        # from a previous sweep that was killed before finish_run.
        cleaned = repo.abort_stale_runs(conn, APPROACH)
        if cleaned:
            logger.info(f"marked {cleaned} stale 'running' run row(s) as aborted")

        run_id = repo.start_run(conn, APPROACH, trigger, tgt)
        stats.run_id = run_id
        bound = logger.bind(run_id=f"run{run_id}")
        span = str(tgt) if len(dates) == 1 else f"{dates[-1]}..{tgt} ({len(dates)} days)"
        bound.info(f"▶  sweep start  dates={span}  trigger={trigger}")

        try:
            with AgmarknetClient() as client:
                solver = build_solver()
                bound.info(f"CAPTCHA solver: {settings.captcha_solver}")

                commodities = taxonomy.get_sweep_list(conn, client, force_refresh=force_taxonomy)
                if only:
                    wanted = {n.strip().lower() for n in only}
                    commodities = [c for c in commodities if c["commodity_name"].lower() in wanted]
                    bound.info(f"--only filter -> {len(commodities)} commodities: "
                               f"{[c['commodity_name'] for c in commodities]}")
                if limit_commodities:
                    commodities = commodities[:limit_commodities]
                    bound.info(f"--limit-commodities {limit_commodities} -> sweeping "
                               f"{len(commodities)} of the taxonomy")

                # Drop commodities that have been "No data available" on every
                # sweep for the last STALE_EMPTY_WINDOW_DAYS -- they cost a
                # CAPTCHA + a call each for nothing. Each is still re-checked
                # once every STALE_EMPTY_RECHECK_DAYS. Skipped when the caller
                # named commodities explicitly (--only).
                if settings.stale_empty_skip and not only:
                    dormant = repo.dormant_empty_commodity_ids(
                        conn, APPROACH,
                        window_days=settings.stale_empty_window_days,
                        min_obs=settings.stale_empty_min_observations,
                        recheck_days=settings.stale_empty_recheck_days,
                    )
                    if dormant:
                        commodities = [c for c in commodities if c["commodity_id"] not in dormant]
                        bound.info(f"skipping {len(dormant)} dormant (always-empty for "
                                   f"{settings.stale_empty_window_days}d) commodities -- "
                                   f"each re-checked every {settings.stale_empty_recheck_days}d")

                stats.commodities_total = len(commodities) * len(dates)
                if settings.progress_refresh_after_hours:
                    bound.info(f"re-sweeping commodities finished > "
                               f"{settings.progress_refresh_after_hours}h ago for late arrivals")

                # Newest date first: today's data is the priority; the older
                # catch-up dates mop up mandis that reported late. Each date is
                # independently checkpointed in pipeline_progress.
                for dt in dates:
                    _sweep_one_date(bound, conn, client, solver, commodities, dt, stats)

            if stats.commodities_failed == 0:
                stats.status = "success"
            elif stats.commodities_done > 0:
                stats.status = "partial"
            else:
                stats.status = "failed"

        except Exception as e:  # noqa: BLE001 -- top-level guard, always log the run
            stats.status = "failed"
            stats.error = f"{type(e).__name__}: {e}"
            bound.opt(exception=True).error(f"sweep aborted: {e}")

        repo.finish_run(conn, run_id, stats.status, error=stats.error, **{
            k: v for k, v in asdict(stats).items()
            if k in {
                "commodities_total", "commodities_done", "commodities_failed",
                "rows_seen", "rows_inserted", "rows_updated", "rows_unchanged",
                "rows_missing", "rows_pruned", "captcha_solves", "throttle_events",
            }
        })
        holdings = "  ".join(f"{d}:{repo.count_rows_for_date(conn, d)}" for d in dates)
        bound.info(
            f"■  sweep {stats.status}  done={stats.commodities_done}/{stats.commodities_total} "
            f"failed={stats.commodities_failed}  rows +{stats.rows_inserted}/"
            f"~{stats.rows_updated}/={stats.rows_unchanged}  missing={stats.rows_missing} "
            f"pruned={stats.rows_pruned} partial={stats.rows_partial}  "
            f"captchas={stats.captcha_solves} throttles={stats.throttle_events}  "
            f"v3 holds  {holdings}"
        )

    _post_run_notify(stats)
    return stats


# --------------------------------------------------------------------- monitoring
def _post_run_notify(stats: SweepStats) -> None:
    if stats.status == "success" and settings.heartbeat_url:
        with contextlib.suppress(Exception):
            httpx.get(settings.heartbeat_url, timeout=10)
    if stats.status in ("failed", "partial", "aborted") and settings.alert_webhook_url:
        with contextlib.suppress(Exception):
            httpx.post(settings.alert_webhook_url, json={
                "event": "pipeline_run",
                "approach": APPROACH,
                **asdict(stats),
            }, timeout=10)
