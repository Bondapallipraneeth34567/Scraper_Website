"""Downloads today's site CSV for every commodity (across the 5 scoped
groups) that actually had data, except Wheat (already fetched
separately) -- one file per commodity, saved persistently."""
import sys

from playwright.sync_api import sync_playwright

sys.path.insert(0, "/home/azureuser/scraper_csv")
from agmarknet_scraper import (  # noqa: E402
    _USER_AGENT,
    _click_go,
    _open_daily_price_form,
    _pick,
)

TARGETS = [
    ("Beverages", "Coffee"),
    ("Cereals", "Bajra(Pearl Millet/Cumbu)"),
    ("Cereals", "Barley(Jau)"),
    ("Cereals", "Jowar(Sorghum)"),
    ("Cereals", "Maize"),
    ("Cereals", "Paddy(Common)"),
    ("Cereals", "Rice"),
    ("Cereals", "Sweet Corn"),
    ("Dry Fruits", "Cashewnuts"),
    ("Dry Fruits", "Makhana(Foxnut)"),
    ("Fibre Crops", "Cotton"),
    ("Oils and Fats", "Coconut Oil"),
    ("Oils and Fats", "Ghee"),
]

OUT_DIR = "/home/azureuser/csv_batch"


def safe_name(s: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in s).strip("_")


def main():
    import os
    os.makedirs(OUT_DIR, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--ignore-certificate-errors"])
        page = browser.new_page(
            viewport={"width": 1400, "height": 1000},
            ignore_https_errors=True,
            user_agent=_USER_AGENT,
        )
        _open_daily_price_form(page, mode="Both")
        _pick(page, "State/UT*", "All States/UTs")

        for group, commodity in TARGETS:
            print(f"=== {commodity} ===")
            try:
                _pick(page, "Commodity Group*", group)
                _pick(page, "Commodity*", commodity)
                page.keyboard.press("Escape")
                page.wait_for_timeout(400)
                _click_go(page)
                page.wait_for_timeout(500)

                if page.locator("table").count() == 0:
                    print("  0 rows, nothing to download")
                    continue

                page.locator('button[title="Download Report"]').click()
                page.wait_for_timeout(300)
                with page.expect_download(timeout=30000) as dl_info:
                    page.get_by_text("Download CSV", exact=False).click()
                download = dl_info.value
                save_path = f"{OUT_DIR}/{safe_name(commodity)}.csv"
                download.save_as(save_path)
                print(f"  saved {save_path}")
            except Exception as exc:
                print(f"  FAILED: {exc!r}")
                try:
                    _open_daily_price_form(page, mode="Both")
                    _pick(page, "State/UT*", "All States/UTs")
                except Exception:
                    print("  form recovery also failed, aborting")
                    raise

        browser.close()


if __name__ == "__main__":
    main()
