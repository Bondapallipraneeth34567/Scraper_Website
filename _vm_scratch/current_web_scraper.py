"""Drives the real agmarknet.gov.in "Daily Price and Arrival Report" page
with a real (headless) browser — filling the same State/Commodity
Group/Commodity dropdowns and clicking the same "Go" button a human would —
and reads today's prices out of the rendered results table.

Ported from the working PIPECAT-AGENT-V1 project's
bot_processors/pricing/agmarknet_scraper.py (2026-08-13) — same scraping
logic verbatim (proven against the live site's captcha wall and its various
flaky-dropdown/race-condition failure modes), with only the storage layer
adjusted: originally Postgres/asyncpg -> Azure SQL/pymssql (via src.database),
then back to Postgres/asyncpg 2026-08-28 (see database.py) once
dbo.MarketPrices was migrated off Azure SQL onto the shared Postgres
server's mandi.market_prices — same verification-dashboard read path,
just no longer split across two separate database engines. The
per-commodity JSON checkpoint's file lock stays POSIX fcntl throughout
(this VM is Ubuntu, not Windows) regardless of which DB is behind it.

scrape_all() is the background job: meant to be run periodically (see
scraper_daemon.py) and writes into mandi.market_prices (Postgres) — the
same table src.services.mandi_service queries from, so calls always see
whatever this job most recently scraped. Nothing about what it scrapes is
hand-picked: every state/UT (via the "All States/UTs" option) and every
commodity (from load_commodity_reference(), harvested from the site's own
dropdowns) is walked dynamically.

Run manually with:  python -m src.services.agmarknet_scraper
"""

import base64
import fcntl
import io
import json
import time
from datetime import date, datetime
from pathlib import Path

from dotenv import load_dotenv
from loguru import logger
from PIL import Image, ImageOps
from playwright.sync_api import Locator, Page, sync_playwright

load_dotenv()

from database import execute_many
from price_shared import _LAST_KNOWN_KEY_SEP, crop_keyword_for

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# Agmarknet requires a captcha on every "Go" submission. Solved with OCR
# rather than any external captcha-solving service — the image is a clean,
# low-noise 6-character alphanumeric render with no CAPTCHA challenge beyond
# that. _ocr_reader is a lazy singleton because loading the model is the
# expensive part (~seconds) and every Go click in a long run reuses it.
_CAPTCHA_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
_ocr_reader = None

# The site shows a full-screen loading overlay after most dropdown/date
# changes while it fetches the next field's options. If the Go click lands
# while it's still up, Playwright's own actionability retry hammers the
# click for its default 30s timeout before giving up — that's most of the
# per-commodity time in production, not the captcha. Waiting for it to
# detach first used to resolve in ~1-2s; as of production logs from
# 2026-08-17..31 the overlay is now routinely taking longer than that to
# detach (site load, not a rare hiccup — ~140 Go-click timeouts/day across
# ~586 commodities), and each one that exhausts _click_go_button's retries
# propagates up to scrape_all's per-commodity handler, which drops that
# commodity for the whole day (see its "reloading form and skipping"
# comment) — a direct, daily contributor to the site-coverage gap the QA
# dashboard's /api/filters/commodity-coverage shows. Timeouts below widened
# accordingly (was 8000/15000/4 attempts) rather than tuned for the old,
# faster-clearing overlay.
_OVERLAY_SELECTOR = "div.fixed.inset-0.bg-black.bg-opacity-30.backdrop-blur-sm"
_GO_CLICK_TIMEOUT_MS = 20000


def _wait_overlay_gone(page: Page, timeout: int = 5000) -> None:
    try:
        page.locator(_OVERLAY_SELECTOR).wait_for(state="hidden", timeout=timeout)
    except Exception:
        pass


