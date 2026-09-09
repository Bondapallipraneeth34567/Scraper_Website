"""HTTP client for the data.gov.in resource API.

Verified live 2026-09-04 against resource 9ef84268-d588-465a-a308-a864a43d0070:

  GET {base}/{resource_id}?api-key=***&format=json&offset=N&limit=M

  Response envelope:
    { "status": "ok", "message": "Resource lists",
      "total": 9489,          # rows currently in the resource
      "count": <this page>,
      "limit": "<M>", "offset": "<N>",
      "updated": 1788512443,                    # epoch of last upstream refresh
      "updated_date": "2026-09-04T09:00:43Z",
      "field": [ {id, name, type}, ... ],       # schema
      "records": [ {state, district, market, commodity, variety, grade,
                    arrival_date, min_price, max_price, modal_price}, ... ] }

Assumptions (documented, not from an official spec):
  * No published per-request `limit` cap or rate limit. We page at
    DATA_GOV_IN_PAGE_SIZE and shrink on a 400/414. We pace politely and
    back off on 429 / 5xx / timeout.
  * Known quirk: when offset >= total the API returns an empty `records`
    with status "ok" (not an error). We treat empty records as "done".
  * `format=json` is honoured for this resource (some legacy resources
    ignore it and always return CSV -- this one does not).
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Any

import httpx
from loguru import logger

from ..config import settings


class DataGovInError(Exception):
    """Base."""


class AuthError(DataGovInError):
    """401/403 or a body complaining about the API key. Not retryable."""


class RateLimited(DataGovInError):
    """429 / quota / timeout. Retryable with backoff."""


class TransientError(DataGovInError):
    """5xx / connection reset / malformed-but-not-auth. Retryable."""


class SchemaError(DataGovInError):
    """A 200 whose shape we don't recognise. Not retried; surfaced loudly."""


@dataclass
class Meta:
    total: int
    count: int
    limit: int
    offset: int
    updated_epoch: int | None
    updated_date: str | None
    status: str
    fields: list[dict] = field(default_factory=list)
    window_shortfall: int = 0   # rows that couldn't be reached (offset+limit > window, no working filter)

    @classmethod
    def from_body(cls, j: dict) -> "Meta":
        def _int(v, d=0):
            try:
                return int(v)
            except (TypeError, ValueError):
                return d

        return cls(
            total=_int(j.get("total")),
            count=_int(j.get("count")),
            limit=_int(j.get("limit")),
            offset=_int(j.get("offset")),
            updated_epoch=_int(j.get("updated"), None) if j.get("updated") is not None else None,
            updated_date=j.get("updated_date"),
            status=str(j.get("status") or ""),
            fields=j.get("field") or j.get("fields") or [],
        )


