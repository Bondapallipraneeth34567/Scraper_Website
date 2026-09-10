"""HTTP client for the three AGMARKNET endpoints the web app itself uses.
No headless browser anywhere -- just httpx.

Endpoints (verified live from the VM, 2026-09-03 investigation -- see
docs/agmarknet_api_investigation.docx):

  GET  {base}/daily-price-arrival/filters
       -> full commodity taxonomy, NO captcha, ~525 KB JSON.

  POST {base}/captcha/generator            body: {}
       -> { captcha_key, captcha_image (base64 PNG), expires_at }  (~5 min)

  POST {base}/daily-price-arrival/report
       body: {
         from_date, to_date,          # same date; data exists from 2025-11-07
         data_type: "100006",         # price + arrival
         group:     <cmdt_group_id>,  # ONE integer
         commodity: <cmdt_id>,        # ONE integer
         state: "[100000]", district: "[100001]", market: "[100002]",
         grade: "[100003]", variety: "[100007]",   # "all" sentinels
         page: "1", limit: "100000",  # limit=100000 -> whole commodity, 1 call
         captcha: <text>, captcha_key: <key>
       }
       -> { status: true,
            data: { records: [ { data: [ ...rows... ],
                                 pagination: [ {total_count, total_pages} ] } ] },
            report_access: { report_ticket, remaining_calls, max_calls: 30, expires_at } }

VERIFIED behaviours this client relies on:
  * limit=100000 returns an entire commodity in one response (Wheat 1 Sep:
    879 rows, pagination.total_pages == 1). We still loop pages defensively.
  * report_ticket is good for ~30 calls / 5 min but ONLY for the identical
    filter set. Changing `commodity` returns "Report filters changed.
    Please solve captcha again." -> one CAPTCHA per commodity.
  * The rate limiter (Google Cloud edge) sends NO Retry-After / X-RateLimit
    headers. ~4 quick calls then the socket hangs. We treat a timeout, a
    429, or a "Too many requests" body as the same signal: back off.

ASSUMPTION (flagged, not verified): the *key names* inside each row object
of data.records[0].data. parser.py maps them tolerantly and `run.py
--dump-sample` prints a real row so they can be pinned on first live run.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import httpx
from loguru import logger

from ..config import settings


# --------------------------------------------------------------- exceptions
class AgmarknetError(Exception):
    """Base for everything this module raises."""


class CaptchaRequired(AgmarknetError):
    """The server wants a (fresh) CAPTCHA solved before it will return data.
    Covers both the first call for a commodity and the "Report filters
    changed. Please solve captcha again." response."""


class RateLimited(AgmarknetError):
    """Throttled: a 429, a "Too many requests" body, or a hung/timed-out
    socket with no useful response. Caller should back off and retry."""


class TransientError(AgmarknetError):
    """5xx, connection reset, malformed-but-not-throttle response. Retryable."""


class SchemaError(AgmarknetError):
    """A 200 response whose shape we don't recognise -- likely the API
    changed. NOT retried; surfaced loudly so a human looks."""


# ------------------------------------------------------------------- models
@dataclass
class CaptchaChallenge:
    key: str
    image_png: bytes
    expires_at: str | None = None


@dataclass
class ReportResult:
    rows: list[dict[str, Any]]
    total_count: int
    total_pages: int
    report_ticket: str | None = None
    remaining_calls: int | None = None
    raw_first_row: dict | None = None          # kept for --dump-sample
    extra: dict = field(default_factory=dict)


# ------------------------------------------------------------------- client
_ALL_SENTINELS = {
    "state": "[100000]",
    "district": "[100001]",
    "market": "[100002]",
    "grade": "[100003]",
    "variety": "[100007]",
}
_DATA_TYPE_PRICE_AND_ARRIVAL = "100006"