def _click_go_button(page: Page, max_attempts: int = 6) -> None:
    """Clicks the Go button, re-waiting for the loading overlay and
    retrying a few times if the click still lands on it — cheaper and
    faster to recover than relying on Playwright's own single up-to-15s
    actionability retry against the same "intercepts pointer events"
    failure (the overlay occasionally takes longer to detach than
    _wait_overlay_gone's own wait allows for)."""
    last_exc: Exception | None = None
    for attempt in range(max_attempts):
        _wait_overlay_gone(page, timeout=20000)
        try:
            page.click("button:has-text('Go')", timeout=_GO_CLICK_TIMEOUT_MS)
            return
        except Exception as exc:
            last_exc = exc
            logger.warning(
                f"⚠️ agmarknet_scraper: Go click blocked by overlay "
                f"(attempt {attempt + 1}/{max_attempts}), retrying"
            )
            page.wait_for_timeout(500)
    raise last_exc


def _get_ocr_reader():
    global _ocr_reader
    if _ocr_reader is None:
        import easyocr

        _ocr_reader = easyocr.Reader(["en"], gpu=False, verbose=False)
    return _ocr_reader


def _preprocess_captcha_image(image_bytes: bytes) -> bytes:
    """Upscales and binarizes the raw captcha render before OCR sees it.
    The site's glyphs are small and anti-aliased, which is most of why a
    single OCR pass gets ambiguous characters (I/l/1, 0/O/9/g) wrong more
    often than not — scaling up 4x with LANCZOS and thresholding to pure
    black/white removes the anti-aliasing noise those confusions come
    from, without touching the actual retry logic in _click_go."""
    img = Image.open(io.BytesIO(image_bytes)).convert("L")
    img = img.resize((img.width * 4, img.height * 4), Image.LANCZOS)
    img = ImageOps.autocontrast(img)
    img = img.point(lambda p: 255 if p > 140 else 0)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _solve_captcha(page: Page) -> None:
    """Reads the captcha's base64 image data straight off the <img> element
    (no extra network round-trip), preprocesses it (see
    _preprocess_captcha_image), and fills its OCR reading into the
    #captcha field. Not reliable on its own — see _click_go, which is the
    real entry point and retries this against a freshly refreshed image on
    rejection, since even a cleaned-up image doesn't get every glyph right
    every time."""
    src = page.locator("img[alt='Captcha']").get_attribute("src")
    _, b64data = src.split(",", 1)
    image_bytes = base64.b64decode(b64data)
    image_bytes = _preprocess_captcha_image(image_bytes)

    reader = _get_ocr_reader()
    guess = "".join(reader.readtext(image_bytes, detail=0, allowlist=_CAPTCHA_ALPHABET))
    page.fill("#captcha", guess)


def _click_go(page: Page, max_attempts: int = 12) -> None:
    """Solves the captcha and clicks Go, retrying against a freshly
    refreshed captcha image whenever the site rejects the guess. The
    #captcha input's own aria-invalid attribute is the accept/reject signal
    — flips to "true" on a rejected submission and back to "false" once one
    is accepted. OCR gets this captcha's ambiguous 6-character glyphs right
    well under half the time, so max_attempts is generously sized rather
    than tuned tight."""
    for attempt in range(max_attempts):
        _solve_captcha(page)
        _click_go_button(page)

        try:
            page.wait_for_function(
                "() => document.querySelector('#captcha')?.getAttribute('aria-invalid') === 'true' "
                "|| document.querySelectorAll('table tbody tr').length > 0",
                timeout=2000,
            )
        except Exception:
            pass

        if page.locator("#captcha").get_attribute("aria-invalid") == "false":
            return

        logger.warning(
            f"⚠️ agmarknet_scraper: captcha rejected (attempt {attempt + 1}/{max_attempts}), refreshing and retrying"
        )
        page.click("button[aria-label='Refresh captcha']")
        page.wait_for_timeout(800)

    logger.error(f"⚠️ agmarknet_scraper: captcha solving failed after {max_attempts} attempts, giving up on this query")