class DataGovInClient:
    def __init__(self) -> None:
        if not settings.datagovin_api_key:
            raise AuthError(
                "no API key: set DATA_GOV_IN_API_KEY (or AGMARKNET_API_KEY) in mandi_pipeline/.env"
            )
        self._url = settings.datagovin_resource_url()
        self._key = settings.datagovin_api_key
        self._http = httpx.Client(
            http2=True,
            headers={"Accept": "application/json", "User-Agent": settings.user_agent},
            timeout=httpx.Timeout(
                connect=settings.http_connect_timeout_sec,
                read=settings.datagovin_read_timeout_sec,   # the API is often slow
                write=settings.http_connect_timeout_sec,
                pool=settings.http_connect_timeout_sec,
            ),
            follow_redirects=True,
        )
        self._last_call = 0.0

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "DataGovInClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- one raw GET with retry/backoff --
    def _get(self, *, offset: int, limit: int, extra: dict[str, str] | None = None) -> dict:
        params = {
            "api-key": self._key,
            "format": "json",
            "offset": str(offset),
            "limit": str(limit),
        }
        if extra:
            params.update(extra)
        last: Exception | None = None
        for attempt in range(settings.max_retries):
            # polite spacing between calls (the shared demo key throttles hard)
            gap = settings.datagovin_min_interval_sec - (time.monotonic() - self._last_call)
            if gap > 0:
                time.sleep(gap)
            try:
                r = self._http.get(self._url, params=params)
            except httpx.TimeoutException as e:
                last = RateLimited(f"timeout: {e}")
            except httpx.HTTPError as e:
                last = TransientError(f"transport error: {e}")
            else:
                self._last_call = time.monotonic()
                if r.status_code in (401, 403):
                    raise AuthError(f"HTTP {r.status_code}: {r.text[:200]}")
                if r.status_code == 429:
                    last = RateLimited("HTTP 429")
                elif r.status_code >= 500:
                    last = TransientError(f"HTTP {r.status_code}")
                elif r.status_code == 400 or r.status_code == 414:
                    raise SchemaError(f"HTTP {r.status_code} (limit too large?): {r.text[:200]}")
                else:
                    try:
                        j = r.json()
                    except ValueError as e:
                        raise SchemaError(f"non-JSON body (HTTP {r.status_code}): {r.text[:200]}") from e
                    status = str(j.get("status") or "").lower()
                    if status and status not in ("ok", "success"):
                        msg = str(j.get("message") or j.get("error") or "")
                        if "key" in msg.lower() or "api" in msg.lower() and "limit" not in msg.lower():
                            raise AuthError(f"body status={status!r}: {msg[:200]}")
                        raise SchemaError(f"body status={status!r}: {msg[:200]}")
                    return j

            delay = min(settings.backoff_base_sec * (2 ** attempt), settings.backoff_max_sec)
            logger.warning(f"data.gov.in GET offset={offset}: {last} -> backoff {delay:.0f}s "
                           f"(attempt {attempt + 1}/{settings.max_retries})")
            time.sleep(delay)
        raise last or TransientError("exhausted retries")

    # -- cheap freshness probe --
    def peek(self) -> Meta:
        return Meta.from_body(self._get(offset=0, limit=1))

    # -- page from offset 0 as far as `cap` (the 10k window) allows --
    def _pull_slice(self, extra: dict[str, str] | None, cap: int) -> list[dict]:
        page = settings.datagovin_page_size
        out: list[dict] = []
        offset = 0
        while offset < cap:
            lim = min(page, cap - offset)
            batch = self._get(offset=offset, limit=lim, extra=extra).get("records") or []
            if not batch:
                break
            out.extend(batch)
            offset += len(batch)
            if len(batch) < lim:
                break
        return out

    # -- full pull --
    def fetch_all(self) -> tuple[Meta, list[dict]]:
        """data.gov.in (Elasticsearch) rejects offset+limit > DATA_GOV_IN_WINDOW
        (10000). Strategy:
          * total under the window        -> plain paging.
          * total over the window         -> partition by Arrival_Date
            (the resource holds only a handful of distinct dates; each
            date's slice is well under the window). If server-side filters
            turn out not to work for this resource/key, fall back to the
            first `window` rows and record the shortfall (never abort)."""
        window = settings.datagovin_window
        first = self._get(offset=0, limit=min(settings.datagovin_page_size, window))
        meta = Meta.from_body(first)
        total = meta.total or 0
        seed = list(first.get("records") or [])

        if not total or total <= window - 500:
            # everything is reachable inside the window -> plain paging
            records = self._pull_slice(None, cap=max(total, len(seed)) or window)
            logger.info(f"data.gov.in: pulled {len(records)} records (plain paging, total={total})")
            return meta, records

        # ---- over the window: try Arrival_Date partitioning ----
        dates = sorted({str(r.get("arrival_date") or "").strip()
                        for r in seed if r.get("arrival_date")})
        probe_date = dates[0] if dates else None
        filters_work = False
        if probe_date:
            probe = self._get(offset=0, limit=5, extra={"filters[Arrival_Date]": probe_date})
            pc = int(probe.get("count") or 0)
            filters_work = pc > 0
            logger.info(f"filter probe filters[Arrival_Date]={probe_date} -> count={pc} "
                        f"({'usable' if filters_work else 'NOT usable'})")

        if filters_work:
            logger.info(f"data.gov.in: total {total} > window {window} -> {len(dates)} date slices")
            by_key: dict[tuple, dict] = {}
            for d in dates:
                rows = self._pull_slice({"filters[Arrival_Date]": d}, cap=window)
                for r in rows:
                    by_key[_row_key(r)] = r
                logger.debug(f"  Arrival_Date={d}: {len(rows)} rows")
            records = list(by_key.values())
            if len(records) < total * 0.8:
                logger.warning(f"date-partitioned pull got {len(records)}/{total}; a single "
                               f"date may exceed the window -- consider (State, Arrival_Date).")
        else:
            # filters unusable -> take everything the window allows, record the gap
            records = self._pull_slice(None, cap=window)
            meta.window_shortfall = max(0, total - len(records))
            logger.warning(
                f"⚠️  server-side filters not usable for this resource/key -- pulled the first "
                f"{len(records)} of {total} rows; {meta.window_shortfall} beyond the {window}-row "
                f"window were NOT retrieved this cycle. Register a personal data.gov.in API key "
                f"(the shared demo key is limited) and confirm the filters[] syntax to close this."
            )

        logger.info(f"data.gov.in: pulled {len(records)} records "
                    f"(total={total}, shortfall={meta.window_shortfall}, updated={meta.updated_date})")
        # A KNOWN window shortfall is a reported partial, not a fetch failure.
        if total and meta.window_shortfall == 0 and len(records) < total * 0.5:
            raise TransientError(f"only got {len(records)} of {total} records -- refusing a "
                                 f"half-empty snapshot for the diff")
        return meta, records


def _row_key(r: dict) -> tuple:
    g = lambda k: str(r.get(k) or "").strip()
    return (g("state"), g("district"), g("market"), g("commodity"),
            g("variety"), g("grade"), g("arrival_date"))
