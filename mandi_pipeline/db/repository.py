"""All database access for the pipeline (sync psycopg 3).

Tables (see db/schema.sql):
  mandi.market_prices_v3     price rows written by this pipeline
  mandi.pipeline_runs        one row per execution (success or failure)
  mandi.pipeline_progress    per-commodity progress for a target_date sweep
  mandi.agmarknet_taxonomy   cached commodity list

Nothing here ever writes mandi.market_prices or mandi.market_prices_v2.
"""
from __future__ import annotations

import re
import socket
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Iterable, Iterator

import psycopg
from psycopg.rows import dict_row

from ..config import settings

_SCHEMA_SQL = Path(__file__).with_name("schema.sql")
_TABLE_RE = re.compile(r"^[a-z_][a-z0-9_]*\.[a-z_][a-z0-9_]*$")

PRICE_COLS = (
    "state", "district", "market", "commodity_group", "commodity", "variety", "grade",
    "arrival_date", "price_date", "min_price", "max_price", "modal_price", "price_unit",
    "arrival_quantity", "arrival_unit", "source", "source_row_hash",
)


def _safe_table(name: str) -> str:
    if not _TABLE_RE.match(name):
        raise ValueError(f"refusing to use unsafe table identifier: {name!r}")
    return name


@contextmanager
def connect() -> Iterator[psycopg.Connection]:
    conn = psycopg.connect(settings.dsn(), connect_timeout=15, application_name="mandi_pipeline")
    try:
        yield conn
    finally:
        conn.close()


# --------------------------------------------------------------------- schema
def apply_schema() -> None:
    sql = _SCHEMA_SQL.read_text(encoding="utf-8")
    with connect() as conn, conn.cursor() as cur:
        cur.execute(sql)
        conn.commit()


# ---------------------------------------------------------------- run logging
def abort_stale_runs(conn: psycopg.Connection, approach: str) -> int:
    """Mark any lingering status='running' rows for this approach as
    'aborted'. Safe to call right after acquiring the single-instance lock:
    holding the lock means no other run is legitimately in progress, so any
    'running' row is a zombie from a process killed before finish_run
    (systemd timeout, VM reboot, crash). Returns how many were cleaned."""
    with conn.cursor() as cur:
        cur.execute(
            """UPDATE mandi.pipeline_runs
                  SET status = 'aborted',
                      finished_at = (now() AT TIME ZONE 'utc'),
                      error = COALESCE(error, 'aborted: no finish_run (killed mid-sweep)')
                WHERE approach = %s AND status = 'running'""",
            (approach,),
        )
        n = cur.rowcount
    conn.commit()
    return n


def start_run(conn: psycopg.Connection, approach: str, trigger: str, target_date: date | None) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO mandi.pipeline_runs (approach, trigger, target_date, host, status)
               VALUES (%s, %s, %s, %s, 'running') RETURNING run_id""",
            (approach, trigger, target_date, socket.gethostname()[:120]),
        )
        run_id = cur.fetchone()[0]
    conn.commit()
    return run_id


def finish_run(conn: psycopg.Connection, run_id: int, status: str, *, error: str | None = None, **counts) -> None:
    fields = {
        "commodities_total", "commodities_done", "commodities_failed",
        "rows_seen", "rows_inserted", "rows_updated", "rows_unchanged",
        "rows_missing", "captcha_solves", "throttle_events",
    }
    sets = ", ".join(f"{k} = %s" for k in counts if k in fields)
    params = [counts[k] for k in counts if k in fields]
    extra = f", {sets}" if sets else ""
    with conn.cursor() as cur:
        cur.execute(
            f"""UPDATE mandi.pipeline_runs
                   SET status = %s, error = %s,
                       finished_at = (now() AT TIME ZONE 'utc'){extra}
                 WHERE run_id = %s""",
            [status, (error or None)[:8000] if error else None, *params, run_id],
        )
    conn.commit()


def last_runs(conn: psycopg.Connection, approach: str, limit: int = 10) -> list[dict]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """SELECT * FROM mandi.pipeline_runs
                WHERE approach = %s ORDER BY started_at DESC LIMIT %s""",
            (approach, limit),
        )
        return cur.fetchall()


# ------------------------------------------------------------------- progress
def load_progress(
    conn: psycopg.Connection, approach: str, target_date: date,
    refresh_after_hours: float = 0.0,
) -> dict[int, str]:
    """{commodity_id: status} for this sweep. 'done'/'empty' => skip.

    AGMARKNET keeps adding markets to a day's data through the day, so a
    commodity finished early can gain rows later. With refresh_after_hours
    > 0, a 'done'/'empty' row older than that is NOT returned here -> it
    gets re-swept, and the hash-based upsert writes only the genuinely new
    rows (older evening runs, once the data is stable, become no-ops)."""
    with conn.cursor() as cur:
        if refresh_after_hours and refresh_after_hours > 0:
            cur.execute(
                """SELECT commodity_id, status FROM mandi.pipeline_progress
                    WHERE approach = %s AND target_date = %s
                      AND NOT (status IN ('done', 'empty')
                               AND updated_at < (now() AT TIME ZONE 'utc')
                                                - make_interval(hours => 0, secs => %s))""",
                (approach, target_date, refresh_after_hours * 3600.0),
            )
        else:
            cur.execute(
                """SELECT commodity_id, status FROM mandi.pipeline_progress
                    WHERE approach = %s AND target_date = %s""",
                (approach, target_date),
            )
        return {cid: st for cid, st in cur.fetchall()}


def record_progress(
    conn: psycopg.Connection, approach: str, target_date: date,
    commodity_id: int, commodity_name: str, status: str,
    rows_upserted: int = 0, last_error: str | None = None,
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO mandi.pipeline_progress
                   (approach, target_date, commodity_id, commodity_name, status,
                    rows_upserted, attempts, last_error, updated_at)
               VALUES (%s, %s, %s, %s, %s, %s, 1, %s, (now() AT TIME ZONE 'utc'))
               ON CONFLICT (approach, target_date, commodity_id) DO UPDATE SET
                   status        = EXCLUDED.status,
                   commodity_name= EXCLUDED.commodity_name,
                   rows_upserted = EXCLUDED.rows_upserted,
                   attempts      = mandi.pipeline_progress.attempts + 1,
                   last_error    = EXCLUDED.last_error,
                   updated_at    = (now() AT TIME ZONE 'utc')""",
            (approach, target_date, commodity_id, commodity_name[:160], status,
             rows_upserted, (last_error or None)),
        )
    conn.commit()