def _open_dropdown(page: Page, label_text: str) -> Locator:
    """Opens a cascading search-select dropdown by its label and returns its
    trigger element, retrying until the search box actually appears (a
    field already picked earlier in the same page session sometimes toggles
    an already-open/stale panel closed instead of opening it)."""
    trigger = page.locator(f"label:text-is('{label_text}')").locator("..")
    search_box = page.locator("input[placeholder='Search...']")

    for _ in range(5):
        trigger.click(force=True)
        page.wait_for_timeout(500)
        if search_box.count() > 0 and search_box.last.is_visible():
            return trigger
    raise RuntimeError(f"agmarknet_scraper: dropdown for {label_text!r} never opened")


def _enumerate_dropdown_options(page: Page, trigger: Locator) -> list[str]:
    """Reads every option currently listed in an already-open dropdown panel
    without selecting any of them."""
    outer = trigger.locator("..")
    panel = outer.locator("div.absolute.top-full")
    return panel.locator("div.py-1 > div").all_inner_texts()


def _pick(page: Page, label_text: str, option_text: str) -> None:
    """Opens a cascading search-select dropdown by its label, types the
    option text into its search box, and clicks the matching option. Reads
    the trigger's displayed text back after clicking and retries the whole
    open/search/click sequence if it doesn't contain the value just
    selected — a click occasionally doesn't register with the underlying
    React state, silently leaving the form on the previous field's value."""
    search_box = page.locator("input[placeholder='Search...']")

    for attempt in range(3):
        trigger = _open_dropdown(page, label_text)

        box = search_box.last
        box.fill(option_text)
        page.wait_for_timeout(800)
        page.locator(f"text='{option_text}'").last.click(force=True)
        page.wait_for_timeout(600)

        if option_text in trigger.inner_text():
            return
        logger.warning(
            f"⚠️ agmarknet_scraper: {label_text!r} still doesn't show "
            f"{option_text!r} after selecting it (attempt {attempt + 1}/3), retrying"
        )

    raise RuntimeError(
        f"agmarknet_scraper: failed to select {option_text!r} for {label_text!r} after 3 attempts"
    )


def _scrape_visible_results_dynamic(page: Page) -> list[dict[str, str]]:
    """Reads every row of the results table, paging through with the
    "Next page" button until it's disabled or absent. Reads column names
    from the table's own header row rather than assuming a fixed layout —
    needed for "Both" mode, whose thead has two rows (a grouping row above
    the real per-column header row); only the last thead row's cell count
    matches each body row's <td> count."""
    rows: list[dict[str, str]] = []
    table = page.locator("table")
    if table.count() == 0:
        return rows
    headers = [
        h.strip() or f"col_{i}"
        for i, h in enumerate(table.locator("thead tr").last.locator("th").all_inner_texts())
    ]

    while True:
        trs = table.locator("tbody tr")
        for i in range(trs.count()):
            cells = trs.nth(i).locator("td").all_inner_texts()
            if len(cells) == len(headers):
                rows.append(dict(zip(headers, (c.strip() for c in cells))))
            else:
                # Used to be silently discarded with no trace at all --
                # logging so a real mismatch (a stray/merged cell in the
                # rendered DOM row) is investigable instead of invisibly
                # eating a row.
                logger.warning(
                    f"⚠️ agmarknet_scraper: dropping malformed table row "
                    f"(expected {len(headers)} cells, got {len(cells)}): {cells!r}"
                )

        next_btn = page.locator('button[title="Next page"]')
        if next_btn.count() == 0 or next_btn.get_attribute("disabled") is not None:
            break
        next_btn.click()
        page.wait_for_timeout(1000)
    return rows


