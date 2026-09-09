"""Standalone Postgres (asyncpg) helper for the CSV-download scraper
(scraper_csv/) -- a parallel, independent implementation of
Web_Scraper/agmarknet_scraper.py being verified against the existing
DOM-pagination scraper before any cutover decision is made. Writes into
mandi.market_prices_v2 (a structural copy of mandi.market_prices, same
columns/constraints/indexes, created 2026-08-31), never into
mandi.market_prices itself -- the two scrapers share nothing, on purpose,
so nothing this one does can affect the existing flow's data."""
import asyncpg

from config import settings


async def _connect():
    return await asyncpg.connect(
        host=settings.host,
        port=settings.port,
        user=settings.user,
        password=settings.password,
        database=settings.database,
        ssl="require" if settings.ssl else None,
        timeout=15,
    )


async def execute_many(sql: str, params_list: list[tuple]) -> None:
    """Run the same upsert statement once per row in params_list over a
    single connection via asyncpg's own executemany."""
    if not params_list:
        return

    conn = await _connect()
    try:
        await conn.executemany(sql, params_list)
    finally:
        await conn.close()


async def fetch_all(sql: str, *params) -> list[asyncpg.Record]:
    """Runs a single SELECT and returns every matching row, over a fresh
    connection -- used for the --resume gap-check against
    mandi.market_prices_v2."""
    conn = await _connect()
    try:
        return await conn.fetch(sql, *params)
    finally:
        await conn.close()
