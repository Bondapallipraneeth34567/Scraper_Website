"""AGMARKNET direct-API layer (no browser).

  client.py          httpx client for /filters, /captcha/generator, /report
  captcha.py         CAPTCHA detection + solver interface (manual / ocr)
  taxonomy.py        commodity list load / refresh / cache
  parser.py          /report JSON -> normalised price rows
  llm_classifier.py  optional fallback classifier for ambiguous responses
  pipeline.py        the per-commodity sweep, checkpointing, backoff
"""
from .client import (
    AgmarknetClient,
    CaptchaRequired,
    RateLimited,
    SchemaError,
    TransientError,
    ReportResult,
)

__all__ = [
    "AgmarknetClient",
    "CaptchaRequired",
    "RateLimited",
    "SchemaError",
    "TransientError",
    "ReportResult",
]