def _row_to_result(row: dict[str, str]) -> dict | None:
    """Converts one scraped table row into a result dict matching the
    site's own reported values. As of 2026-08-31 (mandi.market_prices
    restructured to mirror agmarknet's own report columns): no more
    per-kg/quintal normalization — min/max/modal_price are stored exactly
    as the site reports them, alongside the real price_unit, instead of
    force-converted to a quintal-equivalent regardless of the source
    unit. Price Date and Arrival Date are two separate columns on the
    site (previously only Price Date was captured, into a column
    misleadingly named arrival_date) — both are now read from their own
    correctly-named source column."""
    try:
        min_price = float(row["Min Price"].replace(",", ""))
        max_price = float(row["Max Price"].replace(",", ""))
        modal_price = float(row["Modal Price"].replace(",", ""))
    except ValueError:
        # Used to return None here with no trace at all -- logging so an
        # unparseable price is investigable instead of invisibly eating a
        # row.
        logger.warning(f"⚠️ agmarknet_scraper: unparseable price in row, dropping: {row!r}")
        return None

    result = {
        "commodity_group": row.get("Commodity Group") or None,
        "commodity": row["Commodity"],
        "variety": row.get("Variety") or "",
        "grade": row.get("Grade") or "",
        "market": row["Market"],
        "district": row["District"],
        "state": row["State/UT"],
        "price_date": row.get("Price Date") or None,
        "arrival_date": row["Arrival Date"],
        "min_price": min_price,
        "max_price": max_price,
        "modal_price": modal_price,
        "price_unit": row["Price Unit"],
    }

    arrival_qty = row.get("Arrival Quantity", "").strip()
    arrival_unit = row.get("Arrival Unit", "").strip()
    if arrival_qty and arrival_qty.upper() != "NR":
        result["arrival_qty"] = arrival_qty
        result["arrival_unit"] = arrival_unit

    return result


def _install_date_override(page: Page, target_date: date) -> None:
    """Rewrites the from_date/to_date the site's own "Go" query submits to
    api.agmarknet.gov.in/v1/daily-price-arrival/report before it's sent.
    The form itself has no date field — every query hardcodes both dates to
    today — but the backend honors whatever date the request actually
    carries. A no-op for target_date == today."""
    if target_date == date.today():
        return

    target_iso = target_date.strftime("%Y-%m-%d")

    def handle(route, request):
        if request.method != "POST":
            route.continue_()
            return
        body = json.loads(request.post_data or "{}")
        body["from_date"] = target_iso
        body["to_date"] = target_iso
        route.continue_(post_data=json.dumps(body))

    page.route("**/daily-price-arrival/report", handle)


def _open_daily_price_form(page: Page, mode: str = "Price") -> None:
    """Navigation shared by every entry point that drives the live form.
    Goes straight to the form's own URL rather than clicking through the
    homepage nav (unstable animated hover menu on the live site)."""
    page.goto(
        "https://agmarknet.gov.in/daily-price-and-arrival-report",
        wait_until="networkidle",
        timeout=60000,
    )
    page.wait_for_timeout(3000)
    _pick(page, "Price/Arrivals*", mode)


def _scrape_crop_all_states(page: Page, group: str, commodity: str) -> dict[str, dict]:
    """Runs one commodity query with State/UT fixed to "All States/UTs" (set
    once by scrape_all() before looping crops), so a single query returns
    every state/UT's rows for this commodity at once."""
    _pick(page, "Commodity Group*", group)
    _pick(page, "Commodity*", commodity)
    page.keyboard.press("Escape")
    page.wait_for_timeout(400)
    _click_go(page)
    page.wait_for_timeout(500)

    rows = _scrape_visible_results_dynamic(page)
    if not rows:
        page.wait_for_timeout(1500)
        _click_go(page)
        page.wait_for_timeout(500)
        rows = _scrape_visible_results_dynamic(page)

    logger.info(f"🌾 agmarknet_scraper: {commodity} (all states) -> {len(rows)} rows")

    scraped: dict[str, dict] = {}
    for row in rows:
        result = _row_to_result(row)
        if result is None:
            continue
        key = _LAST_KNOWN_KEY_SEP.join((
            result["state"], result["district"], result["market"],
            result["commodity"], result["variety"], result["grade"],
        ))
        scraped.setdefault(key, result)
    return scraped


