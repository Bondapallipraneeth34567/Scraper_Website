"""Downloads today's Wheat CSV via the real scraper_csv pipeline and saves
it persistently so it can be handed to the user directly, instead of
letting it sit in Playwright's auto-cleaned temp download dir."""
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
SAVE_PATH = "/home/azureuser/wheat_today_for_user.csv"


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
        print(f"Saved to: {SAVE_PATH}")

        browser.close()


if __name__ == "__main__":
    main()
