"""Approach 2 -- the official data.gov.in Open Government Data API.

Resource 9ef84268-d588-465a-a308-a864a43d0070
"Current Daily Price of Various Commodities from Various Markets (Mandi)".

No CAPTCHA, no browser, no per-commodity loop: one paged bulk fetch of the
whole resource every 4 hours, diffed against the previous snapshot, written
to mandi.market_prices_v4.

  client.py    peek() + fetch_all() with paging, retry, backoff
  parser.py    record -> normalised row
  snapshot.py  raw JSON snapshot to disk (+ optional blob)
  pipeline.py  the cycle: guard -> peek -> fetch -> diff -> upsert -> watermark
  run.py       CLI entry point
"""
from .client import (
    AuthError,
    DataGovInClient,
    DataGovInError,
    Meta,
    RateLimited,
    SchemaError,
    TransientError,
)

__all__ = [
    "DataGovInClient", "Meta",
    "DataGovInError", "AuthError", "RateLimited", "TransientError", "SchemaError",
]
