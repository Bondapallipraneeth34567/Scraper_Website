"""Timing comparison: old DOM row-by-row + 'Next page' pagination vs new
CSV download+parse, on the SAME rendered result set (one Go click, one
captcha solve, shared by both reads) so the comparison isolates just the
row-extraction step -- not captcha luck, which is identical overhead for
both and irrelevant to which extraction method is faster.

Loads Web_Scraper's and scraper_csv's agmarknet_scraper.py under distinct
module names (importlib) since both files are literally named
agmarknet_scraper.py and a plain sys.path import would only ever load one
of them.

Tested commodities span the range: Chicory (small, ~2 rows/1 page),
Bajra (medium, ~50 rows/~5 pages), Wheat (large, ~500 rows/~50 pages) --
the pagination cost the old approach pays scales with page count, so a
small commodity should show little difference and Wheat should show it
starkly.
"""
import importlib.util
import sys
import time
from datetime import date

from playwright.sync_api import sync_playwright

# Run early on 2026-09-01 (UTC morning = still early IST) against TODAY's
# date and got misleadingly small numbers (Wheat: 8 rows vs the 516 seen
# yesterday afternoon) purely because most markets hadn't reported yet --
# both old and new methods agreed exactly on that small count, so it
# wasn't a bug, just too little real volume to show pagination cost.
# Targeting yesterday instead, where real high-volume data is already
# confirmed to exist, via the same date-override mechanism scrape_all()
# uses for backfills.
TARGET_DATE = date(2026, 8, 31)


def _load_module(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _purge_shared_deps():
    # Both agmarknet_scraper.py files do `from config import settings`,
    # `from database import ...`, `from price_shared import ...` -- plain
    # names, not renamed like the top-level module. Without this, loading
    # Web_Scraper's copy first caches sys.modules['database'] etc. under
    # its own file, and scraper_csv's later `from database import
    # execute_many, fetch_all` would silently reuse that SAME cached
    # module instead of its own (which actually defines fetch_all) --
    # Web_Scraper's database.py has no fetch_all at all, so that would
    # fail outright rather than mixing up behavior quietly.
    for mod in ("config", "database", "price_shared"):
        sys.modules.pop(mod, None)


_purge_shared_deps()
sys.path.insert(0, "/home/azureuser/Web_Scraper")
old = _load_module("old_scraper", "/home/azureuser/Web_Scraper/agmarknet_scraper.py")
sys.path.pop(0)

_purge_shared_deps()
sys.path.insert(0, "/home/azureuser/scraper_csv")
new = _load_module("new_scraper", "/home/azureuser/scraper_csv/agmarknet_scraper.py")
sys.path.pop(0)


TEST_COMMODITIES = [
    ("Beverages", "Chicory(Chikori/Kasni)"),
    ("Cereals", "Bajra(Pearl Millet/Cumbu)"),
    ("Cereals", "Jowar(Sorghum)"),
    ("Cereals", "Maize"),
    ("Cereals", "Wheat"),
]


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--ignore-certificate-errors"])
        page = browser.new_page(
            viewport={"width": 1400, "height": 1000},
            ignore_https_errors=True,
            user_agent=new._USER_AGENT,
        )
        new._install_date_override(page, TARGET_DATE)
        new._open_daily_price_form(page, mode="Both")
        new._pick(page, "State/UT*", "All States/UTs")

        results = []
        for group, commodity in TEST_COMMODITIES:
            print(f"\n=== {commodity} ===")
            new._pick(page, "Commodity Group*", group)
            new._pick(page, "Commodity*", commodity)
            page.keyboard.press("Escape")
            page.wait_for_timeout(400)

            # Each method gets its own independent Go click/captcha solve
            # and fresh page-1 load -- no shared page state between them.
            # Sharing one load (tried first) let one method's page
            # interactions (the CSV download's dropdown menu, or the DOM
            # walk's "Next page" clicks) leave the page in a state that
            # corrupted the other method's read on the very next call --
            # a test-methodology artifact, not a real bug in either
            # method (production only ever calls one of them per
            # commodity, never both back-to-back).
            t0 = time.monotonic()
            new._click_go(page)
            captcha1 = time.monotonic() - t0
            page.wait_for_timeout(500)
            if page.locator("table").count() == 0:
                print("0 rows -- skipping (nothing to compare extraction speed on)")
                continue
            t0 = time.monotonic()
            old_rows = old._scrape_visible_results_dynamic(page)
            old_time = time.monotonic() - t0
            print(f"OLD: captcha {captcha1:.1f}s + extraction {old_time:.1f}s = {len(old_rows)} rows")

            t0 = time.monotonic()
            new._click_go(page)
            captcha2 = time.monotonic() - t0
            page.wait_for_timeout(500)
            t0 = time.monotonic()
            new_rows = new._download_and_parse_csv(page)
            new_time = time.monotonic() - t0
            print(f"NEW: captcha {captcha2:.1f}s + extraction {new_time:.1f}s = {len(new_rows)} rows")

            speedup = old_time / new_time if new_time > 0 else float("inf")
            print(f"Extraction-only speedup: {speedup:.1f}x faster (captcha cost excluded, identical either way)")
            results.append((commodity, len(old_rows), old_time, len(new_rows), new_time, speedup))

            time.sleep(1.5)

        browser.close()

    print("\n\n=== SUMMARY ===")
    print(f"{'Commodity':<30} {'Old rows':>9} {'Old time':>9} {'New rows':>9} {'New time':>9} {'Speedup':>8}")
    for commodity, or_, ot, nr, nt, sp in results:
        print(f"{commodity:<30} {or_:>9} {ot:>8.1f}s {nr:>9} {nt:>8.1f}s {sp:>7.1f}x")


if __name__ == "__main__":
    main()