# How many commodities' worth of results to accumulate before flushing to
# the DB mid-run, rather than only once the whole run finishes. Chosen as a
# balance: small enough that a kill (systemd timeout, VM reboot, manual
# restart) loses at most ~this many commodities' progress instead of the
# entire day (see the 2026-08-18 incident — a 6h systemd TimeoutStartSec
# killed a run at 222/585 commodities in and every one of them was lost,
# since the old code only saved once at the very end); large enough not to
# hammer Azure SQL with a round-trip every single commodity.
_DB_CHECKPOINT_INTERVAL = 25


def _flush_to_db(batch: dict[str, dict]) -> bool:
    """Synchronously runs save_scraped() for a partial batch mid-run, in a
    brand-new dedicated thread — NOT just asyncio.run() in the calling
    thread. That was tried first and failed in production (2026-08-20):
    scrape_all() runs inside sync_playwright(), and Playwright's sync API
    bridges to its async implementation via its own event-loop machinery
    running in this same thread, so asyncio.run() here collided with it
    every time ("cannot be called from a running event loop") — every
    checkpoint failed all run, never actually saving anything mid-run.
    A fresh threading.Thread has none of Playwright's loop state attached,
    so asyncio.run() inside it is unconditionally safe. Returns False (and
    leaves the batch for the caller to retry at the next checkpoint) on
    failure, rather than silently dropping it — a transient Azure SQL
    hiccup shouldn't cost the same data loss this mechanism exists to
    prevent."""
    import threading

    outcome = {"ok": False}

    def _run_in_fresh_thread():
        import asyncio

        try:
            asyncio.run(save_scraped(batch))
            outcome["ok"] = True
        except Exception:
            logger.opt(exception=True).error(
                f"⚠️ agmarknet_scraper: incremental DB checkpoint failed for {len(batch)} "
                "entries — will retry with the next checkpoint instead of dropping them"
            )

    t = threading.Thread(target=_run_in_fresh_thread, daemon=True)
    t.start()
    t.join()
    return outcome["ok"]