def dormant_empty_commodity_ids(
    conn: psycopg.Connection, approach: str, *,
    window_days: int, min_obs: int, recheck_days: int,
) -> set[int]:
    """commodity_ids safe to SKIP this sweep because they never have data
    (~335/605 are livestock / wood / exotic that return "No data available"
    every day). A commodity qualifies when, over the last `window_days`:
      * every recorded sweep outcome was 'empty' (never done/failed),
      * with at least `min_obs` such sweeps,
      * it produced ZERO rows in the price table (the real evidence), and
      * it was last checked within `recheck_days` -- so a dormant commodity
        still gets one periodic re-check (it is NOT returned once overdue).
    """
    table = _safe_table(settings.target_table)
    with conn.cursor() as cur:
        cur.execute(
            f"""SELECT p.commodity_id
                  FROM mandi.pipeline_progress p
                 WHERE p.approach = %s
                   AND p.target_date >= (CURRENT_DATE - %s::int)
                 GROUP BY p.commodity_id, p.commodity_name
                HAVING bool_and(p.status = 'empty')
                   AND count(*) >= %s
                   AND max(p.updated_at) >= (now() AT TIME ZONE 'utc')
                                            - make_interval(days => %s)
                   AND NOT EXISTS (
                         SELECT 1 FROM {table} v
                          WHERE v.commodity = p.commodity_name
                            AND v.arrival_date >= (CURRENT_DATE - %s::int)
                   )""",
            (approach, window_days, min_obs, recheck_days, window_days),
        )
        return {r[0] for r in cur.fetchall()}


def clear_progress(conn: psycopg.Connection, approach: str, target_date: date) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM mandi.pipeline_progress WHERE approach = %s AND target_date = %s",
            (approach, target_date),
        )
    conn.commit()


# ------------------------------------------------------------------- taxonomy
def save_taxonomy(conn: psycopg.Connection, rows: Iterable[dict]) -> int:
    rows = list(rows)
    if not rows:
        return 0
    with conn.cursor() as cur:
        cur.execute("UPDATE mandi.agmarknet_taxonomy SET active = FALSE")
        cur.executemany(
            """INSERT INTO mandi.agmarknet_taxonomy
                   (commodity_id, commodity_name, group_id, group_name, active, refreshed_at)
               VALUES (%(commodity_id)s, %(commodity_name)s, %(group_id)s, %(group_name)s,
                       TRUE, (now() AT TIME ZONE 'utc'))
               ON CONFLICT (commodity_id) DO UPDATE SET
                   commodity_name = EXCLUDED.commodity_name,
                   group_id       = EXCLUDED.group_id,
                   group_name     = EXCLUDED.group_name,
                   active         = TRUE,
                   refreshed_at   = (now() AT TIME ZONE 'utc')""",
            rows,
        )
    conn.commit()
    return len(rows)


