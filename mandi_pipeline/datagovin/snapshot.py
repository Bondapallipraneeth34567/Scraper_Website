"""Persist each raw API pull -- "download/store the returned data" -- so a
row in the DB can be traced back to the exact bytes it came from, and so a
diff can be re-run offline.

  {DATA_GOV_IN_SNAPSHOT_DIR}/<UTC-timestamp>.json
      { "fetched_at":..., "resource_id":..., "meta": {...}, "records": [...] }

Old snapshots beyond DATA_GOV_IN_SNAPSHOT_KEEP are pruned. If Azure Blob is
configured, the snapshot is mirrored to <BLOB_PREFIX>datagovin/snapshots/.
"""
from __future__ import annotations

import gzip
import json
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger

from ..config import settings
from ..storage import _get_container   # reuse the same container client


def save_snapshot(meta, records: list[dict]) -> Path:
    settings.datagovin_snapshot_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = settings.datagovin_snapshot_dir / f"{stamp}.json.gz"
    payload = {
        "fetched_at": stamp,
        "resource_id": settings.datagovin_resource_id,
        "meta": asdict(meta),
        "record_count": len(records),
        "records": records,
    }
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False)
    logger.info(f"💾 snapshot -> {path}  ({len(records)} records)")

    _mirror_to_blob(path)
    _prune()
    return path


def _mirror_to_blob(path: Path) -> None:
    container = _get_container()
    if container is None:
        return
    prefix = settings.blob_prefix
    if prefix and not prefix.endswith("/"):
        prefix += "/"
    name = f"{prefix}datagovin/snapshots/{path.name}"
    try:
        with open(path, "rb") as f:
            container.upload_blob(name, f, overwrite=True)
        logger.info(f"☁  snapshot mirrored -> blob {settings.azure_storage_container}/{name}")
    except Exception:  # noqa: BLE001
        logger.opt(exception=True).warning(f"⚠️  snapshot blob upload failed for {name}")


def _prune() -> None:
    keep = max(1, settings.datagovin_snapshot_keep)
    files = sorted(settings.datagovin_snapshot_dir.glob("*.json*"), reverse=True)
    for old in files[keep:]:
        try:
            old.unlink()
        except OSError:
            pass