class AgmarknetClient:
    def __init__(self) -> None:
        self._http = httpx.Client(
            http2=True,
            headers=settings.base_headers(),
            timeout=httpx.Timeout(
                connect=settings.http_connect_timeout_sec,
                read=settings.http_read_timeout_sec,
                write=settings.http_read_timeout_sec,
                pool=settings.http_connect_timeout_sec,
            ),
            follow_redirects=True,
        )
        self._last_report_call = 0.0

    # -- lifecycle --
    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "AgmarknetClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- politeness --
    def _pace_report(self) -> None:
        """Keep calls to the throttled /report endpoint at least
        REQUEST_MIN_INTERVAL_SEC apart. /filters is not paced."""
        gap = settings.request_min_interval_sec - (time.monotonic() - self._last_report_call)
        if gap > 0:
            time.sleep(gap)

    def _pace_captcha(self) -> None:
        """Small floor (CAPTCHA_MIN_GAP_SEC) between the previous /report and
        the next captcha mint -- the edge limiter is burst-sensitive, and
        without this the mint fires ~0s after the prior report."""
        gap = settings.captcha_min_gap_sec - (time.monotonic() - self._last_report_call)
        if gap > 0:
            time.sleep(gap)

    # -- 1. taxonomy (no captcha) --
    def get_filters(self) -> dict:
        try:
            r = self._http.get(settings.filters_url())
        except httpx.TimeoutException as e:
            raise RateLimited(f"filters timed out: {e}") from e
        except httpx.HTTPError as e:
            raise TransientError(f"filters transport error: {e}") from e
        if r.status_code == 429:
            raise RateLimited("filters -> HTTP 429")
        if r.status_code >= 500:
            raise TransientError(f"filters -> HTTP {r.status_code}")
        try:
            j = r.json()
        except ValueError as e:
            raise SchemaError(f"filters returned non-JSON (HTTP {r.status_code})") from e
        return j.get("data", j)

    # -- 2. captcha image (no captcha to reach it, obviously) --
    def new_captcha(self) -> CaptchaChallenge:
        import base64

        self._pace_captcha()
        try:
            r = self._http.post(settings.captcha_url(), json={})
        except httpx.TimeoutException as e:
            raise RateLimited(f"captcha/generator timed out: {e}") from e
        except httpx.HTTPError as e:
            raise TransientError(f"captcha/generator transport error: {e}") from e

        if r.status_code == 429 or "too many" in r.text.lower():
            raise RateLimited("captcha/generator -> throttled")
        if r.status_code >= 500:
            raise TransientError(f"captcha/generator -> HTTP {r.status_code}")
        try:
            j = r.json()
        except ValueError as e:
            raise SchemaError("captcha/generator returned non-JSON") from e

        key = j.get("captcha_key") or j.get("key")
        img_b64 = j.get("captcha_image") or j.get("image") or j.get("captcha")
        if not key or not img_b64:
            raise SchemaError(f"captcha/generator missing key/image; got keys {sorted(j)}")
        if img_b64.startswith("data:"):
            img_b64 = img_b64.split(",", 1)[1]
        return CaptchaChallenge(key=key, image_png=base64.b64decode(img_b64),
                                expires_at=j.get("expires_at"))

    # -- 3. the data call --
    def fetch_report(
        self,
        *,
        group_id: int,
        commodity_id: int,
        target_date: date,
        captcha_text: str | None,
        captcha_key: str | None,
        report_ticket: str | None = None,
        page: int = 1,
        limit: int = 100000,
    ) -> ReportResult:
        """One /report call. Provide EITHER (captcha_text + captcha_key) for
        the first call of a commodity, OR report_ticket to page the same
        commodity. Raises CaptchaRequired / RateLimited / TransientError /
        SchemaError; returns ReportResult on success."""
        iso = target_date.isoformat()
        body: dict[str, Any] = {
            "from_date": iso,
            "to_date": iso,
            "data_type": _DATA_TYPE_PRICE_AND_ARRIVAL,
            "group": str(group_id),
            "commodity": str(commodity_id),
            "page": str(page),
            "limit": str(limit),
            **_ALL_SENTINELS,
        }
        if report_ticket:
            body["report_ticket"] = report_ticket
        if captcha_text is not None and captcha_key is not None:
            body["captcha"] = captcha_text
            body["captcha_key"] = captcha_key

        self._pace_report()
        try:
            r = self._http.post(settings.report_url(), json=body)
        except httpx.TimeoutException as e:
            # A hung socket IS the throttle here -- no response body to read.
            raise RateLimited(f"/report read timed out (throttle): {e}") from e
        except httpx.HTTPError as e:
            raise TransientError(f"/report transport error: {e}") from e
        finally:
            self._last_report_call = time.monotonic()

        if r.status_code == 429:
            raise RateLimited("/report -> HTTP 429")
        if r.status_code >= 500:
            raise TransientError(f"/report -> HTTP {r.status_code}")

        text = r.text
        try:
            j = r.json()
        except ValueError as e:
            if "too many" in text.lower():
                raise RateLimited("/report -> 'too many requests' (non-JSON)") from e
            raise SchemaError(f"/report returned non-JSON (HTTP {r.status_code}): {text[:200]}") from e

        return self._interpret_report(j)

    # -- response interpretation --
    @staticmethod
    def _interpret_report(j: dict) -> ReportResult:
        if j.get("status") is True:
            data = j.get("data") or {}
            records = data.get("records") or []
            first = records[0] if records else {}
            rows = first.get("data") or []
            pag = (first.get("pagination") or [{}])
            pag0 = pag[0] if pag else {}
            access = j.get("report_access") or data.get("report_access") or {}
            return ReportResult(
                rows=rows,
                total_count=int(pag0.get("total_count") or len(rows)),
                total_pages=int(pag0.get("total_pages") or 1),
                report_ticket=access.get("report_ticket"),
                remaining_calls=access.get("remaining_calls"),
                raw_first_row=rows[0] if rows else None,
                extra={"report_access": access},
            )

        msg = str(j.get("message") or j.get("detail") or j.get("error") or "").strip()
        low = msg.lower()

        if "too many" in low or "rate limit" in low:
            raise RateLimited(f"/report body: {msg!r}")
        if "captcha" in low or "report filters changed" in low or "solve captcha" in low:
            raise CaptchaRequired(msg or "captcha required (unspecified)")
        # "No data available" is a NORMAL response for a commodity that has no
        # prices on this date (livestock, wood products, exotic flowers, etc.).
        # It is NOT a failure -- return an empty result so the caller records
        # the commodity as 'empty', same as a successful call with zero rows.
        if "no data" in low or "no record" in low or "data not available" in low:
            access = j.get("report_access") or (j.get("data") or {}).get("report_access") or {}
            return ReportResult(
                rows=[], total_count=0, total_pages=1,
                report_ticket=access.get("report_ticket"),
                remaining_calls=access.get("remaining_calls"),
                raw_first_row=None, extra={"report_access": access, "note": msg},
            )
        # status:false but nothing we recognise -> let the caller optionally
        # run the LLM classifier before deciding it's a schema change.
        raise SchemaError(f"/report status=false, unrecognised message: {msg!r} (keys {sorted(j)})")

    # -- convenience: page a whole commodity given a first page result --
    _MAX_PAGES = 500

    def fetch_all_pages(
        self, *, first: ReportResult, group_id: int, commodity_id: int,
        target_date: date, limit: int = 100000,
    ) -> list[dict]:
        """Return EVERY row for the commodity, not just page 1.

        Verified: limit=100000 normally returns the whole commodity in one
        response with total_pages == 1. But `total_pages` is derived by the
        API from its own page size, and a truncated response still reports
        total_pages == 1 -- trusting it silently loses rows. So we page on
        the declared `total_count` instead: keep pulling pages (reusing the
        report_ticket, no extra CAPTCHA) until we have as many rows as the
        API said exist, a page comes back empty, or the safety cap is hit.
        A residual shortfall is logged loudly (the sweep turns it into
        SweepStats.rows_missing)."""
        rows = list(first.rows)
        want = first.total_count or len(rows)

        if len(rows) >= want:
            return rows
        if not first.report_ticket:
            logger.warning(
                f"commodity {commodity_id}: got {len(rows)}/{want} rows and no "
                f"report_ticket to page the rest -- returning partial"
            )
            return rows

        page = 1
        while len(rows) < want and page < self._MAX_PAGES:
            page += 1
            logger.info(f"paging commodity {commodity_id}: page {page} "
                        f"({len(rows)}/{want} rows so far)")
            nxt = self.fetch_report(
                group_id=group_id, commodity_id=commodity_id, target_date=target_date,
                captcha_text=None, captcha_key=None,
                report_ticket=first.report_ticket, page=page, limit=limit,
            )
            if not nxt.rows:
                break
            rows.extend(nxt.rows)

        if len(rows) < want:
            logger.warning(
                f"commodity {commodity_id}: fetched {len(rows)}/{want} declared rows "
                f"after {page} page(s) -- the API returned fewer than it promised"
            )
        return rows
