"""Drives the real agmarknet.gov.in "Daily Price and Arrival Report" page
with a real (headless) browser — filling the same State/Commodity
Group/Commodity dropdowns and clicking the same "Go" button a human would —
and reads today's prices out of the page's own "Download CSV" export
(downloading the full result set in one file, not paging through the
rendered table).

PARALLEL VERIFICATION COPY (2026-08-31): this is a from-scratch-independent
implementation, living in scraper_csv/, run alongside the existing
Web_Scraper/agmarknet_scraper.py (DOM table row-by-row + "Next page"
pagination) rather than replacing it. It writes into mandi.market_prices_v2
(a structural copy of mandi.market_prices), never into mandi.market_prices
itself, so the two scrapers can run independently and be compared —
Web_Scraper/ stays completely untouched. Cutover (retiring the old scraper,
deciding what happens to market_prices_v2's data) is a deliberate later
step once the comparison is judged successful, not automatic.

Form-driving/captcha logic is unchanged from the working copy (proven
against the live site's captcha wall and its various flaky-dropdown/
race-condition failure modes). What's different from Web_Scraper's version:
reading the DOM table row-by-row and clicking "Next page" in a loop was
where nearly all of the scraper's time and failures came from (each "Next
page" click was another chance to hit the same overlay-timeout issue
_click_go_button exists for, and a big state's commodity — Tamil Nadu's
Amaranthus was 18 pages — meant 17 extra chances to fail partway through
and lose the whole commodity for the day). Confirmed live (2026-08-31)
that "Download CSV" returns every matching row across every page in one
file, in both single-state and the production "All States/UTs" query
mode, so there's no pagination loop to fail partway through at all — see
_download_and_parse_csv. Also drops the last_known_prices.json checkpoint
entirely in favor of a direct mandi.market_prices_v2 resume-check (see
_already_scraped_crop_keywords) and flushes each commodity to the DB
immediately after it's scraped instead of accumulating several first (see
scrape_all).

Run manually with:  python -m agmarknet_scraper
"""

import csv
import base64
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

from database import execute_many, fetch_all
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
# click for its default 30s timeout before giving up. Widened from the
# original 8000/15000/4-attempts (see Web_Scraper/agmarknet_scraper.py's
# 2026-08-31 patch) since production logs showed the overlay routinely
# taking longer than that to detach, not rarely.
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
                f"⚠️ agmarknet_scraper(v2): Go click blocked by overlay "
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
            f"⚠️ agmarknet_scraper(v2): captcha rejected (attempt {attempt + 1}/{max_attempts}), refreshing and retrying"
        )
        page.click("button[aria-label='Refresh captcha']")
        page.wait_for_timeout(800)

    logger.error(f"⚠️ agmarknet_scraper(v2): captcha solving failed after {max_attempts} attempts, giving up on this query")


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
    raise RuntimeError(f"agmarknet_scraper(v2): dropdown for {label_text!r} never opened")


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
            f"⚠️ agmarknet_scraper(v2): {label_text!r} still doesn't show "
            f"{option_text!r} after selecting it (attempt {attempt + 1}/3), retrying"
        )

    raise RuntimeError(
        f"agmarknet_scraper(v2): failed to select {option_text!r} for {label_text!r} after 3 attempts"
    )


