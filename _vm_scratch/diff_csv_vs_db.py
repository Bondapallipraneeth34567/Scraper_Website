"""Downloads a fresh CSV for one commodity (saved persistently this time,
not to Playwright's auto-cleaned temp dir) and diffs it against what's
actually in mandi.market_prices_v2 for the same date, to show exactly
which rows get dropped and why -- reported live (2026-08-31): Bajra came
back as 27 CSV rows but only 21 landed in the DB."""
import csv
import sys

from playwright.sync_api import sync_playwright

sys.path.insert(0, "/home/azureuser/scraper_csv")
from agmarknet_scraper import (  # noqa: E402
    _USER_AGENT,
    _click_go,
    _open_daily_price_form,
    _pick,
)

GROUP = "Cereals"
COMMODITY = "Bajra(Pearl Millet/Cumbu)"
SAVE_PATH = "/home/azureuser/bajra_today.csv"


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--ignore-certificate-errors"])
        page = browser.new_page(
            viewport={"width": 1400, "height": 1000},
            ignore_https_errors=True,
            user_agent=_USER_AGENT,
        )
        _open_daily_price_form(page, mode="Both")
        _pick(page, "State/UT*", "All States/UTs")
        _pick(page, "Commodity Group*", GROUP)
        _pick(page, "Commodity*", COMMODITY)
        page.keyboard.press("Escape")
        page.wait_for_timeout(400)
        _click_go(page)
        page.wait_for_selector("table tbody tr", timeout=10000)

        page.locator('button[title="Download Report"]').click()
        page.wait_for_timeout(300)
        with page.expect_download(timeout=30000) as dl_info:
            page.get_by_text("Download CSV", exact=False).click()
        download = dl_info.value
        download.save_as(SAVE_PATH)
        print(f"Saved persistent CSV to: {SAVE_PATH}")

        browser.close()

    with open(SAVE_PATH, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.reader(f))
    header = rows[1]
    data_rows = [dict(zip(header, r)) for r in rows[2:] if len(r) == len(header)]
    print(f"\nTotal CSV data rows: {len(data_rows)}")

    from collections import defaultdict
    by_state_district = defaultdict(list)
    for r in data_rows:
        by_state_district[(r["State/UT"], r["District"])].append(r["Market"])

    dupes = {k: v for k, v in by_state_district.items() if len(v) > 1}
    print(f"\n(state, district) pairs with MORE THAN ONE market reporting this commodity: {len(dupes)}")
    dropped = 0
    for (state, district), markets in dupes.items():
        kept = markets[0]
        lost = markets[1:]
        dropped += len(lost)
        print(f"  {state} / {district}: kept {kept!r}, DROPPED {lost!r}")
    print(f"\nTotal rows that would be silently dropped by scraped.setdefault(): {dropped}")
    print(f"Expected DB row count if this is the cause: {len(data_rows) - dropped}")


if __name__ == "__main__":
    main()