def scrape_all(resume: bool = False, target_date: date | None = None, limit: int | None = None) -> dict[str, dict]:
    """Runs every commodity Agmarknet tracks against every state/UT at once
    and returns a "state||district||market||commodity||variety||grade" ->
    result dict of everything scraped this call.

    target_date defaults to today. Pass a past date to backfill it instead.

    Persists to last_known_prices.json after each commodity (via
    _merge_into_last_known_json), not just once at the end — a full run is
    hundreds of form submissions and can take hours; a mid-run crash would
    otherwise lose the entire run's progress. Also flushes to the real DB
    every _DB_CHECKPOINT_INTERVAL commodities for the same reason — the
    JSON checkpoint alone only protected the *resume* logic from a crash,
    not the actual scraped data, which used to sit in memory until one
    save_scraped() call at the very end (see _DB_CHECKPOINT_INTERVAL's
    docstring for what that cost on 2026-08-18).

    resume=True skips any crop_keyword that already has an entry dated
    target_date in last_known_prices.json.

    limit, when given, stops after that many commodities — for a quick
    smoke test of the write path, not for real backfills."""
    target_date = target_date or date.today()
    targets: dict[str, tuple[str, str]] = {}
    for group, commodities in load_commodity_reference().items():
        for commodity in commodities:
            targets[crop_keyword_for(commodity)] = (group, commodity)

    target_date_str = target_date.strftime("%d-%m-%Y")
    done_today: set[str] = set()
    if resume and _LAST_KNOWN_PATH.exists():
        existing = json.loads(_LAST_KNOWN_PATH.read_text(encoding="utf-8"))
        for key, entry in existing.items():
            if entry.get("arrival_date") == target_date_str:
                done_today.add(key.split(_LAST_KNOWN_KEY_SEP)[-1])
        if done_today:
            logger.info(
                f"⏭️ agmarknet_scraper: resuming — {len(done_today)}/{len(targets)} "
                f"commodities already have {target_date_str}'s data"
            )

    scraped: dict[str, dict] = {}
    done_count = len(done_today)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--ignore-certificate-errors"])
        page = browser.new_page(
            viewport={"width": 1400, "height": 1000},
            ignore_https_errors=True,
            user_agent=_USER_AGENT,
        )
        _install_date_override(page, target_date)
        _open_daily_price_form(page, mode="Both")
        _pick(page, "State/UT*", "All States/UTs")

        scraped_this_run = 0
        pending_batch: dict[str, dict] = {}
        for crop_keyword, (group, commodity) in targets.items():
            if crop_keyword in done_today:
                continue
            if limit is not None and scraped_this_run >= limit:
                logger.info(f"🛑 agmarknet_scraper: stopping at --limit={limit}")
                break

            try:
                commodity_scraped = _scrape_crop_all_states(page, group, commodity)
            except Exception:
                logger.opt(exception=True).warning(
                    f"⚠️ agmarknet_scraper: failed on {group}/{commodity}, reloading form and skipping"
                )
                try:
                    _open_daily_price_form(page, mode="Both")
                    _pick(page, "State/UT*", "All States/UTs")
                except Exception:
                    logger.opt(exception=True).error(
                        "⚠️ agmarknet_scraper: form recovery failed, aborting run"
                    )
                    raise
                continue

            scraped.update(commodity_scraped)
            pending_batch.update(commodity_scraped)
            if commodity_scraped:
                _merge_into_last_known_json(commodity_scraped)
            done_count += 1
            scraped_this_run += 1
            logger.info(f"📊 agmarknet_scraper: {done_count}/{len(targets)} commodities done")

            if len(pending_batch) >= _DB_CHECKPOINT_INTERVAL:
                if _flush_to_db(pending_batch):
                    logger.info(
                        f"💾 agmarknet_scraper: incremental checkpoint — {len(pending_batch)} "
                        f"entries saved ({done_count}/{len(targets)} commodities in)"
                    )
                    pending_batch = {}
                # on failure, pending_batch is deliberately left non-empty so
                # it's retried (grown, really) at the next checkpoint

            time.sleep(1.5)  # be polite between form submissions

        if pending_batch:
            _flush_to_db(pending_batch)

        browser.close()

    return scraped


# Full Agmarknet commodity taxonomy (group -> [commodity, ...]), harvested
# once from the site's own dropdowns rather than hand-picked.
_DATA_DIR = Path("data")
_COMMODITY_REFERENCE_PATH = _DATA_DIR / "agmarknet_commodities.json"
_STATE_REFERENCE_PATH = _DATA_DIR / "agmarknet_states.json"
_LAST_KNOWN_PATH = _DATA_DIR / "last_known_prices.json"
_LAST_KNOWN_LOCK_PATH = _LAST_KNOWN_PATH.with_suffix(".lock")


