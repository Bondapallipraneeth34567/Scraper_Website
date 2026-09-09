"""Cosmetic fix: renames the leftover *_reordered_pkey constraint names
(a side effect of reorder_columns.py creating each table as
{table}_reordered before swapping it into place) back to the clean
{table}_pkey name. Purely a rename -- no data or behavior change."""
import asyncio
import asyncpg
from config import settings

RENAMES = [
    ("market_prices", "market_prices_reordered_pkey", "market_prices_pkey"),
    ("market_prices_v2", "market_prices_v2_reordered_pkey", "market_prices_v2_pkey"),
]


async def main():
    conn = await asyncpg.connect(
        host=settings.host, port=settings.port, user=settings.user,
        password=settings.password, database=settings.database,
        ssl="require" if settings.ssl else None,
    )
    for table, old_name, new_name in RENAMES:
        await conn.execute(f"ALTER TABLE mandi.{table} RENAME CONSTRAINT {old_name} TO {new_name}")
        print(f"mandi.{table}: {old_name} -> {new_name}")
    await conn.close()


asyncio.run(main())
