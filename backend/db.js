// TZ pinned before anything touches a Date — node-postgres's default
// timestamp-without-timezone parser builds JS Date objects from local
// wall-clock components, so a non-UTC host would otherwise silently shift
// every scraped_at/verified_at by its own offset. mandi.market_prices_v2's
// scraped_at is written as UTC (see the scraper's `(now() AT TIME ZONE
// 'utc')`), so the reader has to agree it's UTC too. Must run before the
// `pg` import below — Node re-reads process.env.TZ lazily per Date
// computation, not just once at startup, but setting it this early costs
// nothing and rules out any import-order surprises.
process.env.TZ = 'UTC';

import 'dotenv/config';
import pg from 'pg';

const { Pool } = pg;

// mandi schema on the shared Postgres server — see backend/server.js's
// top-of-file comment for how dbo.MarketPrices/VerificationResults/
// DashboardUsers ended up here (migrated off Azure SQL, 2026-08-28).
const pool = new Pool({
  host: process.env.POSTGRES_HOST,
  port: Number(process.env.POSTGRES_PORT),
  database: process.env.POSTGRES_DB,
  user: process.env.POSTGRES_USER,
  password: process.env.POSTGRES_PASSWORD,
  ssl: process.env.POSTGRES_SSL === 'true' ? { rejectUnauthorized: false } : false,
  max: 10,
  idleTimeoutMillis: 30000,
  connectionTimeoutMillis: 15000,
});

/** Shared connection pool — one per process, reused across requests. Kept
 * async-shaped (existing call sites all do `await getPool()`) even though
 * pg's Pool needs no connect step of its own, purely so this stayed a
 * drop-in swap for the old mssql-backed getPool(). */
export async function getPool() {
  return pool;
}