def harvest_commodity_reference() -> dict[str, list[str]]:
    """Walks every Commodity Group and, for each, every Commodity inside it,
    reading the dropdown's own rendered option list. Takes roughly a
    minute; meant to be run occasionally, not on every scrape."""
    reference: dict[str, list[str]] = {}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--ignore-certificate-errors"])
        page = browser.new_page(
            viewport={"width": 1400, "height": 1000},
            ignore_https_errors=True,
            user_agent=_USER_AGENT,
        )
        _open_daily_price_form(page)

        group_trigger = _open_dropdown(page, "Commodity Group*")
        groups = _enumerate_dropdown_options(page, group_trigger)
        page.keyboard.press("Escape")
        page.wait_for_timeout(300)

        for group in groups:
            _pick(page, "Commodity Group*", group)
            commodity_trigger = _open_dropdown(page, "Commodity*")
            commodities = _enumerate_dropdown_options(page, commodity_trigger)
            page.keyboard.press("Escape")
            page.wait_for_timeout(300)
            reference[group] = commodities
            logger.info(f"📚 agmarknet_scraper: {group} -> {len(commodities)} commodities")

        browser.close()

    _DATA_DIR.mkdir(exist_ok=True)
    _COMMODITY_REFERENCE_PATH.write_text(
        json.dumps(reference, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    logger.info(
        f"💾 agmarknet_scraper: wrote {sum(len(v) for v in reference.values())} "
        f"commodities across {len(reference)} groups to {_COMMODITY_REFERENCE_PATH}"
    )
    return reference


def load_commodity_reference() -> dict[str, list[str]]:
    if not _COMMODITY_REFERENCE_PATH.exists():
        return {}
    return json.loads(_COMMODITY_REFERENCE_PATH.read_text(encoding="utf-8"))


def harvest_state_reference() -> list[str]:
    """Walks the State/UT dropdown's own rendered option list once."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--ignore-certificate-errors"])
        page = browser.new_page(
            viewport={"width": 1400, "height": 1000},
            ignore_https_errors=True,
            user_agent=_USER_AGENT,
        )
        _open_daily_price_form(page)
        state_trigger = _open_dropdown(page, "State/UT*")
        states = _enumerate_dropdown_options(page, state_trigger)
        page.keyboard.press("Escape")
        browser.close()

    _DATA_DIR.mkdir(exist_ok=True)
    _STATE_REFERENCE_PATH.write_text(
        json.dumps(states, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    logger.info(f"💾 agmarknet_scraper: wrote {len(states)} states/UTs to {_STATE_REFERENCE_PATH}")
    return states


def load_state_reference() -> list[str]:
    if not _STATE_REFERENCE_PATH.exists():
        return []
    return json.loads(_STATE_REFERENCE_PATH.read_text(encoding="utf-8"))


def _merge_into_last_known_json(scraped: dict[str, dict]) -> None:
    """JSON-only checkpoint so a mid-run crash doesn't lose everything
    scraped so far — same role as the working project's version, ported
    from Windows' msvcrt file locking to POSIX fcntl.flock (this VM is
    Ubuntu). Locked and written atomically (temp file + os.replace, never
    an in-place write) — this function runs on every commodity scraped,
    hundreds of times over a multi-hour run, so with two scrape runs ever
    overlapping a collision here isn't a rare edge case. See the working
    project's own version of this function for the concurrent-write
    corruption incident (2026-08-13) this locking exists to prevent."""
    if not scraped:
        return
    _DATA_DIR.mkdir(exist_ok=True)
    _LAST_KNOWN_LOCK_PATH.touch(exist_ok=True)
    with open(_LAST_KNOWN_LOCK_PATH, "r+b") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            existing: dict[str, dict] = {}
            if _LAST_KNOWN_PATH.exists():
                existing = json.loads(_LAST_KNOWN_PATH.read_text(encoding="utf-8"))
            existing.update(scraped)
            tmp_path = _LAST_KNOWN_PATH.with_suffix(".json.tmp")
            tmp_path.write_text(
                json.dumps(dict(sorted(existing.items())), indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            tmp_path.replace(_LAST_KNOWN_PATH)
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


# One row per (state, district, market, commodity, variety, grade,
# arrival_date) — a same-day re-scrape updates that day's row, any other
# day's arrival_date is a new row, so real per-day history accumulates.
# Prices are stored exactly as reported by the site itself, no
# normalization (see _row_to_result).
#
# market is part of the key (not just state/district) — confirmed live
# (2026-08-31) that a state/district commonly has several markets all
# reporting the same commodity the same day.
#
# variety and grade are ALSO part of the key, for the same reason one
# level deeper — confirmed live (2026-08-31, Wheat: 447 CSV rows, only
# 367 landed before this fix) that a single market commonly reports
# several distinct varieties of the same commodity the same day at
# different prices. Without variety/grade in the key, scraped.setdefault()
# above and this same table's own ON CONFLICT would each independently
# collapse those down to just one, silently discarding every other
# variety's real, distinct price data — not a duplicate, a different
# product's own report. variety/grade are NOT NULL DEFAULT '' rather than
# nullable specifically so this works: Postgres treats two NULLs as never
# equal in a UNIQUE constraint, so a nullable variety/grade would let a
# same-day re-scrape of a blank-variety commodity insert a second row
# instead of updating the first, since NULL never matches NULL.
#
# mandi.market_prices lives on the shared Postgres server (not Azure SQL —
# migrated 2026-08-28, see the verification dashboard's backend/server.js
# in the Scraper_Website repo for the read side of that same move;
# restructured again 2026-08-31 to mirror agmarknet's own report columns,
# including dropping crop_keyword). market_price_id isn't part of the
# site's own column list but stays as the internal primary key.
_UPSERT_SQL = """
INSERT INTO mandi.market_prices
    (state, district, market, commodity_group, commodity, variety, grade,
     arrival_date, price_date, min_price, max_price, modal_price,
     price_unit, arrival_quantity, arrival_unit)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15)
