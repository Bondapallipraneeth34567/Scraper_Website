"""Full verification with the variety/grade fix in place: scrapes Wheat
through the real scraper_csv pipeline, saves the CSV persistently, and
cross-checks every single (state, district, market, variety, grade) row
in the CSV against what actually landed in mandi.market_prices_v2 --
Wheat was the commodity that exposed the market-only key's remaining gap
(447 CSV rows, only 367 landed)."""
import csv
import sys

from playwright.sync_api import sync_playwright

sys.path.insert(0, "/home/azureuser/scraper_csv")
from agmarknet_scraper import (  # noqa: E402
    _USER_AGENT,
    _click_go,
    _flush_to_db,
    _open_daily_price_form,
    _pick,
    _row_to_result,
)
from price_shared import _LAST_KNOWN_KEY_SEP  # noqa: E402

GROUP = "Cereals"
COMMODITY = "Wheat"
SAVE_PATH = "/home/azureuser/wheat_verify_final.csv"


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
    csv_rows = [dict(zip(header, r)) for r in rows[2:] if len(r) == len(header)]
    print(f"Total CSV data rows: {len(csv_rows)}")

    scraped = {}
    for row in csv_rows:
        result = _row_to_result(row)
        if result is None:
            continue
        key = _LAST_KNOWN_KEY_SEP.join((
            result["state"], result["district"], result["market"],
            result["commodity"], result["variety"], result["grade"],
        ))
        scraped.setdefault(key, result)

    print(f"Rows after in-memory dedup (should equal CSV row count if no drops): {len(scraped)}")
    ok = _flush_to_db(scraped)
    print(f"DB flush succeeded: {ok}")

    import asyncio
    import asyncpg
    from config import settings

    async def check():
        conn = await asyncpg.connect(
            host=settings.host, port=settings.port, user=settings.user,
            password=settings.password, database=settings.database,
            ssl="require" if settings.ssl else None,
        )
        db_rows = await conn.fetch(
            "SELECT state, district, market, variety, grade FROM mandi.market_prices_v2 "
            "WHERE commodity = $1 AND arrival_date = CURRENT_DATE",
            COMMODITY,
        )
        await conn.close()
        return db_rows

    db_rows = asyncio.run(check())
    db_set = {(r["state"], r["district"], r["market"], r["variety"], r["grade"]) for r in db_rows}
    csv_set = {(r["State/UT"], r["District"], r["Market"], r["Variety"], r["Grade"]) for r in csv_rows}

    print(f"\nDistinct (state,district,market,variety,grade) tuples in CSV: {len(csv_set)}")
    print(f"Distinct (state,district,market,variety,grade) tuples in DB:  {len(db_set)}")
    missing_from_db = csv_set - db_set
    extra_in_db = db_set - csv_set
    print(f"In CSV but MISSING from DB: {len(missing_from_db)}")
    if missing_from_db:
        for m in sorted(missing_from_db)[:20]:
            print(f"  MISSING: {m}")
    print(f"In DB but not in CSV (unexpected): {len(extra_in_db)}")
    if extra_in_db:
        for m in sorted(extra_in_db)[:20]:
            print(f"  EXTRA: {m}")

    if not missing_from_db and not extra_in_db:
        print("\n*** EXACT MATCH — every CSV row landed in the DB, nothing dropped, nothing extra. ***")


if __name__ == "__main__":
    main()