def _download_and_parse_csv(page: Page) -> list[dict[str, str]]:
    """Downloads the query's full result set via the page's own "Download
    Report" -> "Download CSV" control and parses it, instead of reading the
    DOM table row-by-row and clicking "Next page" in a loop. Confirmed live
    (2026-08-31, Tamil Nadu + Amaranthus: 176 rows single-state, 190 rows
    across 3 states in "All States/UTs" mode) that the CSV contains every
    matching row across every page in one file — not just the visible
    10-row page.

    Returns [] immediately, without attempting a download, if the query
    genuinely returned nothing: confirmed live (2026-08-31, "Cane"/Forest
    Products, a real 0-row commodity) that in that case no <table> renders
    at all and the Download Report button stays disabled — there is
    nothing to download, and clicking a disabled button would just hang
    waiting for a download event that will never fire.

    The CSV's own layout: row 0 is a title row ("Daily Price Arrival
    Report..."), row 1 is the real header (State/UT, District, Market,
    Commodity Group, Commodity, Variety, Grade, Min Price, Max Price,
    Modal Price, Price Unit, Arrival Quantity, Arrival Unit, Arrival
    Date), row 2 onward is data — one column short of the on-screen
    table's "Price Date" column, which isn't present here at all (see
    _row_to_result, which reads "Arrival Date" instead)."""
    if page.locator("table").count() == 0:
        return []

    page.locator('button[title="Download Report"]').click()
    page.wait_for_timeout(300)
    with page.expect_download(timeout=30000) as dl_info:
        page.get_by_text("Download CSV", exact=False).click()
    download = dl_info.value

    with open(download.path(), newline="", encoding="utf-8-sig") as f:
        rows = list(csv.reader(f))
    if len(rows) < 2:
        return []
    header = rows[1]
    return [dict(zip(header, r)) for r in rows[2:] if len(r) == len(header)]


