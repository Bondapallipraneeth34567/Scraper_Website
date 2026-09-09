"""One-off: restructures mandi.market_prices and mandi.market_prices_v2 to
match agmarknet.gov.in's own report columns (State/UT, District, Market,
Commodity Group, Commodity, Variety, Grade, Min/Max/Modal Price, Price
Unit, Price Date, Arrival Quantity, Arrival Unit, Arrival Date), while
keeping market_price_id (PK) and crop_keyword (the upsert dedup key) as
internal columns beyond that list -- neither scraper works without them.

Takes a full backup of mandi.market_prices into
mandi.market_prices_backup_20260831 (a plain CREATE TABLE ... AS SELECT,
not touched by anything else) before altering anything live, since that
table has 103,908 real rows. market_prices_v2 only has 23 test rows from
smoke-testing scraper_csv, but gets the identical migration for
consistency and because there's no reason not to back it up too.

Renames (data preserved, not recreated):
  min_price_quintal   -> min_price
  max_price_quintal   -> max_price
  modal_price_quintal -> modal_price
  arrival_qty         -> arrival_quantity

Adds (NULL for every pre-migration row -- this data was never captured
before): commodity_group, variety, grade, price_date.

Adds price_unit and backfills every existing row to 'Rs./Quintal' -- that
matches what min/max/modal_price actually already hold for historical
rows (the old scraper normalized every price to a quintal-equivalent
regardless of the site's original reported unit), so old rows stay
internally consistent even though the original per-row unit before
normalization is not recoverable. New rows going forward store the RAW
value + the real reported unit, no normalization.
"""
import asyncio

import asyncpg
from config import settings

MIGRATION_SQL = """
-- Renames (data preserved)
ALTER TABLE mandi.{table} RENAME COLUMN min_price_quintal TO min_price;
ALTER TABLE mandi.{table} RENAME COLUMN max_price_quintal TO max_price;
ALTER TABLE mandi.{table} RENAME COLUMN modal_price_quintal TO modal_price;
ALTER TABLE mandi.{table} RENAME COLUMN arrival_qty TO arrival_quantity;

-- New site-matching columns
ALTER TABLE mandi.{table} ADD COLUMN IF NOT EXISTS commodity_group TEXT;
ALTER TABLE mandi.{table} ADD COLUMN IF NOT EXISTS variety TEXT;
ALTER TABLE mandi.{table} ADD COLUMN IF NOT EXISTS grade TEXT;
ALTER TABLE mandi.{table} ADD COLUMN IF NOT EXISTS price_unit TEXT;
ALTER TABLE mandi.{table} ADD COLUMN IF NOT EXISTS price_date DATE;

-- Backfill: every existing row's min/max/modal_price is already a
-- quintal-equivalent number (the old scraper's normalization), so label
-- it as such for internal consistency.
UPDATE mandi.{table} SET price_unit = 'Rs./Quintal' WHERE price_unit IS NULL;
"""


async def migrate(conn: asyncpg.Connection, table: str) -> None:
    print(f"--- migrating mandi.{table} ---")
    for stmt in MIGRATION_SQL.format(table=table).strip().split(";"):
        stmt = stmt.strip()
        if not stmt:
            continue
        await conn.execute(stmt)
    cols = await conn.fetch(
        "SELECT column_name, data_type FROM information_schema.columns "
        "WHERE table_schema='mandi' AND table_name=$1 ORDER BY ordinal_position",
        table,
    )
    print(f"mandi.{table} now has {len(cols)} columns:")
    for c in cols:
        print(f"  {c['column_name']:<20} {c['data_type']}")


async def main() -> None:
    conn = await asyncpg.connect(
        host=settings.host, port=settings.port, user=settings.user,
        password=settings.password, database=settings.database,
        ssl="require" if settings.ssl else None,
    )
    try:
        print("=== backing up mandi.market_prices (103,908 rows) ===")
        await conn.execute(
            "DROP TABLE IF EXISTS mandi.market_prices_backup_20260831;"
            "CREATE TABLE mandi.market_prices_backup_20260831 AS "
            "SELECT * FROM mandi.market_prices;"
        )
        backup_count = await conn.fetchval("SELECT COUNT(*) FROM mandi.market_prices_backup_20260831")
        print(f"Backup created: mandi.market_prices_backup_20260831 ({backup_count} rows)")

        await migrate(conn, "market_prices")
        await migrate(conn, "market_prices_v2")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
