"""Exports today's Wheat rows actually stored in mandi.market_prices_v2 to
a CSV, so it can be compared side-by-side against the site's own
downloaded CSV for verification."""
import asyncio
import csv

import asyncpg
from config import settings

SAVE_PATH = "/home/azureuser/wheat_today_db_export.csv"


async def main():
    conn = await asyncpg.connect(
        host=settings.host, port=settings.port, user=settings.user,
        password=settings.password, database=settings.database,
        ssl="require" if settings.ssl else None,
    )
    rows = await conn.fetch(
        "SELECT state, district, market, commodity_group, commodity, variety, grade, "
        "min_price, max_price, modal_price, price_unit, price_date, "
        "arrival_quantity, arrival_unit, arrival_date "
        "FROM mandi.market_prices_v2 "
        "WHERE commodity = 'Wheat' AND arrival_date = CURRENT_DATE "
        "ORDER BY state, district, market"
    )
    await conn.close()

    with open(SAVE_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "State/UT", "District", "Market", "Commodity Group", "Commodity", "Variety", "Grade",
            "Min Price", "Max Price", "Modal Price", "Price Unit", "Price Date",
            "Arrival Quantity", "Arrival Unit", "Arrival Date",
        ])
        for r in rows:
            writer.writerow([r[k] for k in r.keys()])

    print(f"Exported {len(rows)} rows to {SAVE_PATH}")


asyncio.run(main())