def load_taxonomy(conn: psycopg.Connection, active_only: bool = True) -> list[dict]:
    q = "SELECT commodity_id, commodity_name, group_id, group_name FROM mandi.agmarknet_taxonomy"
    if active_only:
        q += " WHERE active = TRUE"
    q += " ORDER BY group_id NULLS LAST, commodity_id"
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(q)
        return cur.fetchall()


_UNIQUE_KEY_COLS = ("state", "district", "market", "commodity", "variety", "grade", "arrival_date")


# --------------------------------------------------------------- price upsert
def upsert_price_rows(
    conn: psycopg.Connection, rows: list[dict], *,
    table: str | None = None, reset_stale: bool = False,
) -> tuple[int, int, int]:
    """Batched INSERT ... ON CONFLICT DO UPDATE.

    The DO UPDATE has `WHERE target.source_row_hash IS DISTINCT FROM
    EXCLUDED.source_row_hash`, so a row whose business fields are unchanged
    is NOT rewritten (no duplicate, no pointless scraped_at churn). Returns
    (inserted, updated, unchanged). Callers must have de-duplicated `rows`
    on the unique key first -- Postgres rejects a batch that hits the same
    conflict target twice.

    table       defaults to settings.target_table (Approach 1's v3).
    reset_stale set stale_at = NULL on any row that reappears (Approach 2's
                market_prices_v4 table has that column).
    """
    table = _safe_table(table or settings.target_table)
    if not rows:
        return (0, 0, 0)

    ncol = len(PRICE_COLS)
    chunk = 400
    submitted = inserted = updated = 0
    stale_reset = ",\n                        stale_at = NULL" if reset_stale else ""

    with conn.cursor() as cur:
        for i in range(0, len(rows), chunk):
            batch = rows[i:i + chunk]
            submitted += len(batch)
            values_ph = ",".join("(" + ",".join(["%s"] * ncol) + ")" for _ in batch)
            params: list = [r[c] for r in batch for c in PRICE_COLS]
            cur.execute(
                f"""INSERT INTO {table} ({", ".join(PRICE_COLS)})
                    VALUES {values_ph}
                    ON CONFLICT (state, district, market, commodity, variety, grade, arrival_date)
                    DO UPDATE SET
                        commodity_group  = EXCLUDED.commodity_group,
                        price_date       = EXCLUDED.price_date,
                        min_price        = EXCLUDED.min_price,
                        max_price        = EXCLUDED.max_price,
                        modal_price      = EXCLUDED.modal_price,
                        price_unit       = EXCLUDED.price_unit,
                        arrival_quantity = EXCLUDED.arrival_quantity,
                        arrival_unit     = EXCLUDED.arrival_unit,
                        source           = EXCLUDED.source,
                        source_row_hash  = EXCLUDED.source_row_hash,
                        scraped_at       = (now() AT TIME ZONE 'utc'){stale_reset}
                    WHERE {table}.source_row_hash IS DISTINCT FROM EXCLUDED.source_row_hash
                    RETURNING (xmax::text = '0') AS was_insert""",
                params,
            )
            for (was_insert,) in cur.fetchall():
                if was_insert:
                    inserted += 1
                else:
                    updated += 1
        conn.commit()

    unchanged = submitted - inserted - updated
    return inserted, updated, unchanged


# ------------------------------------------------- diff helpers (Approach 2)
def load_row_hashes(
    conn: psycopg.Connection, table: str, *, min_arrival_date: date | None = None,
) -> dict[tuple, str]:
    """{unique_key_tuple: source_row_hash} for every row (optionally only
    those with arrival_date >= min_arrival_date). Used to classify a fresh
    snapshot into new / modified / unchanged / missing."""
    table = _safe_table(table)
    q = f"SELECT {', '.join(_UNIQUE_KEY_COLS)}, source_row_hash FROM {table}"
    params: list = []
    if min_arrival_date is not None:
        q += " WHERE arrival_date >= %s"
        params.append(min_arrival_date)
    with conn.cursor() as cur:
        cur.execute(q, params)
        return {tuple(r[:-1]): r[-1] for r in cur.fetchall()}


def mark_stale(conn: psycopg.Connection, table: str, keys: list[tuple]) -> int:
    """stale_at = now() for rows whose unique key is in `keys` and that are
    not already flagged. Returns the number newly flagged."""
    table = _safe_table(table)
    if not keys:
        return 0
    flagged = 0
    with conn.cursor() as cur:
        for i in range(0, len(keys), 500):
            chunk = keys[i:i + 500]
            ph = ",".join("(" + ",".join(["%s"] * len(_UNIQUE_KEY_COLS)) + ")" for _ in chunk)
            params = [v for k in chunk for v in k]
            cur.execute(
                f"""UPDATE {table} SET stale_at = (now() AT TIME ZONE 'utc')
                     WHERE ({', '.join(_UNIQUE_KEY_COLS)}) IN ({ph})
                       AND stale_at IS NULL""",
                params,
            )
            flagged += cur.rowcount
        conn.commit()
    return flagged


