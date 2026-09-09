"""loguru configuration -- matches the existing scraper's style so
`journalctl -u mandi-pipeline` and the rotating file both read the same.

- stdout (picked up by systemd -> journald)
- rotating file  state/logs/pipeline.log   (10 MB x 10, zipped)
- structured JSONL  state/logs/pipeline.jsonl  (one object per line, for
  ingestion into whatever log store the team runs)
"""
from __future__ import annotations

import sys

from loguru import logger

from .config import settings

_CONFIGURED = False


def setup_logging() -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return

    logger.remove()

    logger.add(
        sys.stdout,
        level=settings.log_level,
        enqueue=True,
        backtrace=False,
        diagnose=False,
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss}</green> "
            "<level>{level: <8}</level> "
            "<cyan>{extra[run_id]}</cyan> "
            "<level>{message}</level>"
        ),
    )

    logger.add(
        settings.log_dir / "pipeline.log",
        level=settings.log_level,
        rotation="10 MB",
        retention=10,
        compression="zip",
        enqueue=True,
        format="{time:YYYY-MM-DD HH:mm:ss} {level: <8} {extra[run_id]} {message}",
    )

    logger.add(
        settings.log_dir / "pipeline.jsonl",
        level=settings.log_level,
        rotation="20 MB",
        retention=10,
        serialize=True,
        enqueue=True,
    )

    # Default binding so the format string never KeyErrors before a run id
    # is known.
    logger.configure(extra={"run_id": "-"})
    _CONFIGURED = True
