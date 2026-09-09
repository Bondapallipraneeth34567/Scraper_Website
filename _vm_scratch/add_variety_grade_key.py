"""Adds variety/grade to the upsert key on mandi.market_prices and
mandi.market_prices_v2 -- confirmed live (2026-08-31, Wheat: 447 CSV rows,
367 landed) that a single market commonly reports several distinct
varieties of the same commodity the same day at different prices, and the
market-only key (added earlier today) still silently collapsed those to
one, same class of bug as the original market-less key.

Converts variety/grade from nullable to NOT NULL DEFAULT '' first --
Postgres treats two NULLs as never equal in a UNIQUE constraint, so
leaving them nullable would mean a same-day re-scrape of a blank-variety
commodity inserts a second row instead of updating the first.

No table rebuild needed this time (unlike the column-reorder migration) --
just ALTER COLUMN + constraint swap, so this is a lower-risk operation
that doesn't require the DROP TABLE + rename dance. Each table's changes
run in one transaction so a concurrent writer never sees a half-migrated
state. Does not touch mandi.market_prices_backup_20260831."""
import asyncio
import asyncpg
from config import settings

TABLES = ["market_prices", "market_prices_v2"]


async def migrate(conn: asyncpg.Connection, table: str) -> None:
    print(f"\n=== {table} ===")
    before = await conn.fetchval(f"SELECT COUNT(*) FROM mandi.{table}")
    null_variety = await conn.fetchval(f"SELECT COUNT(*) FROM mandi.{table} WHERE variety IS NULL")
    null_grade = await conn.fetchval(f"SELECT COUNT(*) FROM mandi.{table} WHERE grade IS NULL")
    print(f"rows: {before}, NULL variety: {null_variety}, NULL grade: {null_grade}")

    async with conn.transaction():
        await conn.execute(f"UPDATE mandi.{table} SET variety = '' WHERE variety IS NULL")
        await conn.execute(f"UPDATE mandi.{table} SET grade = '' WHERE grade IS NULL")
        await conn.execute(f"ALTER TABLE mandi.{table} ALTER COLUMN variety SET DEFAULT ''")
        await conn.execute(f"ALTER TABLE mandi.{table} ALTER COLUMN variety SET NOT NULL")
        await conn.execute(f"ALTER TABLE mandi.{table} ALTER COLUMN grade SET DEFAULT ''")
        await conn.execute(f"ALTER TABLE mandi.{table} ALTER COLUMN grade SET NOT NULL")

        await conn.execute(f"ALTER TABLE mandi.{table} DROP CONSTRAINT uq_{table}_upsert_key")
        await conn.execute(
            f"ALTER TABLE mandi.{table} ADD CONSTRAINT uq_{table}_upsert_key "
            "UNIQUE (state, district, market, commodity, variety, grade, arrival_date)"
        )
        print(f"mandi.{table}: variety/grade now NOT NULL DEFAULT '', unique key updated")

    after = await conn.fetchval(f"SELECT COUNT(*) FROM mandi.{table}")
    print(f"rows after: {after} (should equal {before})")
    if after != before:
        raise RuntimeError(f"Row count changed for {table}: {before} -> {after}")


async def main() -> None:
    conn = await asyncpg.connect(
        host=settings.host, port=settings.port, user=settings.user,
        password=settings.password, database=settings.database,
        ssl="require" if settings.ssl else None,
    )
    try:
        for table in TABLES:
            await migrate(conn, table)
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
