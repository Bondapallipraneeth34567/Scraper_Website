"""Single source of truth for configuration. Everything is read from the
environment (loaded from .env on the VM by python-dotenv). No secret or
endpoint is hard-coded in any other module -- they all import `settings`
from here.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

_ROOT = Path(__file__).resolve().parent

# Load mandi_pipeline/.env FIRST and let it win -- otherwise an unrelated
# .env in a parent directory (e.g. the bot's repo-root .env) would be
# picked up by a bare load_dotenv() and its DATABASE_URL would point us at
# the wrong database. Then fall back to the normal search for anything the
# pipeline .env doesn't set.
_PIPELINE_ENV = _ROOT / ".env"
if _PIPELINE_ENV.exists():
    # Authoritative: load ONLY this file. Do NOT also do the usual upward
    # search -- a parent directory's .env (the bot's repo-root .env) would
    # otherwise leak its DATABASE_URL and point us at the wrong database.
    load_dotenv(_PIPELINE_ENV, override=True)
else:
    load_dotenv()


def _b(name: str, default: bool) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def _i(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default


def _f(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default


def _s(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _path(name: str, default: str) -> Path:
    raw = _s(name) or default
    p = Path(raw)
    return p if p.is_absolute() else (_ROOT.parent / p)


@dataclass(frozen=True)
class Settings:
    # --- database ---
    # Accepts either PG_* or the dashboard backend's POSTGRES_* names, so a
    # mandi_pipeline/.env can be a straight copy of backend/.env's DB block.
    database_url: str = field(default_factory=lambda: _s("DATABASE_URL"))
    pg_host: str = field(default_factory=lambda: _s("PG_HOST") or _s("POSTGRES_HOST", "localhost"))
    pg_port: int = field(default_factory=lambda: _i("PG_PORT", 0) or _i("POSTGRES_PORT", 5432))
    pg_db: str = field(default_factory=lambda: _s("PG_DB") or _s("POSTGRES_DB", "postgres"))
    pg_user: str = field(default_factory=lambda: _s("PG_USER") or _s("POSTGRES_USER", "postgres"))
    pg_password: str = field(default_factory=lambda: _s("PG_PASSWORD") or _s("POSTGRES_PASSWORD"))
    pg_sslmode: str = field(default_factory=lambda: _s("PG_SSLMODE")
                            or ("require" if _b("POSTGRES_SSL", False) else "prefer"))
    target_table: str = field(default_factory=lambda: _s("TARGET_TABLE", "mandi.market_prices_v3"))

    # --- agmarknet API ---
    api_base: str = field(default_factory=lambda: _s("AGMARKNET_API_BASE", "https://api.agmarknet.gov.in/v1").rstrip("/"))
    site_origin: str = field(default_factory=lambda: _s("AGMARKNET_SITE_ORIGIN", "https://agmarknet.gov.in").rstrip("/"))
    user_agent: str = field(default_factory=lambda: _s(
        "AGMARKNET_USER_AGENT",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    ))
    data_min_date: date = field(default_factory=lambda: date.fromisoformat(_s("AGMARKNET_DATA_MIN_DATE", "2025-11-07")))

    # --- data.gov.in API (Approach 2) ---
    datagovin_api_key: str = field(default_factory=lambda: _s("DATA_GOV_IN_API_KEY") or _s("AGMARKNET_API_KEY"))
    datagovin_resource_id: str = field(default_factory=lambda: _s("DATA_GOV_IN_RESOURCE_ID", "9ef84268-d588-465a-a308-a864a43d0070"))
    datagovin_base: str = field(default_factory=lambda: _s("DATA_GOV_IN_BASE", "https://api.data.gov.in/resource").rstrip("/"))
    datagovin_page_size: int = field(default_factory=lambda: _i("DATA_GOV_IN_PAGE_SIZE", 1000))
    datagovin_target_table: str = field(default_factory=lambda: _s("DATA_GOV_IN_TARGET_TABLE", "mandi.market_prices_v4"))
    datagovin_skip_if_unchanged: bool = field(default_factory=lambda: _b("DATA_GOV_IN_SKIP_IF_UNCHANGED", True))
    datagovin_snapshot_dir: Path = field(default_factory=lambda: _path("DATA_GOV_IN_SNAPSHOT_DIR", "mandi_pipeline/state/datagovin/snapshots"))
    datagovin_snapshot_keep: int = field(default_factory=lambda: _i("DATA_GOV_IN_SNAPSHOT_KEEP", 30))
    datagovin_read_timeout_sec: float = field(default_factory=lambda: _f("DATA_GOV_IN_READ_TIMEOUT_SEC", 90.0))
    datagovin_min_interval_sec: float = field(default_factory=lambda: _f("DATA_GOV_IN_MIN_INTERVAL_SEC", 3.0))
    # data.gov.in / Elasticsearch rejects offset+limit > this
    datagovin_window: int = field(default_factory=lambda: _i("DATA_GOV_IN_WINDOW", 10000))

    def datagovin_resource_url(self) -> str:
        return f"{self.datagovin_base}/{self.datagovin_resource_id}"

    # --- politeness / retry ---
    request_min_interval_sec: float = field(default_factory=lambda: _f("REQUEST_MIN_INTERVAL_SEC", 8.0))
    http_connect_timeout_sec: float = field(default_factory=lambda: _f("HTTP_CONNECT_TIMEOUT_SEC", 10.0))
    http_read_timeout_sec: float = field(default_factory=lambda: _f("HTTP_READ_TIMEOUT_SEC", 45.0))
    max_retries: int = field(default_factory=lambda: _i("MAX_RETRIES", 4))
    backoff_base_sec: float = field(default_factory=lambda: _f("BACKOFF_BASE_SEC", 15.0))
    backoff_max_sec: float = field(default_factory=lambda: _f("BACKOFF_MAX_SEC", 300.0))
    sweep_passes: int = field(default_factory=lambda: _i("SWEEP_PASSES", 3))
    # AGMARKNET fills a day's markets in progressively. > 0 => a commodity
    # finished more than this many hours ago is re-swept on the next run to
    # pick up late arrivals. Set this ~= the timer interval (4h) so a
    # commodity is re-checked once per timer tick, NOT re-done mid-sweep
    # (which wastes a CAPTCHA for zero new rows). Raise it if sweeps run
    # longer than this -- watch rows_unchanged in pipeline_runs.
    progress_refresh_after_hours: float = field(default_factory=lambda: _f("PROGRESS_REFRESH_AFTER_HOURS", 4.0))

    # --- mirror the site exactly (100% accuracy) ---
    # After a COMPLETE, healthy fetch of a commodity for a date (every row
    # the API declared was retrieved), delete any market_prices_v3 rows for
    # that commodity+date whose unique key is no longer in the site's
    # response -- so v3 (and the CSV built from it) is a faithful mirror,
    # not an ever-growing superset that keeps stale/superseded rows.
    mirror_prune_stale: bool = field(default_factory=lambda: _b("MIRROR_PRUNE_STALE", True))
    # Safety brake: if a single reconcile would delete more than this
    # fraction of the commodity's existing rows for the date (and it has a
    # meaningful number to begin with), SKIP the prune and flag the run --
    # a sudden shrink is almost always an upstream glitch, not a real drop.
    mirror_max_prune_ratio: float = field(default_factory=lambda: _f("MIRROR_MAX_PRUNE_RATIO", 0.34))
    mirror_prune_floor: int = field(default_factory=lambda: _i("MIRROR_PRUNE_FLOOR", 15))

    # --- skip perpetually-empty commodities (livestock, wood, exotic
    #     flowers -- ~335/605 return "No data available" every day). Each
    #     still costs a CAPTCHA + a call, so we stop checking them daily. ---
    stale_empty_skip: bool = field(default_factory=lambda: _b("STALE_EMPTY_SKIP", True))
    stale_empty_window_days: int = field(default_factory=lambda: _i("STALE_EMPTY_WINDOW_DAYS", 10))
    stale_empty_min_observations: int = field(default_factory=lambda: _i("STALE_EMPTY_MIN_OBS", 2))
    stale_empty_recheck_days: int = field(default_factory=lambda: _i("STALE_EMPTY_RECHECK_DAYS", 7))

    # --- captcha ---
    # manual | easyocr | llm | llm_assisted_ocr   ("ocr" is an alias for easyocr)
    captcha_solver: str = field(default_factory=lambda: _s("CAPTCHA_SOLVER", "manual").lower())
    captcha_wait_timeout_sec: float = field(default_factory=lambda: _f("CAPTCHA_WAIT_TIMEOUT_SEC", 1800.0))
    captcha_state_dir: Path = field(default_factory=lambda: _path("CAPTCHA_STATE_DIR", "mandi_pipeline/state/captcha"))
    captcha_expected_len: int = field(default_factory=lambda: _i("CAPTCHA_EXPECTED_LEN", 6))
    # small floor between the previous /report and the next captcha mint, so
    # the two aren't fired back-to-back at the burst-sensitive edge limiter
    captcha_min_gap_sec: float = field(default_factory=lambda: _f("CAPTCHA_MIN_GAP_SEC", 3.0))
    # min per-character softmax confidence for CAPTCHA_SOLVER=trained to
    # accept its own read (below this it raises -> fresh image / fallback)
    captcha_trained_min_conf: float = field(default_factory=lambda: _f("CAPTCHA_TRAINED_MIN_CONF", 0.55))
    captcha_local_attempts: int = field(default_factory=lambda: _i("CAPTCHA_LOCAL_ATTEMPTS", 2))
    captcha_llm_model: str = field(default_factory=lambda: _s("CAPTCHA_LLM_MODEL", "") or _s("LLM_MODEL", "claude-sonnet-5"))
    captcha_debug: bool = field(default_factory=lambda: _b("CAPTCHA_DEBUG", False))
    # AGMARKNET's CAPTCHA has no audio alternative (verified -- see
    # pipeline._build_fallback_solver). When an automated solver
    # (easyocr/llm/llm_assisted_ocr) exhausts _MAX_CAPTCHA_ATTEMPTS on a
    # commodity, this escalates to ONE manual (human) attempt before giving
    # up, instead of marking the commodity failed outright.
    captcha_manual_fallback: bool = field(default_factory=lambda: _b("CAPTCHA_MANUAL_FALLBACK", True))
    operator_console_host: str = field(default_factory=lambda: _s("OPERATOR_CONSOLE_HOST", "127.0.0.1"))
    operator_console_port: int = field(default_factory=lambda: _i("OPERATOR_CONSOLE_PORT", 8770))
    operator_console_token: str = field(default_factory=lambda: _s("OPERATOR_CONSOLE_TOKEN"))

    # --- llm classifier ---
    llm_classifier_enabled: bool = field(default_factory=lambda: _b("LLM_CLASSIFIER_ENABLED", False))
    anthropic_api_key: str = field(default_factory=lambda: _s("ANTHROPIC_API_KEY"))
    llm_model: str = field(default_factory=lambda: _s("LLM_MODEL", "claude-sonnet-5"))

    # --- CSV output (one file per commodity per day, like the old scraper's
    #     "Download CSV" archive) ---
    csv_output_enabled: bool = field(default_factory=lambda: _b("CSV_OUTPUT_ENABLED", True))
    csv_output_dir: Path = field(default_factory=lambda: _path("CSV_OUTPUT_DIR", "mandi_pipeline/state/downloads"))
    csv_local_keep: bool = field(default_factory=lambda: _b("CSV_LOCAL_KEEP", True))
    # OFF by default: store min/max/modal EXACTLY as AGMARKNET reports them,
    # leaving a blank price column NULL. ON = fill a missing min/max/modal
    # from whichever of the three was reported (a convenience, not the truth).
    csv_backfill_partial_prices: bool = field(default_factory=lambda: _b("CSV_BACKFILL_PARTIAL_PRICES", False))

    # --- Azure Blob mirror for the CSVs ---
    azure_storage_connection_string: str = field(default_factory=lambda: _s("AZURE_STORAGE_CONNECTION_STRING"))
    azure_storage_container: str = field(default_factory=lambda: _s("AZURE_STORAGE_CONTAINER"))
    blob_prefix: str = field(default_factory=lambda: _s("BLOB_PREFIX", "agmarknet_api/"))
    blob_upload_enabled: bool = field(
        default_factory=lambda: _b("BLOB_UPLOAD_ENABLED", bool(_s("AZURE_STORAGE_CONNECTION_STRING")))
    )

    # --- monitoring ---
    log_dir: Path = field(default_factory=lambda: _path("LOG_DIR", "mandi_pipeline/state/logs"))
    log_level: str = field(default_factory=lambda: _s("LOG_LEVEL", "INFO").upper())
    heartbeat_url: str = field(default_factory=lambda: _s("HEARTBEAT_URL"))
    alert_webhook_url: str = field(default_factory=lambda: _s("ALERT_WEBHOOK_URL"))

    # --- runtime state ---
    state_dir: Path = field(default_factory=lambda: _path("STATE_DIR", "mandi_pipeline/state"))

    def dsn(self) -> str:
        """psycopg connection string. DATABASE_URL wins; else assemble."""
        if self.database_url:
            return self.database_url
        return (
            f"host={self.pg_host} port={self.pg_port} dbname={self.pg_db} "
            f"user={self.pg_user} password={self.pg_password} sslmode={self.pg_sslmode}"
        )

    def report_url(self) -> str:
        return f"{self.api_base}/daily-price-arrival/report"

    def filters_url(self) -> str:
        return f"{self.api_base}/daily-price-arrival/filters"

    def captcha_url(self) -> str:
        return f"{self.api_base}/captcha/generator"

    def base_headers(self) -> dict[str, str]:
        # Exactly the headers the AGMARKNET web app sends for these calls
        # (confirmed from the site's own network traffic in the 2026-09-03
        # investigation). No cookies are required.
        return {
            "User-Agent": self.user_agent,
            "Origin": self.site_origin,
            "Referer": self.site_origin + "/",
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
        }


settings = Settings()

# Ensure runtime dirs exist so nothing has to mkdir defensively later.
for _d in (settings.state_dir, settings.log_dir, settings.captcha_state_dir,
           settings.csv_output_dir, settings.datagovin_snapshot_dir):
    _d.mkdir(parents=True, exist_ok=True)
