"""Azure Blob mirror for the per-commodity CSVs -- same idea as the
existing browser scraper (scraper_csv/agmarknet_scraper.py): the local
file under CSV_OUTPUT_DIR is the staging copy, the blob is the durable
one.

Blob layout:  {BLOB_PREFIX}{YYYY-MM-DD}/{Commodity}.csv
BLOB_PREFIX defaults to "agmarknet_api/" so these do NOT collide with the
browser scraper's own <date>/<commodity>.csv objects in the same
container.

Best-effort: a failed upload is logged and returns False -- it never
raises, because the DB write and the local CSV have already succeeded and
a blob hiccup must not fail the commodity.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

from loguru import logger

from .config import settings

_container = None
_disabled_reason: str | None = None


def _get_container():
    global _container, _disabled_reason
    if _container is not None:
        return _container
    if _disabled_reason:
        return None
    if not settings.blob_upload_enabled:
        _disabled_reason = "BLOB_UPLOAD_ENABLED is false"
        return None
    if not settings.azure_storage_connection_string or not settings.azure_storage_container:
        _disabled_reason = "AZURE_STORAGE_CONNECTION_STRING / AZURE_STORAGE_CONTAINER not set"
        logger.warning(f"blob mirror disabled: {_disabled_reason}")
        return None
    try:
        from azure.storage.blob import ContainerClient
    except ImportError:
        _disabled_reason = "azure-storage-blob not installed (`pip install azure-storage-blob`)"
        logger.warning(f"blob mirror disabled: {_disabled_reason}")
        return None
    try:
        _container = ContainerClient.from_connection_string(
            settings.azure_storage_connection_string,
            container_name=settings.azure_storage_container,
        )
    except Exception as e:  # noqa: BLE001
        _disabled_reason = f"could not build ContainerClient: {e}"
        logger.warning(f"blob mirror disabled: {_disabled_reason}")
        return None
    return _container


def blob_name_for(target_date: date, csv_path: Path) -> str:
    prefix = settings.blob_prefix
    if prefix and not prefix.endswith("/"):
        prefix += "/"
    return f"{prefix}{target_date.isoformat()}/{csv_path.name}"


def upload_csv(target_date: date, csv_path: Path) -> bool:
    """Mirror one CSV to blob. Returns True only on a confirmed upload."""
    container = _get_container()
    if container is None:
        return False
    name = blob_name_for(target_date, csv_path)
    try:
        with open(csv_path, "rb") as f:
            container.upload_blob(name, f, overwrite=True)
        logger.info(f"☁  mirrored -> blob {settings.azure_storage_container}/{name}")
        return True
    except Exception:  # noqa: BLE001
        logger.opt(exception=True).warning(
            f"⚠️  blob upload failed for {name} -- local copy kept as the only copy"
        )
        return False


def ship_logs() -> int:
    """Ship the pipeline's own scrape logs to blob, in their OWN folder
    ({BLOB_PREFIX}logs/<YYYY-MM>/), then prune what was uploaded from local
    disk -- keeps the VM from filling up with log history.

      * rotated loguru archives (state/logs/*.zip)  -> uploaded, then DELETED locally
      * current pipeline.log / pipeline.jsonl        -> uploaded as a dated
        snapshot, kept locally (they're still being written to)

    Returns the number of files shipped. Best-effort; never raises.
    """
    container = _get_container()
    if container is None:
        logger.warning(f"ship_logs: blob not configured ({_disabled_reason})")
        return 0

    logdir: Path = settings.log_dir
    prefix = settings.blob_prefix
    if prefix and not prefix.endswith("/"):
        prefix += "/"
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    shipped = 0

    for z in sorted(logdir.glob("*.zip")):
        name = f"{prefix}logs/{month}/{z.name}"
        try:
            with open(z, "rb") as f:
                container.upload_blob(name, f, overwrite=True)
            z.unlink()
            shipped += 1
            logger.info(f"☁  shipped log archive -> blob {name} (local removed)")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"ship_logs: {z.name} failed: {e}")

    for base in ("pipeline.log", "pipeline.jsonl"):
        p = logdir / base
        if not p.exists() or p.stat().st_size == 0:
            continue
        name = f"{prefix}logs/{month}/{stamp}_{base}"
        try:
            with open(p, "rb") as f:
                container.upload_blob(name, f, overwrite=True)
            shipped += 1
            logger.info(f"☁  shipped log snapshot -> blob {name} (local kept)")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"ship_logs: snapshot {base} failed: {e}")

    return shipped
