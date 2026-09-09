"""Check why Wheat came back as 447 CSV rows but only 367 landed in the DB
-- confirm whether it's the same (state,district,market) collapsing
multiple varieties/grades, the way Coffee/Bajra did before market was
added to the key."""
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
COMMODITY = "Wheat"
SAVE_PATH = "/home/azureuser/wheat_verify.csv"


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
    data = [dict(zip(header, r)) for r in rows[2:] if len(r) == len(header)]
    print(f"Total CSV rows: {len(data)}")

    from collections import defaultdict
    by_market = defaultdict(list)
    for r in data:
        key = (r["State/UT"], r["District"], r["Market"])
        by_market[key].append((r["Variety"], r["Grade"], r["Min Price"], r["Max Price"], r["Modal Price"]))

    dupes = {k: v for k, v in by_market.items() if len(v) > 1}
    print(f"Distinct (state,district,market) triples: {len(by_market)}")
    print(f"Triples with MORE THAN ONE row (multi-variety/grade): {len(dupes)}")
    total_extra = sum(len(v) - 1 for v in dupes.values())
    print(f"Total 'extra' rows beyond one-per-market: {total_extra}")
    print(f"Expected DB rows if market-only key: {len(by_market)}")
    print()
    shown = 0
    for k, v in dupes.items():
        if shown >= 15:
            print(f"... and {len(dupes) - shown} more")
            break
        print(k, "->", v)
        shown += 1


if __name__ == "__main__":
    main()
