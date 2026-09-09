import asyncio
import asyncpg
from config import settings


async def main():
    conn = await asyncpg.connect(
        host=settings.host, port=settings.port, user=settings.user,
        password=settings.password, database=settings.database,
        ssl="require" if settings.ssl else None,
    )
    for table in ("market_prices", "market_prices_v2"):
        print(f"--- {table} ---")
        cols = await conn.fetch(
            "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
            "WHERE table_schema='mandi' AND table_name=$1 ORDER BY ordinal_position",
            table,
        )
        for c in cols:
            print(f"  {c['column_name']:<20} {c['data_type']:<12} nullable={c['is_nullable']}")
        cons = await conn.fetch(
            "SELECT conname, pg_get_constraintdef(oid) AS def FROM pg_constraint "
            "WHERE conrelid = ('mandi.' || $1)::regclass",
            table,
        )
        for c in cons:
            print(f"  CONSTRAINT {c['conname']}: {c['def']}")
    await conn.close()


asyncio.run(main())