# ---------------------------------------------- source watermark (Approach 2)
def get_source_state(conn: psycopg.Connection, approach: str) -> dict | None:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT * FROM mandi.pipeline_source_state WHERE approach = %s", (approach,))
        return cur.fetchone()


def set_source_state(
    conn: psycopg.Connection, approach: str, *, resource_id: str,
    upstream_updated: int | None, upstream_updated_at: str | None,
    rows_last_seen: int, last_run_id: int,
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO mandi.pipeline_source_state
                   (approach, resource_id, upstream_updated, upstream_updated_at,
                    rows_last_seen, last_run_id, last_success_at)
               VALUES (%s, %s, %s, %s, %s, %s, (now() AT TIME ZONE 'utc'))
               ON CONFLICT (approach) DO UPDATE SET
                   resource_id        = EXCLUDED.resource_id,
                   upstream_updated   = EXCLUDED.upstream_updated,
                   upstream_updated_at= EXCLUDED.upstream_updated_at,
                   rows_last_seen     = EXCLUDED.rows_last_seen,
                   last_run_id        = EXCLUDED.last_run_id,
                   last_success_at    = (now() AT TIME ZONE 'utc')""",
            (approach, resource_id, upstream_updated,
             _parse_ts(upstream_updated_at), rows_last_seen, last_run_id),
        )
    conn.commit()


def _parse_ts(s: str | None):
    if not s:
        return None
    from datetime import datetime
    for f in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(s, f)
        except ValueError:
            continue
    return None


def count_rows_datagovin(conn: psycopg.Connection) -> int:
    table = _safe_table(settings.datagovin_target_table)
    with conn.cursor() as cur:
        cur.execute(f"SELECT count(*) FROM {table}")
        return cur.fetchone()[0]


def count_rows_for_date(conn: psycopg.Connection, target_date: date, source: str = "agmarknet_api") -> int:
    table = _safe_table(settings.target_table)
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT count(*) FROM {table} WHERE arrival_date = %s AND source = %s",
            (target_date, source),
        )
        return cur.fetchone()[0]


def commodities_present_for_date(conn: psycopg.Connection, target_date: date, source: str = "agmarknet_api") -> set[str]:
    table = _safe_table(settings.target_table)
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT DISTINCT commodity FROM {table} WHERE arrival_date = %s AND source = %s",
            (target_date, source),
        )
        return {r[0] for r in cur.fetchall()}


def commodity_row_counts_for_date(
    conn: psycopg.Connection, target_date: date, source: str = "agmarknet_api",
) -> dict[str, int]:
    """{commodity: row_count} in the price table for this arrival_date.
    Used by --verify-csv to reconcile the CSV artifacts against the DB."""
    table = _safe_table(settings.target_table)
    with conn.cursor() as cur:
        cur.execute(
            f"""SELECT commodity, count(*) FROM {table}
                 WHERE arrival_date = %s AND source = %s
                 GROUP BY commodity""",
            (target_date, source),
        )
        return {c: n for c, n in cur.fetchall()}


_CSV_ROW_COLS = (
    "commodity_group", "commodity", "variety", "grade", "market", "district", "state",
    "arrival_date", "min_price", "max_price", "modal_price", "price_unit",
    "arrival_quantity", "arrival_unit",
)


def rows_for_commodity_date(
    conn: psycopg.Connection, commodity: str, arrival_date: date, source: str = "agmarknet_api",
) -> list[dict]:
    """Every stored row for one (commodity, arrival_date), as dicts keyed for
    csv_export._COLUMNS. This is what the per-commodity CSV is written from --
    so the file is a COMPLETE extract for the day (all sweeps, incl. late
    arrivals) that always reconciles with the DB, not a single sweep's
    snapshot."""
    table = _safe_table(settings.target_table)
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            f"""SELECT {", ".join(_CSV_ROW_COLS)} FROM {table}
                 WHERE commodity = %s AND arrival_date = %s AND source = %s
                 ORDER BY state, district, market, variety, grade""",
            (commodity, arrival_date, source),
        )
        return cur.fetchall()


def progress_detail_for_date(
    conn: psycopg.Connection, approach: str, target_date: date,
) -> dict[str, dict]:
    """{commodity_name: {status, rows_upserted, attempts, last_error}} from
    mandi.pipeline_progress for this sweep. commodity_name is the taxonomy
    name, which can differ slightly from the price table's `commodity`."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """SELECT commodity_name, status, rows_upserted, attempts, last_error
                 FROM mandi.pipeline_progress
                WHERE approach = %s AND target_date = %s""",
            (approach, target_date),
        )
        return {r["commodity_name"]: r for r in cur.fetchall()}