def _row_to_result(row: dict[str, str]) -> dict | None:
    """Converts one CSV row into a result dict matching the site's own
    reported values (2026-08-31: no more per-kg/quintal normalization —
    min/max/modal_price are stored exactly as the site reports them,
    alongside the real price_unit). price_date duplicates arrival_date
    since the CSV export has no separate Price Date column at all (unlike
    the on-screen DOM table Web_Scraper/agmarknet_scraper.py still reads)
    — in every case observed so far the two are identical anyway."""
    try:
        min_price = float(row["Min Price"].replace(",", ""))
        max_price = float(row["Max Price"].replace(",", ""))
        modal_price = float(row["Modal Price"].replace(",", ""))
    except ValueError:
        return None

    result = {
        "commodity_group": row.get("Commodity Group") or None,
        "commodity": row["Commodity"],
        "variety": row.get("Variety") or None,
        "grade": row.get("Grade") or None,
        "market": row["Market"],
        "district": row["District"],
        "state": row["State/UT"],
        "price_date": row["Arrival Date"],
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

    rows = _download_and_parse_csv(page)
    if not rows:
        page.wait_for_timeout(1500)
        _click_go(page)
        page.wait_for_timeout(500)
        rows = _download_and_parse_csv(page)

    logger.info(f"🌾 agmarknet_scraper(v2): {commodity} (all states) -> {len(rows)} rows")

    scraped: dict[str, dict] = {}
    for row in rows:
        result = _row_to_result(row)
        if result is None:
            continue
        key = _LAST_KNOWN_KEY_SEP.join((result["state"], result["district"], result["market"], result["commodity"]))
        scraped.setdefault(key, result)
    return scraped


def _flush_to_db(batch: dict[str, dict]) -> bool:
    """Synchronously runs save_scraped() for one commodity's rows right
    after it's scraped, in a brand-new dedicated thread — NOT just
    asyncio.run() in the calling thread. scrape_all() runs inside
    sync_playwright(), and Playwright's sync API bridges to its async
    implementation via its own event-loop machinery running in this same
    thread, so asyncio.run() here would collide with it ("cannot be called
    from a running event loop"). A fresh threading.Thread has none of
    Playwright's loop state attached, so asyncio.run() inside it is
    unconditionally safe. Returns False (and leaves the batch for the
    caller to retry) on failure, rather than silently dropping it."""
    import threading

    outcome = {"ok": False}

    def _run_in_fresh_thread():
        import asyncio

        try:
            asyncio.run(save_scraped(batch))
            outcome["ok"] = True
        except Exception:
            logger.opt(exception=True).error(
                f"⚠️ agmarknet_scraper(v2): DB checkpoint failed for {len(batch)} "
                "entries — will retry with the next commodity's flush instead of dropping them"
            )

    t = threading.Thread(target=_run_in_fresh_thread, daemon=True)
    t.start()
    t.join()
    return outcome["ok"]


def _already_scraped_crop_keywords(target_date: date) -> set[str]:
    """Queries mandi.market_prices_v2 directly for which commodities
    already have a row dated target_date, and normalizes each one through
    crop_keyword_for() to get back a set comparable against targets'
    own keys — mandi.market_prices_v2 has no crop_keyword column (dropped
    2026-08-31 in favor of the raw commodity name as part of the upsert
    key), so this normalization now happens on the fly here instead of
    being read back from a stored column. Reflects the real, current
    state of this scraper's own table rather than a local file that can
    drift from it."""
    import threading

    result: set[str] = set()

    def _run_in_fresh_thread():
        import asyncio

        async def _query():
            rows = await fetch_all(
                "SELECT DISTINCT commodity FROM mandi.market_prices_v2 WHERE arrival_date = $1",
                target_date,
            )
            result.update(crop_keyword_for(r["commodity"]) for r in rows)

        try:
            asyncio.run(_query())
        except Exception:
            logger.opt(exception=True).warning(
                "⚠️ agmarknet_scraper(v2): resume gap-check query failed — "
                "proceeding as if nothing is done yet (a full run, not a partial-skip crash)"
            )

    t = threading.Thread(target=_run_in_fresh_thread, daemon=True)
    t.start()
    t.join()
    return result


def scrape_all(resume: bool = False, target_date: date | None = None, limit: int | None = None) -> dict[str, dict]:
    """Runs every commodity Agmarknet tracks against every state/UT at once
    and returns a "state||district||market||commodity" -> result dict of
    everything scraped this call.

    target_date defaults to today. Pass a past date to backfill it instead.

    Flushes each commodity's rows to mandi.market_prices_v2 immediately
    after it's scraped, rather than accumulating several commodities'
    worth first — a single "All States/UTs" query already returns however
    many rows the whole country reported that commodity (Amaranthus alone
    was 190 rows in one query, confirmed 2026-08-31), so each commodity's
    result is already a naturally-sized batch, and this bounds a mid-run
    crash to losing at most the one commodity in flight.

    resume=True skips any crop_keyword that already has a row dated
    target_date in mandi.market_prices_v2.

    limit, when given, stops after that many commodities — for a quick
    smoke test of the write path, not for real backfills."""
    target_date = target_date or date.today()
    targets: dict[str, tuple[str, str]] = {}
    for group, commodities in load_commodity_reference().items():
        for commodity in commodities:
            targets[crop_keyword_for(commodity)] = (group, commodity)

    done_today: set[str] = set()
    if resume:
        done_today = _already_scraped_crop_keywords(target_date)
        if done_today:
            logger.info(
                f"⏭️ agmarknet_scraper(v2): resuming — {len(done_today)}/{len(targets)} "
                f"commodities already have {target_date.isoformat()}'s data in mandi.market_prices_v2"
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
        failed_flushes: dict[str, dict] = {}
        for crop_keyword, (group, commodity) in targets.items():
            if crop_keyword in done_today:
                continue
            if limit is not None and scraped_this_run >= limit:
                logger.info(f"🛑 agmarknet_scraper(v2): stopping at --limit={limit}")
                break

            try:
                commodity_scraped = _scrape_crop_all_states(page, group, commodity)
            except Exception:
                logger.opt(exception=True).warning(
                    f"⚠️ agmarknet_scraper(v2): failed on {group}/{commodity}, reloading form and skipping"
                )
                try:
                    _open_daily_price_form(page, mode="Both")
                    _pick(page, "State/UT*", "All States/UTs")
                except Exception:
                    logger.opt(exception=True).error(
                        "⚠️ agmarknet_scraper(v2): form recovery failed, aborting run"
                    )
                    raise
                continue

            scraped.update(commodity_scraped)
            done_count += 1
            scraped_this_run += 1
            logger.info(f"📊 agmarknet_scraper(v2): {done_count}/{len(targets)} commodities done")

            if commodity_scraped:
                to_flush = {**failed_flushes, **commodity_scraped}
                if _flush_to_db(to_flush):
                    failed_flushes = {}
                else:
                    failed_flushes = to_flush

            time.sleep(1.5)  # be polite between form submissions

        if failed_flushes:
            _flush_to_db(failed_flushes)

        browser.close()

    return scraped


# Full Agmarknet commodity taxonomy (group -> [commodity, ...]), harvested
# once from the site's own dropdowns rather than hand-picked. Shared,
# read-only reference data — copied from Web_Scraper/data/, not
# regenerated here.
_DATA_DIR = Path("data")
_COMMODITY_REFERENCE_PATH = _DATA_DIR / "agmarknet_commodities.json"


def load_commodity_reference() -> dict[str, list[str]]:
    if not _COMMODITY_REFERENCE_PATH.exists():
        return {}
    return json.loads(_COMMODITY_REFERENCE_PATH.read_text(encoding="utf-8"))


# One row per (state, district, market, commodity, arrival_date) — a
# same-day re-scrape updates that day's row, any other day's arrival_date
# is a new row, so real per-day history accumulates. Prices are stored
# exactly as reported by the site itself, no normalization (see
# _row_to_result). market IS part of the key (not just state/district) —
# confirmed live (2026-08-31) that a state/district commonly has several
# markets all reporting the same commodity the same day (Bajra:
# Gujarat/Banaskanth alone had 5 separate markets), and without market in
# the key, scraped.setdefault() above and this same table's own ON
# CONFLICT would each independently collapse them down to just one,
# silently discarding every other market's real, distinct price data —
# not a duplicate, a different market's own report.
#
# mandi.market_prices_v2 has a UNIQUE constraint on (state, district,
# market, commodity, arrival_date) as the upsert key (crop_keyword dropped
# 2026-08-31, along with restructuring the rest of the table to mirror
# agmarknet's own report columns — see create_v2_table.py/
# reorder_columns.py), matching mandi.market_prices' own structure
# exactly.
_UPSERT_SQL = """
INSERT INTO mandi.market_prices_v2
    (state, district, market, commodity_group, commodity, variety, grade,
     arrival_date, price_date, min_price, max_price, modal_price,
     price_unit, arrival_quantity, arrival_unit)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15)
ON CONFLICT (state, district, market, commodity, arrival_date) DO UPDATE SET
    commodity_group = EXCLUDED.commodity_group,
    variety = EXCLUDED.variety,
    grade = EXCLUDED.grade,
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
    """Upserts freshly scraped results into mandi.market_prices_v2
    (Postgres) — the parallel-verification table, not mandi.market_prices."""
    if not scraped:
        return

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
    logger.info(f"💾 agmarknet_scraper(v2): upserted {len(scraped)} fresh entries into mandi.market_prices_v2")


if __name__ == "__main__":
    import asyncio
    import sys

    target_date = None
    limit = None
    for arg in sys.argv[1:]:
        if arg.startswith("--date="):
            target_date = date.fromisoformat(arg.split("=", 1)[1])
        elif arg.startswith("--limit="):
            limit = int(arg.split("=", 1)[1])

    async def _run():
        # scrape_all() is sync (drives a real sync_playwright browser) and
        # Playwright's sync API refuses to run inside a thread that
        # already has an active asyncio loop, hence to_thread here. No
        # final save_scraped(scraped) call needed afterward — every
        # commodity is already flushed to mandi.market_prices_v2 as it's
        # scraped (see scrape_all's per-commodity _flush_to_db).
        scraped = await asyncio.to_thread(
            scrape_all, resume="--resume" in sys.argv, target_date=target_date, limit=limit
        )
        logger.info(f"✅ agmarknet_scraper(v2): run complete — {len(scraped)} entries scraped this run")

    asyncio.run(_run())
