"""Optional LLM-assisted classifier for AMBIGUOUS /report responses.

The primary path is deterministic string matching in client._interpret_report
(status flag, "captcha", "Report filters changed", "too many requests").
This is only consulted when a response is a 200 that matches NONE of those
-- i.e. right before the pipeline would give up with SchemaError -- to
decide whether it is really a schema change or just an unfamiliar way of
saying "solve the captcha" / "you're rate limited".

Enabled by LLM_CLASSIFIER_ENABLED=true (default false). Never fabricates
data; only returns one of a fixed label set.
"""
from __future__ import annotations

from loguru import logger

from ..config import settings

LABELS = ("captcha_required", "rate_limited", "transient_error", "schema_change", "ok")

_SYSTEM = (
    "You classify a single JSON/HTTP response from the AGMARKNET price API. "
    "Reply with ONLY one label from this set, nothing else: "
    "captcha_required | rate_limited | transient_error | schema_change | ok. "
    "captcha_required = the server is asking for a (new) CAPTCHA or says filters "
    "changed. rate_limited = throttling / too many requests / quota. "
    "transient_error = a temporary server/network fault worth retrying. "
    "schema_change = a 200 response in a shape the client no longer understands. "
    "ok = this actually looks like a valid data response. No explanation."
)

_client = None


def _get_client():
    global _client
    if _client is None:
        import anthropic
        _client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    return _client


def classify(response_text: str, http_status: int) -> str:
    if not settings.llm_classifier_enabled:
        return "schema_change"
    if not settings.anthropic_api_key:
        logger.warning("LLM classifier enabled but ANTHROPIC_API_KEY unset -- skipping")
        return "schema_change"
    try:
        import anthropic  # noqa: F401
    except ImportError:
        logger.warning("LLM classifier enabled but `anthropic` not installed -- skipping")
        return "schema_change"

    snippet = response_text[:4000]
    try:
        msg = _get_client().messages.create(
            model=settings.llm_model,
            max_tokens=64,
            # One-label classification -- no reasoning needed. Without this,
            # Sonnet 5's default adaptive thinking eats the token budget and
            # the label is truncated away.
            thinking={"type": "disabled"},
            system=_SYSTEM,
            messages=[{"role": "user", "content": f"HTTP {http_status}\n\n{snippet}"}],
        )
        if getattr(msg, "stop_reason", None) == "refusal":
            logger.warning("LLM classifier: model declined -- treating as schema_change")
            return "schema_change"
        raw = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text").strip().lower()
    except Exception as e:
        logger.warning(f"LLM classifier call failed: {e}")
        return "schema_change"

    for lbl in LABELS:
        if lbl in raw:
            logger.info(f"LLM classifier: {lbl}  (raw={raw!r})")
            return lbl
    logger.warning(f"LLM classifier returned nothing usable: {raw!r}")
    return "schema_change"