ON CONFLICT (state, district, market, commodity, variety, grade, arrival_date) DO UPDATE SET
    commodity_group = EXCLUDED.commodity_group,
    price_date = EXCLUDED.price_date,
    min_price = EXCLUDED.min_price,
    max_price = EXCLUDED.max_price,
    modal_price = EXCLUDED.modal_price,
    price_unit = EXCLUDED.price_unit,
    arrival_quantity = EXCLUDED.arrival_quantity,
    arrival_unit = EXCLUDED.arrival_unit,
    scraped_at = (now() AT TIME ZONE 'utc');
"""


async def save_scraped(scraped: dict[str, dict]) -> None:
    """Upserts freshly scraped results into mandi.market_prices (Postgres)
    — src.services.mandi_service's price-lookup source of truth. Still ALSO
    writes last_known_prices.json (via _merge_into_last_known_json) as the
    per-commodity crash-recovery checkpoint scrape_all() itself relies on."""
    if not scraped:
        return

    _merge_into_last_known_json(scraped)

    rows = []
    for result in scraped.values():
        arrival_date = datetime.strptime(result["arrival_date"], "%d-%m-%Y").date()
        price_date = (
            datetime.strptime(result["price_date"], "%d-%m-%Y").date()
            if result.get("price_date") else None
        )
        arrival_qty = result.get("arrival_qty")
        rows.append((
            result["state"], result["district"], result["market"], result.get("commodity_group"),
            result["commodity"], result.get("variety"), result.get("grade"),
            arrival_date, price_date,
            result["min_price"], result["max_price"], result["modal_price"],
            result["price_unit"],
            float(arrival_qty.replace(",", "")) if arrival_qty else None,
            result.get("arrival_unit"),
        ))

    await execute_many(_UPSERT_SQL, rows)
    logger.info(f"💾 agmarknet_scraper: upserted {len(scraped)} fresh entries into mandi.market_prices")


if __name__ == "__main__":
    import asyncio
    import sys

    if "--harvest-commodities" in sys.argv:
        harvest_commodity_reference()
    elif "--harvest-states" in sys.argv:
        harvest_state_reference()
    else:
        target_date = None
        limit = None
        for arg in sys.argv[1:]:
            if arg.startswith("--date="):
                target_date = date.fromisoformat(arg.split("=", 1)[1])
            elif arg.startswith("--limit="):
                limit = int(arg.split("=", 1)[1])

        async def _run():
            # scrape_all() is sync (drives a real sync_playwright browser)
            # and Playwright's sync API refuses to run inside a thread that
            # already has an active asyncio loop, hence to_thread here.
            scraped = await asyncio.to_thread(
                scrape_all, resume="--resume" in sys.argv, target_date=target_date, limit=limit
            )
            await save_scraped(scraped)

        asyncio.run(_run())
