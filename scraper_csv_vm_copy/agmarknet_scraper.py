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
_already_scraped_commodities) and flushes each commodity to the DB
immediately after it's scraped instead of accumulating several first (see
scrape_all).

UPDATE 2026-09-02: Web_Scraper/ (the DOM-pagination original) has been
retired — this scraper won the comparison on both robustness (above) and
data accuracy (spot-checked matching rows: Web_Scraper's on-screen table
carried floating-point-averaged prices for ~2% of rows where this
scraper's CSV export gave the clean per-row reported value) and is now
the only one running. Also gained: strictly group-ordered scraping with
num_workers concurrent browser sessions WITHIN each group rather than
across the whole commodity list (see scrape_all/_scrape_group_parallel) —
finishes faster than one worker without ever having two different groups
in flight at once.

Run manually with:  python -m agmarknet_scraper [--workers=N]
"""

import csv
import os
import base64
import io
import json
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv
from loguru import logger
from PIL import Image, ImageOps
from playwright.sync_api import Locator, Page, sync_playwright

load_dotenv()

from database import execute_many, fetch_all
from price_shared import _LAST_KNOWN_KEY_SEP

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# Agmarknet requires a captcha on every "Go" submission. Solved with OCR
# rather than any external captcha-solving service — the image is a clean,
# low-noise 6-character alphanumeric render with no CAPTCHA challenge beyond
# that.
_CAPTCHA_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"

# Opt-in OCR-accuracy diagnostic. With AGMARKNET_CAPTCHA_DEBUG set, every
# captcha attempt (accepted OR rejected) is written to captcha_debug/ as
# <ts>_<guess>_<ok|reject>_raw.png (exactly what the site served) and
# _prep.png (what OCR actually saw after _preprocess_captcha_image),
# plus one line per attempt in captcha_debug/samples.jsonl. Off by
# default: a full run makes thousands of Go submissions and well over
# half the individual OCR reads are wrong, so this fills disk fast — it's
# for a targeted "which glyphs is OCR missing" investigation, not
# always-on. The rejected-guess is also logged inline (see _click_go) so
# journalctl shows what OCR read even with this off.
_CAPTCHA_DEBUG_DIR = Path("captcha_debug")
_CAPTCHA_DEBUG = bool(os.environ.get("AGMARKNET_CAPTCHA_DEBUG"))


def _save_captcha_sample(
    guess: str, raw_png: bytes, prep_png: bytes, accepted: bool, attempt: int
) -> None:
    if not _CAPTCHA_DEBUG:
        return
    try:
        _CAPTCHA_DEBUG_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.utcnow().strftime("%Y%m%dT%H%M%S_%f")
        safe_guess = "".join(c for c in guess if c.isalnum()) or "EMPTY"
        tag = "ok" if accepted else "reject"
        (_CAPTCHA_DEBUG_DIR / f"{stamp}_{safe_guess}_{tag}_raw.png").write_bytes(raw_png)
        (_CAPTCHA_DEBUG_DIR / f"{stamp}_{safe_guess}_{tag}_prep.png").write_bytes(prep_png)
        with (_CAPTCHA_DEBUG_DIR / "samples.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(
                {"ts": stamp, "guess": guess, "accepted": accepted, "attempt": attempt}
            ) + "\n")
    except Exception:
        logger.opt(exception=True).warning(
            "⚠️ agmarknet_scraper(v2): couldn't save captcha debug sample"
        )

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


def _new_ocr_reader():
    """Constructs a fresh EasyOCR reader instance. Used to be a
    module-level lazy singleton shared by every Go click in a run — fine
    when only one browser session was ever active at a time, but within-
    group parallelism (2026-09-02, see _scrape_group_parallel) now runs
    several worker threads concurrently, and a single Reader isn't safe
    for concurrent .readtext() calls from multiple threads. Each worker
    thread constructs and owns exactly one of these for its whole
    lifetime instead — model load (~seconds) still only happens once per
    worker, same amortized cost as the old singleton had per run."""
    import easyocr

    return easyocr.Reader(["en"], gpu=False, verbose=False)


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


def _solve_captcha(page: Page, reader) -> tuple[str, bytes, bytes]:
    """Reads the captcha's base64 image data straight off the <img> element
    (no extra network round-trip), preprocesses it (see
    _preprocess_captcha_image), and fills its OCR reading into the
    #captcha field. Not reliable on its own — see _click_go, which is the
    real entry point and retries this against a freshly refreshed image on
    rejection, since even a cleaned-up image doesn't get every glyph right
    every time. `reader` is the calling worker's own OCR reader instance
    (see _new_ocr_reader) — never shared across concurrent workers.

    Returns (guess, raw_png, preprocessed_png) so _click_go can archive
    the samples the site rejects (AGMARKNET_CAPTCHA_DEBUG) for later
    OCR-accuracy analysis — the raw bytes are gone otherwise, decoded and
    thrown away inside this call."""
    src = page.locator("img[alt='Captcha']").get_attribute("src")
    _, b64data = src.split(",", 1)
    raw_png = base64.b64decode(b64data)
    prep_png = _preprocess_captcha_image(raw_png)

    guess = "".join(reader.readtext(prep_png, detail=0, allowlist=_CAPTCHA_ALPHABET))
    page.fill("#captcha", guess)
    return guess, raw_png, prep_png


def _click_go(page: Page, reader, max_attempts: int = 12) -> None:
    """Solves the captcha and clicks Go, retrying against a freshly
    refreshed captcha image whenever the site rejects the guess. The
    #captcha input's own aria-invalid attribute is the accept/reject signal
    — flips to "true" on a rejected submission and back to "false" once one
    is accepted. OCR gets this captcha's ambiguous 6-character glyphs right
    well under half the time, so max_attempts is generously sized rather
    than tuned tight."""
    for attempt in range(max_attempts):
        guess, raw_png, prep_png = _solve_captcha(page, reader)
        _click_go_button(page)

        try:
            page.wait_for_function(
                "() => document.querySelector('#captcha')?.getAttribute('aria-invalid') === 'true' "
                "|| document.querySelectorAll('table tbody tr').length > 0",
                timeout=2000,
            )
        except Exception:
            pass

        accepted = page.locator("#captcha").get_attribute("aria-invalid") == "false"
        _save_captcha_sample(guess, raw_png, prep_png, accepted, attempt + 1)
        if accepted:
            return

        logger.warning(
            f"⚠️ agmarknet_scraper(v2): captcha rejected (OCR read {guess!r}, "
            f"attempt {attempt + 1}/{max_attempts}), refreshing and retrying"
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


_CSV_ARCHIVE_DIR = Path("downloaded_csvs")


def _archive_filename(commodity: str, target_date: date) -> Path:
    safe_commodity = "".join(c if c.isalnum() else "_" for c in commodity).strip("_")
    day_dir = _CSV_ARCHIVE_DIR / target_date.isoformat()
    day_dir.mkdir(parents=True, exist_ok=True)
    return day_dir / f"{safe_commodity}.csv"


# Azure Blob mirror of the local downloaded_csvs/ archive (2026-09-02) —
# same <date>/<commodity>.csv layout, so a row in mandi.market_prices_v2
# can be checked against its exact source CSV without SSHing into the VM
# at all, from anywhere. get_container_client()'s ContainerClient is
# documented thread-safe for concurrent use, so this one instance is
# shared by every worker thread in _scrape_group_parallel rather than
# each constructing its own (unlike _new_ocr_reader — the OCR model
# itself isn't safe for concurrent inference, but this is a plain HTTP
# client). Lazy + memoized so import doesn't require the env vars to be
# set (e.g. --harvest-commodities never touches blob storage at all).
_blob_container = None


def _get_blob_container():
    global _blob_container
    if _blob_container is None:
        from azure.storage.blob import ContainerClient

        _blob_container = ContainerClient.from_connection_string(
            os.environ["AZURE_STORAGE_CONNECTION_STRING"],
            container_name=os.environ["AZURE_STORAGE_CONTAINER"],
        )
    return _blob_container


def _upload_to_blob(local_path: Path, commodity: str, target_date: date) -> bool:
    """Mirrors one archived CSV up to Azure Blob Storage at
    <date>/<commodity>.csv, same naming as the local archive. Best-effort:
    logs and returns False on failure rather than raising — a blob upload
    hiccup (network blip, throttling) shouldn't cost a whole commodity's
    worth of scraping/DB-write work that already succeeded locally.
    Returns True only on a confirmed-successful upload — the caller (see
    _download_and_parse_csv) uses this to decide whether the local copy
    is safe to delete, so a False here has real consequences: it means
    the local file is the ONLY remaining copy and must be kept."""
    safe_commodity = "".join(c if c.isalnum() else "_" for c in commodity).strip("_")
    blob_name = f"{target_date.isoformat()}/{safe_commodity}.csv"
    try:
        container = _get_blob_container()
        with open(local_path, "rb") as f:
            container.upload_blob(blob_name, f, overwrite=True)
        return True
    except Exception:
        logger.opt(exception=True).warning(
            f"⚠️ agmarknet_scraper(v2): blob upload failed for {blob_name} — "
            "keeping the local archive copy since it's now the only one"
        )
        return False


def _download_and_parse_csv(page: Page, commodity: str, target_date: date) -> list[dict[str, str]]:
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
    _row_to_result, which reads "Arrival Date" instead).

    Saves a copy of every downloaded CSV to
    downloaded_csvs/<date>/<commodity>.csv before parsing it (2026-09-01)
    — previously read straight from Playwright's own auto-cleaned temp
    download dir via download.path(), so once the browser closed there was
    no way to go back and inspect what the scraper had actually pulled for
    a given commodity/day. This is real evidence of what got scraped, not
    just what the DB ended up with after parsing/dedup — worth keeping.

    That local copy is only temporary now, though (2026-09-02): once it's
    mirrored to Azure Blob Storage (see _upload_to_blob) AND parsed, it's
    deleted from local disk — the blob is the permanent archive, local
    disk is just staging so the VM doesn't accumulate every CSV from
    every day forever. Kept locally only if the blob upload itself
    failed, so there's never a moment where a commodity's raw CSV exists
    nowhere at all."""
    if page.locator("table").count() == 0:
        return []

    page.locator('button[title="Download Report"]').click()
    page.wait_for_timeout(300)
    with page.expect_download(timeout=30000) as dl_info:
        page.get_by_text("Download CSV", exact=False).click()
    download = dl_info.value

    archive_path = _archive_filename(commodity, target_date)
    download.save_as(str(archive_path))
    uploaded = _upload_to_blob(archive_path, commodity, target_date)

    with open(archive_path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.reader(f))

    # Local disk is just staging once the blob has a confirmed copy — see
    # _download_and_parse_csv's own docstring. Deleted here, right after
    # reading it (not before — still need it on disk to open() above),
    # rather than left to be cleaned up separately, so there's no window
    # where a run could crash and leave stale local copies behind for
    # commodities that already succeeded in the blob.
    if uploaded:
        archive_path.unlink(missing_ok=True)

    if len(rows) < 2:
        return []
    header = rows[1]

    if "State/UT" not in header or "Commodity" not in header:
        logger.error(
            f"⚠️ agmarknet_scraper(v2): CSV header doesn't look like the expected "
            f"layout (missing State/UT or Commodity) — got {header!r}. The site's "
            f"export format may have changed; parsing would silently misalign "
            f"every row if the wrong line got treated as the header."
        )

    parsed = []
    dropped = 0
    for r in rows[2:]:
        if len(r) == len(header):
            parsed.append(dict(zip(header, r)))
        else:
            # A row whose column count doesn't match the header used to be
            # silently discarded here with no trace at all — the most
            # likely cause is an unescaped comma inside a market/commodity
            # name in the site's own CSV export, which throws csv.reader's
            # column count off for just that one row. Logging every drop
            # (not just counting them) so a real occurrence is
            # investigable instead of invisibly eating a row.
            dropped += 1
            logger.warning(
                f"⚠️ agmarknet_scraper(v2): dropping malformed CSV row "
                f"(expected {len(header)} columns, got {len(r)}): {r!r}"
            )
    if dropped:
        logger.warning(f"⚠️ agmarknet_scraper(v2): {dropped} malformed row(s) dropped out of {len(rows) - 2} total")
    return parsed


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
        # Used to return None here with no trace at all -- logging so an
        # unparseable price (a stray non-numeric value the site
        # occasionally reports) is investigable instead of invisibly
        # eating a row.
        logger.warning(f"⚠️ agmarknet_scraper(v2): unparseable price in row, dropping: {row!r}")
        return None

    result = {
        "commodity_group": row.get("Commodity Group") or None,
        "commodity": row["Commodity"],
        "variety": row.get("Variety") or "",
        "grade": row.get("Grade") or "",
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


def _scrape_crop_all_states(page: Page, group: str, commodity: str, target_date: date, reader) -> dict[str, dict]:
    """Runs one commodity query with State/UT fixed to "All States/UTs" (set
    once by the caller before looping crops), so a single query returns
    every state/UT's rows for this commodity at once. `reader` is the
    calling worker's own OCR reader (see _new_ocr_reader)."""
    _pick(page, "Commodity Group*", group)
    _pick(page, "Commodity*", commodity)
    page.keyboard.press("Escape")
    page.wait_for_timeout(400)
    _click_go(page, reader)
    page.wait_for_timeout(500)

    # Up to 3 total tries before accepting an empty result as genuine —
    # matches the outer per-commodity exception retry's own 3-attempt
    # budget (2026-09-01). This path used to stop at 2 total tries even
    # though the site is documented (see below) to sometimes return 0
    # rows on one attempt and 10+ on the very next for the identical
    # query — a plain empty dict isn't an exception, so it never benefited
    # from scrape_all's own retry-the-whole-commodity loop; it needed its
    # own matching resilience here instead.
    rows = _download_and_parse_csv(page, commodity, target_date)
    attempt = 1
    while not rows and attempt < 3:
        attempt += 1
        # An empty result is sometimes a genuine "nothing reported today"
        # and sometimes a transient site hiccup around the Go click — this
        # exact (state, crop) combo has been observed to return 0 rows on
        # one run and 10+ on the next with no code change in between.
        page.wait_for_timeout(1500)
        _click_go(page, reader)
        page.wait_for_timeout(500)
        rows = _download_and_parse_csv(page, commodity, target_date)

    logger.info(f"🌾 agmarknet_scraper(v2): {commodity} (all states) -> {len(rows)} rows")

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


def _already_scraped_commodities(target_date: date) -> set[str]:
    """Queries mandi.market_prices_v2 directly for which raw commodity
    names already have a row dated target_date. Compares against targets'
    own raw commodity names now (2026-09-01) rather than crop_keyword_for()
    normalizations of them — crop_keyword_for() strips everything after
    "(", which collapses genuinely distinct commodities that happen to
    share a prefix (confirmed live: 'Paddy(Basmati)' and 'Paddy(Common)'
    both normalize to 'paddy', 19 such collisions across the full
    taxonomy). Using it for resume comparison meant that once ONE of a
    colliding pair was scraped, --resume would treat the OTHER as already
    done too and skip it, even though it had never actually been queried.
    Reflects the real, current state of this scraper's own table rather
    than a local file that can drift from it."""
    import threading

    result: set[str] = set()

    def _run_in_fresh_thread():
        import asyncio

        async def _query():
            rows = await fetch_all(
                "SELECT DISTINCT commodity FROM mandi.market_prices_v2 WHERE arrival_date = $1",
                target_date,
            )
            result.update(r["commodity"] for r in rows)

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


# When mandi.market_prices_v2 is empty, _resolve_target_date has no
# earliest-existing-date to start walking forward from — falls back to
# this instead of date.today(), so a wipe-and-restart resumes the
# in-progress backfill at 2026-09-01 (explicitly requested 2026-09-02)
# rather than skipping straight to whatever "today" happens to be.
_CAMPAIGN_START_DATE = date(2026, 9, 1)


def _resolve_target_date() -> date:
    """Picks which date to scrape when no --date= override is given: the
    earliest date, at or after the earliest row already in
    mandi.market_prices_v2, that hasn't been recorded as a fully-attempted
    scrape (data/completed_dates.json, written by _mark_date_completed);
    or _CAMPAIGN_START_DATE if the table's empty; or today if every date
    up to today is already done. Never returns a later date while an
    earlier one is still unfinished.

    Completion is an explicit marker, NOT "every taxonomy commodity has a
    row for this date". Hundreds of the ~605 taxonomy commodities report
    nothing on any given day (out-of-season produce, untraded livestock,
    obscure forest products), so a row-count test could never be met — it
    pinned the daemon to a single date indefinitely while newer days' rows
    landed under the wrong arrival_date (observed 2026-09-02; replaced
    with the marker 2026-09-03). The pre-2026-09-02 default
    (target_date = date.today()) had no gate at all and rolled to tomorrow
    the moment the clock ticked over, mid-run."""
    import threading

    earliest: list[date | None] = [None]

    def _run_in_fresh_thread():
        import asyncio

        async def _query():
            rows = await fetch_all("SELECT MIN(arrival_date) AS d FROM mandi.market_prices_v2")
            earliest[0] = rows[0]["d"] if rows and rows[0]["d"] else None

        try:
            asyncio.run(_query())
        except Exception:
            logger.opt(exception=True).warning(
                "⚠️ agmarknet_scraper(v2): earliest-date query failed — defaulting to today"
            )

    t = threading.Thread(target=_run_in_fresh_thread, daemon=True)
    t.start()
    t.join()

    completed = _load_completed_dates()
    d = earliest[0] or _CAMPAIGN_START_DATE
    while d < date.today():
        if d.isoformat() not in completed:
            return d
        d += timedelta(days=1)
    return d


def _scrape_group_parallel(
    group: str,
    commodities: list[str],
    target_date: date,
    num_workers: int,
    limit_state: dict | None,
    progress: dict,
) -> tuple[dict[str, dict], bool]:
    """Scrapes every commodity in ONE commodity group using num_workers
    concurrent browser sessions (own browser, own page, own OCR reader
    each — see _new_ocr_reader), pulling commodities off a shared queue
    until it's drained. Added 2026-09-02 to cut wall-clock time roughly in
    half (measured: ~24/hr with one worker, ~47/hr with two) without ever
    running two DIFFERENT groups at once — scrape_all's own loop only
    calls this one group at a time, in taxonomy order, and never starts
    the next group until this one's queue is fully drained. Trade-off:
    which commodity WITHIN this group lands in the DB first is now
    whichever worker's captcha luck ran out first, not strictly the
    taxonomy's own list order within the group — only the group
    boundaries stay exactly as ordered as a single-worker run would be.

    limit_state, when given, is a shared {"lock", "remaining"} dict (see
    scrape_all) that every worker decrements after claiming a commodity —
    lets --limit's smoke-test budget span all workers instead of each one
    having its own separate count.

    progress is a shared {"lock", "done", "total"} dict so the
    "X/Y commodities done" log line stays a single running count across
    every group and worker in this run, matching what the old single-
    worker loop printed, instead of restarting from 0 each group.

    Returns (group_scraped, clean): clean is False if any commodity in the
    group was given up on after its retries, a worker aborted mid-queue,
    or a scraped commodity failed to persist — i.e. the group can't be
    treated as fully done for this date. scrape_all uses that to decide
    whether the whole date earns its completion marker."""
    import queue
    import threading

    work_queue: queue.Queue[str] = queue.Queue()
    for commodity in commodities:
        work_queue.put(commodity)

    # Set by any worker on an unrecoverable per-commodity or per-worker
    # failure — flips this group's returned `clean` to False.
    had_failure = threading.Event()

    results_lock = threading.Lock()
    group_scraped: dict[str, dict] = {}

    def _worker(worker_id: int) -> None:
        reader = _new_ocr_reader()
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

            while True:
                try:
                    commodity = work_queue.get_nowait()
                except queue.Empty:
                    break

                if limit_state is not None:
                    with limit_state["lock"]:
                        if limit_state["remaining"] <= 0:
                            break
                        limit_state["remaining"] -= 1

                # Retries the WHOLE commodity (not just the Go click) up to
                # 3 total attempts before giving up on it for the day —
                # see the pre-2026-09-02 single-worker version of this
                # loop for why (38 commodities lost to a single Go-click
                # failure, confirmed 2026-09-01).
                commodity_scraped = None
                for attempt in range(1, 4):
                    try:
                        commodity_scraped = _scrape_crop_all_states(page, group, commodity, target_date, reader)
                        break
                    except Exception:
                        logger.opt(exception=True).warning(
                            f"⚠️ agmarknet_scraper(v2): worker {worker_id} failed on {group}/{commodity} "
                            f"(attempt {attempt}/3), reloading form and retrying"
                        )
                        try:
                            _open_daily_price_form(page, mode="Both")
                            _pick(page, "State/UT*", "All States/UTs")
                        except Exception:
                            logger.opt(exception=True).error(
                                f"⚠️ agmarknet_scraper(v2): worker {worker_id} form recovery failed, "
                                "aborting this worker (its remaining queued commodities will be picked "
                                "up by a future --resume run, not lost)"
                            )
                            had_failure.set()
                            browser.close()
                            return

                if commodity_scraped is None:
                    had_failure.set()
                    logger.error(
                        f"⚠️ agmarknet_scraper(v2): worker {worker_id} giving up on {group}/{commodity} "
                        "after 3 attempts, skipping"
                    )

                with progress["lock"]:
                    progress["done"] += 1
                    logger.info(f"📊 agmarknet_scraper(v2): {progress['done']}/{progress['total']} commodities done")

                if commodity_scraped:
                    with results_lock:
                        group_scraped.update(commodity_scraped)
                    if not _flush_to_db(commodity_scraped):
                        had_failure.set()
                        logger.error(
                            f"⚠️ agmarknet_scraper(v2): worker {worker_id} DB flush failed for {commodity} — "
                            "will be picked up by a future --resume run instead of retried here"
                        )

                time.sleep(1.5)  # be polite between form submissions

            browser.close()

    threads = [threading.Thread(target=_worker, args=(i,)) for i in range(num_workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    return group_scraped, not had_failure.is_set()


def scrape_all(
    resume: bool = False,
    target_date: date | None = None,
    limit: int | None = None,
    num_workers: int = 2,
) -> dict[str, dict]:
    """Runs every commodity Agmarknet tracks against every state/UT at once
    and returns a "state||district||market||commodity||variety||grade" ->
    result dict of everything scraped this call.

    target_date defaults to the earliest date that isn't yet fully scraped
    (see _resolve_target_date) rather than always today — a slow run never
    jumps to a new date while an earlier one is still incomplete. Pass an
    explicit date to backfill or force one instead.

    Processes commodity groups strictly in taxonomy order — Beverages
    fully done before Cereals starts, Cereals before Drug and Narcotics,
    and so on — via _scrape_group_parallel, which runs num_workers
    concurrent browser sessions WITHIN each group (added 2026-09-02: an
    earlier version sharded the whole commodity list up front across
    parallel OS processes, which finished faster but meant two different
    groups' commodities were being scraped at the same time — e.g. some
    Cereals landing in the DB interleaved with some Drug and Narcotics —
    which looked like commodities were being skipped/scraped out of
    order. This version never starts a new group until the previous one's
    entire commodity list is done, at the cost of some speed: only the
    order WITHIN a group can vary now, by worker completion time rather
    than the taxonomy's own list order, never across groups).

    A plain per-group list, not a dict keyed by crop_keyword_for(commodity)
    -- that used to silently drop 19 real, distinct commodities out of 605
    in the full taxonomy (confirmed 2026-09-01), since crop_keyword_for()
    strips everything after "(" and several genuinely different
    commodities share a prefix (Mango vs Mango(Raw-Ripe), Paddy(Basmati)
    vs Paddy(Common), 3 Rose varieties, 3 Tube Rose varieties, etc.) --
    whichever one happened to be assigned to that dict key last silently
    overwrote the other, which then never got queried at all, run after
    run. Every (group, commodity) pair from the site's own taxonomy is
    now queried independently, with no deduplication by any derived key.

    Flushes each commodity's rows to mandi.market_prices_v2 immediately
    after it's scraped, rather than accumulating several commodities'
    worth first — a single "All States/UTs" query already returns however
    many rows the whole country reported that commodity (Amaranthus alone
    was 190 rows in one query, confirmed 2026-08-31), so each commodity's
    result is already a naturally-sized batch, and this bounds a crash to
    losing at most whatever's in flight across the active workers.

    resume=True skips any commodity that already has a row dated
    target_date in mandi.market_prices_v2.

    On a run that walks the whole taxonomy cleanly (every group's workers
    drained, nothing given up on, no --limit cutoff), target_date is
    written to data/completed_dates.json via _mark_date_completed — that
    marker, not a per-commodity row count, is how _resolve_target_date
    knows the date is done and the next call can move to the following
    day.

    limit, when given, stops after roughly that many commodities total
    across all workers — for a quick smoke test of the write path, not
    for real backfills; not exact under concurrency (a worker that's
    already claimed a commodity finishes it even if the budget hits 0
    mid-group), just an upper bound."""
    import threading

    target_date = target_date or _resolve_target_date()
    commodity_reference = load_commodity_reference()
    total_commodities = sum(len(commodities) for commodities in commodity_reference.values())

    done_today: set[str] = set()
    if resume:
        done_today = _already_scraped_commodities(target_date)
        if done_today:
            logger.info(
                f"⏭️ agmarknet_scraper(v2): resuming — {len(done_today)}/{total_commodities} "
                f"commodities already have {target_date.isoformat()}'s data in mandi.market_prices_v2"
            )

    progress = {"lock": threading.Lock(), "done": len(done_today), "total": total_commodities}
    limit_state = {"lock": threading.Lock(), "remaining": limit} if limit is not None else None

    scraped: dict[str, dict] = {}
    full_pass_clean = True
    for group, commodities in commodity_reference.items():
        remaining = [c for c in commodities if c not in done_today]
        if not remaining:
            continue
        if limit_state is not None and limit_state["remaining"] <= 0:
            logger.info(f"🛑 agmarknet_scraper(v2): stopping at --limit={limit}")
            full_pass_clean = False
            break

        logger.info(f"📂 agmarknet_scraper(v2): starting group {group!r} ({len(remaining)} commodities)")
        group_scraped, group_clean = _scrape_group_parallel(
            group, remaining, target_date,
            num_workers=num_workers, limit_state=limit_state, progress=progress,
        )
        scraped.update(group_scraped)
        full_pass_clean = full_pass_clean and group_clean

    # A date only earns its completion marker — which is what lets
    # _resolve_target_date move past it to the next day — when this run
    # actually walked the whole taxonomy for it: every group, every worker
    # draining cleanly, no per-commodity give-up, no --limit cutoff.
    # Anything less and the date stays eligible for another --resume pass.
    # A --limit smoke test never counts, even if it happens to finish,
    # since it only ever touches a handful of commodities on purpose.
    if limit is None and full_pass_clean and commodity_reference:
        _mark_date_completed(target_date)

    return scraped


# Full Agmarknet commodity taxonomy (group -> [commodity, ...]), harvested
# once from the site's own dropdowns rather than hand-picked. Originally
# just copied in from Web_Scraper/data/ with no way to refresh it from
# within this project -- harvest_commodity_reference() below (ported back
# in 2026-09-01) closes that gap, so a stale/changed taxonomy on the live
# site (a commodity added, renamed, or removed since the last harvest)
# doesn't silently drift undetected. Run with:
#   python -m agmarknet_scraper --harvest-commodities
_DATA_DIR = Path("data")
_COMMODITY_REFERENCE_PATH = _DATA_DIR / "agmarknet_commodities.json"


def harvest_commodity_reference() -> dict[str, list[str]]:
    """Walks every Commodity Group and, for each, every Commodity inside it,
    reading the dropdown's own rendered option list rather than assuming
    the existing agmarknet_commodities.json is still accurate. Takes
    roughly a minute (16 groups); meant to be run occasionally, not on
    every scrape. Overwrites the existing reference file -- run this
    before a --resume or full run if you suspect the site's taxonomy has
    changed, not mid-run."""
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
            logger.info(f"📚 agmarknet_scraper(v2): {group} -> {len(commodities)} commodities")

        browser.close()

    _DATA_DIR.mkdir(exist_ok=True)
    _COMMODITY_REFERENCE_PATH.write_text(
        json.dumps(reference, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    logger.info(
        f"💾 agmarknet_scraper(v2): wrote {sum(len(v) for v in reference.values())} "
        f"commodities across {len(reference)} groups to {_COMMODITY_REFERENCE_PATH}"
    )
    return reference


def load_commodity_reference() -> dict[str, list[str]]:
    if not _COMMODITY_REFERENCE_PATH.exists():
        return {}
    return json.loads(_COMMODITY_REFERENCE_PATH.read_text(encoding="utf-8"))


# Explicit per-date "this date has been fully attempted" markers, written
# by _mark_date_completed after a clean full scrape_all() pass and read by
# _resolve_target_date to decide which date to work next. Replaces a
# "does every taxonomy commodity have a row for this date" check that
# could never pass — see _resolve_target_date's docstring — and so left
# the daemon re-scraping the same date forever, newer days piling up
# unscraped behind it.
_COMPLETED_DATES_PATH = _DATA_DIR / "completed_dates.json"


def _load_completed_dates() -> set[str]:
    if not _COMPLETED_DATES_PATH.exists():
        return set()
    try:
        return set(json.loads(_COMPLETED_DATES_PATH.read_text(encoding="utf-8")))
    except (ValueError, OSError):
        logger.opt(exception=True).warning(
            "⚠️ agmarknet_scraper(v2): couldn't read completed_dates.json — "
            "treating no date as complete (re-scraping a done date is wasteful, not wrong)"
        )
        return set()


def _mark_date_completed(target_date: date) -> None:
    """Records target_date as fully attempted so _resolve_target_date stops
    returning it. Called only after scrape_all() has walked the whole
    taxonomy for the date with every group's workers draining cleanly, no
    per-commodity give-up, and no --limit budget cutting the run short."""
    done = _load_completed_dates()
    if target_date.isoformat() in done:
        return
    done.add(target_date.isoformat())
    _DATA_DIR.mkdir(exist_ok=True)
    _COMPLETED_DATES_PATH.write_text(json.dumps(sorted(done), indent=2), encoding="utf-8")
    logger.info(
        f"✅ agmarknet_scraper(v2): marked {target_date.isoformat()} complete in {_COMPLETED_DATES_PATH}"
    )


# One row per (state, district, market, commodity, variety, grade,
# arrival_date) — a same-day re-scrape updates that day's row, any other
# day's arrival_date is a new row, so real per-day history accumulates.
# Prices are stored exactly as reported by the site itself, no
# normalization (see _row_to_result).
#
# market is part of the key (not just state/district) — confirmed live
# (2026-08-31) that a state/district commonly has several markets all
# reporting the same commodity the same day (Bajra: Gujarat/Banaskanth
# alone had 5 separate markets).
#
# variety and grade are ALSO part of the key, for the same reason one
# level deeper — confirmed live (2026-08-31, Wheat: 447 CSV rows, only
# 367 landed before this fix) that a single market commonly reports
# several distinct varieties of the same commodity the same day at
# different prices (e.g. one Gujarat APMC reporting Bansi, Lokwan, AND
# Rajasthan Tukdi wheat, each its own real price). Without variety/grade
# in the key, scraped.setdefault() above and this same table's own ON
# CONFLICT would each independently collapse those down to just one,
# silently discarding every other variety's real, distinct price data —
# not a duplicate, a different product's own report. variety/grade are
# NOT NULL DEFAULT '' rather than nullable specifically so this works:
# Postgres treats two NULLs as never equal in a UNIQUE constraint, so a
# nullable variety/grade would let a same-day re-scrape of a
# blank-variety commodity insert a second row instead of updating the
# first, since NULL never matches NULL.
#
# mandi.market_prices_v2 has this same UNIQUE constraint as the upsert
# key (crop_keyword dropped 2026-08-31, along with restructuring the rest
# of the table to mirror agmarknet's own report columns — see
# create_v2_table.py/reorder_columns.py), matching mandi.market_prices'
# own structure exactly.
_UPSERT_SQL = """
INSERT INTO mandi.market_prices_v2
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

    if "--harvest-commodities" in sys.argv:
        harvest_commodity_reference()
    else:
        target_date = None
        limit = None
        num_workers = 2
        for arg in sys.argv[1:]:
            if arg.startswith("--date="):
                target_date = date.fromisoformat(arg.split("=", 1)[1])
            elif arg.startswith("--limit="):
                limit = int(arg.split("=", 1)[1])
            elif arg.startswith("--workers="):
                # How many concurrent browser sessions work each commodity
                # group at once — see scrape_all()/_scrape_group_parallel's
                # docstrings. 2 is the tested default; each worker is its
                # own browser + OCR reader (~1-1.3GB), so raising this
                # needs matching VM memory headroom.
                num_workers = int(arg.split("=", 1)[1])

        async def _run():
            # scrape_all() is sync (drives real sync_playwright browsers) and
            # Playwright's sync API refuses to run inside a thread that
            # already has an active asyncio loop, hence to_thread here. No
            # final save_scraped(scraped) call needed afterward — every
            # commodity is already flushed to mandi.market_prices_v2 as it's
            # scraped (see _scrape_group_parallel's per-commodity _flush_to_db).
            scraped = await asyncio.to_thread(
                scrape_all, resume="--resume" in sys.argv, target_date=target_date,
                limit=limit, num_workers=num_workers,
            )
            logger.info(f"✅ agmarknet_scraper(v2): run complete — {len(scraped)} entries scraped this run")

        asyncio.run(_run())
