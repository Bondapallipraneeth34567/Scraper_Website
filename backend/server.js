import express from 'express';
import cors from 'cors';
import cookieParser from 'cookie-parser';
import { readFileSync } from 'fs';
import { dirname, join } from 'path';
import { fileURLToPath } from 'url';
import { getPool } from './db.js';
import { comparePassword, signToken, COOKIE_NAME, setCookieOptions, clearCookieOptions, requireAuth } from './auth.js';

const app = express();

// Full Agmarknet commodity-group taxonomy (group -> [commodity, ...]).
// Originally harvested straight from the live site by the (separate,
// gitignored — see .gitignore) scraper project's
// bot_processors/pricing/agmarknet_scraper.py::harvest_commodity_reference()
// (see frontend/src/data/siteTotals.js — 16 groups / 604 commodities,
// confirmed against agmarknet.gov.in on 2026-08-20) and copied into this
// repo's own backend/data/ so the dashboard has no runtime dependency on
// the scraper project's directory existing alongside it — this repo is
// scoped to frontend/+backend/ only. market_prices_v2 does have its own
// commodity_group column (unlike the retired market_prices), but the
// group-filtering endpoints below still resolve group -> commodity names
// through this taxonomy file rather than that column — group membership
// here should stay the single source of truth either way; re-copy this
// file here if the scraper re-harvests a changed taxonomy.
const __dirname = dirname(fileURLToPath(import.meta.url));
const COMMODITY_GROUPS = JSON.parse(
  readFileSync(join(__dirname, 'data', 'agmarknet_commodities.json'), 'utf8')
);

const allowedOrigins = (process.env.CORS_ORIGINS || 'http://localhost:5173')
  .split(',')
  .map((s) => s.trim())
  .filter(Boolean);
app.use(cors({
  origin(origin, callback) {
    if (!origin || allowedOrigins.includes(origin)) return callback(null, true);
    callback(new Error('Not allowed by CORS'));
  },
  credentials: true,
}));
app.use(cookieParser());
app.use(express.json());

function normalizeEmail(email) {
  return String(email || '').trim().toLowerCase();
}

// Public signup is intentionally disabled — exactly one account exists,
// seeded via seed_admin_user.js. Login is the only way in.

// dbo.MarketPrices / dbo.VerificationResults / dbo.DashboardUsers all lived
// on Azure SQL until 2026-08-28, when the scraper (Web_Scraper on the VM)
// switched to writing mandi.market_prices on the shared Postgres server
// instead (sethu-admin.postgres.database.azure.com) — this file's read
// side moved with it, VerificationResults/DashboardUsers included, since
// VerificationResults FK's onto market_prices and can't join across two
// different database engines in one query.
//
// History: market_prices (DOM+pagination scraper) -> market_prices_v2
// (CSV-export browser scraper, 2026-09-02) -> market_prices_v3 (2026-09-08,
// current). v3 is written by the mandi_pipeline "AGMARKNET direct API"
// scraper (Approach 1): no headless browser, calls the site's own
// /daily-price-arrival/report JSON endpoint per commodity, one CAPTCHA
// each. It is the only one of the three still updating daily — market_prices
// stopped 2026-09-02, market_prices_v2's scraper got stuck re-scraping
// 2026-09-01 and was retired 2026-09-08. Same column set as v2 (plus
// source / source_row_hash / first_seen_at, which this API ignores).
// verification_results' FK moved to v3 the same day (the table was empty).

app.post('/api/auth/login', async (req, res) => {
  try {
    const email = normalizeEmail(req.body.email);
    const password = String(req.body.password || '');

    const pool = await getPool();
    const result = await pool.query(
      'SELECT user_id, name, email, password_hash, role FROM mandi.dashboard_users WHERE email = $1',
      [email]
    );

    const row = result.rows[0];
    const ok = row ? await comparePassword(password, row.password_hash) : false;
    if (!ok) {
      return res.status(401).json({ error: 'Invalid email or password' });
    }

    const user = { id: Number(row.user_id), name: row.name, email: row.email, role: row.role };
    res.cookie(COOKIE_NAME, signToken(user), setCookieOptions());
    res.json(user);
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.post('/api/auth/logout', (req, res) => {
  res.clearCookie(COOKIE_NAME, clearCookieOptions());
  res.json({ ok: true });
});

app.get('/api/auth/me', requireAuth, (req, res) => {
  res.json(req.user);
});

app.use('/api', requireAuth);

// 605 real commodities in the full Agmarknet taxonomy (see
// backend/data/agmarknet_commodities.json) — was 585 here, left over from
// when market_prices' own scraper deduped commodities by a normalized
// crop_keyword and silently collapsed 19 distinct ones sharing a name
// prefix (e.g. Mango vs Mango(Raw-Ripe)) into one, undercounting the real
// target. market_prices_v2's scraper doesn't dedupe that way.
const TARGET_COMMODITIES_PER_DAY = 605;

// Row -> frontend record shape (mirrors what mockData.js used to produce,
// so the frontend's existing components don't need to change).
function toRecord(row) {
  return {
    // pg returns BIGINT/NUMERIC columns as strings (can't safely fit every
    // possible 64-bit value in a JS number, and floats would lose decimal
    // precision) -- these particular values are nowhere near unsafe range
    // in practice, and the frontend sorts/compares numerically, so
    // coercing here avoids "100" sorting before "27" as strings would.
    id: Number(row.market_price_id),
    date: row.arrival_date instanceof Date ? row.arrival_date.toISOString().slice(0, 10) : row.arrival_date,
    state: row.state,
    district: row.district,
    market: row.market,
    commodity: row.commodity,
    // Column names here are min_price/max_price/modal_price/arrival_quantity
    // (not the old *_quintal/arrival_qty names) as of the 2026-08-31
    // schema restructure — prices are stored exactly as the site reports
    // them, no quintal normalization. This mapping was left reading the
    // old names, which don't exist on either table anymore, so every
    // record's prices/arrival qty were silently coming back NaN.
    minPrice: row.min_price === null ? null : Number(row.min_price),
    maxPrice: row.max_price === null ? null : Number(row.max_price),
    modalPrice: row.modal_price === null ? null : Number(row.modal_price),
    arrivalQty: row.arrival_quantity === null ? null : Number(row.arrival_quantity),
    arrivalUnit: row.arrival_unit,
    scrapedAt: row.scraped_at instanceof Date ? row.scraped_at.toISOString().slice(11, 16) : row.scraped_at,
    status: row.status || 'pending',
    notes: row.notes || undefined,
    verifiedBy: row.verified_by || undefined,
    verifiedAt: row.verified_at ? new Date(row.verified_at).toISOString() : undefined,
  };
}

// Reused everywhere a record needs its latest verification verdict joined
// in — one VerificationResults row per market_price_id (the most recent).
const LATEST_VERIFICATION_JOIN = `
  LEFT JOIN (
    SELECT market_price_id, status, notes, verified_by, verified_at, issue_type,
           ROW_NUMBER() OVER (PARTITION BY market_price_id ORDER BY verified_at DESC) AS rn
    FROM mandi.verification_results
  ) vr ON vr.market_price_id = mp.market_price_id AND vr.rn = 1
`;

app.get('/api/overview', async (req, res) => {
  try {
    const pool = await getPool();
    const totals = await pool.query(`
      SELECT COUNT(*) AS total_rows, COUNT(DISTINCT state) AS states,
             COUNT(DISTINCT district) AS districts, COUNT(DISTINCT market) AS markets,
             COUNT(DISTINCT commodity) AS commodities,
             MIN(arrival_date) AS min_date, MAX(arrival_date) AS max_date
      FROM mandi.market_prices_v3
    `);
    const dupes = await pool.query(`
      SELECT COUNT(*) AS duplicate_groups FROM (
        SELECT state, district, market, commodity, arrival_date
        FROM mandi.market_prices_v3
        GROUP BY state, district, market, commodity, arrival_date
        HAVING COUNT(*) > 1
      ) g
    `);
    const missing = await pool.query(`
      SELECT COUNT(*) AS missing_records
      FROM (
        SELECT vr.status,
               ROW_NUMBER() OVER (PARTITION BY vr.market_price_id ORDER BY vr.verified_at DESC) AS rn
        FROM mandi.verification_results vr
      ) latest
      WHERE latest.rn = 1 AND latest.status = 'missing'
    `);
    const t = totals.rows[0];
    res.json({
      total_rows: Number(t.total_rows),
      states: Number(t.states),
      districts: Number(t.districts),
      markets: Number(t.markets),
      commodities: Number(t.commodities),
      min_date: t.min_date?.toISOString().slice(0, 10),
      max_date: t.max_date?.toISOString().slice(0, 10),
      duplicate_groups: Number(dupes.rows[0].duplicate_groups),
      missing_records: Number(missing.rows[0].missing_records),
    });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.get('/api/completeness', async (req, res) => {
  try {
    const pool = await getPool();
    // crop_keyword was dropped from the schema 2026-08-31 (see
    // agmarknet_scraper.py's own schema comments) in favor of the raw
    // commodity name — this query was left referencing the dropped
    // column, which errored on every call.
    const result = await pool.query(`
      SELECT CAST(arrival_date AS date) AS d, COUNT(DISTINCT commodity) AS commodities, COUNT(*) AS rows_count
      FROM mandi.market_prices_v3 GROUP BY CAST(arrival_date AS date) ORDER BY d
    `);
    res.json({
      dates: result.rows.map((r) => ({
        date: r.d.toISOString().slice(0, 10),
        commodities: Number(r.commodities),
        rows: Number(r.rows_count),
      })),
      target: TARGET_COMMODITIES_PER_DAY,
    });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.get('/api/filters/states', async (req, res) => {
  try {
    const pool = await getPool();
    const result = await pool.query('SELECT DISTINCT state FROM mandi.market_prices_v3 ORDER BY state');
    res.json(result.rows.map((r) => r.state));
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.get('/api/filters/districts', async (req, res) => {
  try {
    const pool = await getPool();
    const params = [];
    let query = 'SELECT DISTINCT district FROM mandi.market_prices_v3 WHERE 1=1';
    if (req.query.state && req.query.state !== 'All') {
      params.push(req.query.state);
      query += ` AND state = $${params.length}`;
    }
    query += ' ORDER BY district';
    const result = await pool.query(query, params);
    res.json(result.rows.map((r) => r.district));
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.get('/api/filters/markets', async (req, res) => {
  try {
    const pool = await getPool();
    const params = [];
    let query = 'SELECT DISTINCT market FROM mandi.market_prices_v3 WHERE 1=1';
    if (req.query.state && req.query.state !== 'All') {
      params.push(req.query.state);
      query += ` AND state = $${params.length}`;
    }
    if (req.query.district && req.query.district !== 'All') {
      params.push(req.query.district);
      query += ` AND district = $${params.length}`;
    }
    query += ' ORDER BY market';
    const result = await pool.query(query, params);
    res.json(result.rows.map((r) => r.market));
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// Commodity Group is a reference-file dimension (no DB column — see
// COMMODITY_GROUPS above), so this list doesn't depend on the DB at all.
app.get('/api/filters/commodity-groups', (req, res) => {
  res.json(Object.keys(COMMODITY_GROUPS));
});

// Reference-file dimension too, same as commodity-groups above — the
// dropdown lists the full agmarknet taxonomy (not just what's scraped so
// far) so a commodity with zero rows is still selectable, rather than
// silently disappearing depending on today's scrape coverage.
app.get('/api/filters/commodities', (req, res) => {
  const group = req.query.group;
  const commodities = group && group !== 'All'
    ? (COMMODITY_GROUPS[group] || [])
    : Object.values(COMMODITY_GROUPS).flat();
  res.json([...commodities].sort((a, b) => a.localeCompare(b)));
});

// Whitelisted so ORDER BY can never take raw user input.
const SORT_COLUMNS = {
  id: 'mp.market_price_id',
  date: 'mp.arrival_date',
  state: 'mp.state',
  district: 'mp.district',
  market: 'mp.market',
  commodity: 'mp.commodity',
  minPrice: 'mp.min_price',
  maxPrice: 'mp.max_price',
  modalPrice: 'mp.modal_price',
  arrivalQty: 'mp.arrival_quantity',
  arrivalUnit: 'mp.arrival_unit',
};

app.get('/api/records', async (req, res) => {
  try {
    const pool = await getPool();
    const { date, state, district, market, commodity, commodityGroup, status, search, sortBy, sortDir } = req.query;
    const page = Math.max(1, Number(req.query.page) || 1);
    const pageSize = Math.min(200, Number(req.query.pageSize) || 50);

    const params = [];
    function addParam(value) {
      params.push(value);
      return `$${params.length}`;
    }

    let where = 'WHERE 1=1';
    if (date) where += ` AND mp.arrival_date = ${addParam(date)}`;
    if (state && state !== 'All') where += ` AND mp.state = ${addParam(state)}`;
    if (district && district !== 'All') where += ` AND mp.district = ${addParam(district)}`;
    if (market && market !== 'All') where += ` AND mp.market = ${addParam(market)}`;
    if (commodity && commodity !== 'All') where += ` AND mp.commodity = ${addParam(commodity)}`;
    if (commodityGroup && commodityGroup !== 'All') {
      // market_prices_v2 does have a commodity_group column, but filtering
      // through the reference taxonomy (as before) keeps group membership
      // consistent with every other group-aware endpoint here regardless
      // of what any one row's own commodity_group happens to say.
      const names = COMMODITY_GROUPS[commodityGroup] || [];
      if (names.length === 0) {
        where += ' AND 1=0';
      } else {
        // LOWER() on both sides, not a bare IN — every other group-aware
        // endpoint here (commodities?group=, commodity-coverage) matches
        // case-insensitively since the taxonomy file's casing isn't
        // guaranteed to match what's actually stored in the DB.
        const placeholders = names.map((name) => addParam(name.toLowerCase()));
        where += ` AND LOWER(mp.commodity) IN (${placeholders.join(',')})`;
      }
    }
    if (search) {
      // ILIKE, not LIKE — Postgres LIKE is case-sensitive by default,
      // unlike the case-insensitive collation Azure SQL used here before.
      const searchParam = addParam(`%${search}%`);
      where += ` AND (mp.market ILIKE ${searchParam} OR mp.commodity ILIKE ${searchParam} OR mp.district ILIKE ${searchParam} OR mp.state ILIKE ${searchParam})`;
    }
    if (status && status !== 'All') {
      where += status === 'pending' ? ' AND vr.status IS NULL' : ` AND vr.status = ${addParam(status)}`;
    }

    const countRes = await pool.query(`SELECT COUNT(*) AS total FROM mandi.market_prices_v3 mp ${LATEST_VERIFICATION_JOIN} ${where}`, params);

    const orderColumn = SORT_COLUMNS[sortBy] || SORT_COLUMNS.id;
    const orderDir = sortDir === 'desc' ? 'DESC' : 'ASC';
    // A tiebreaker on market_price_id keeps paging stable when the sorted
    // column has duplicate values — but Postgres rejects the same column
    // listed twice, which happens whenever the sort column already IS id.
    const orderBy = orderColumn === SORT_COLUMNS.id
      ? `${orderColumn} ${orderDir}`
      : `${orderColumn} ${orderDir}, mp.market_price_id ${orderDir}`;

    const limitParam = addParam(pageSize);
    const offsetParam = addParam((page - 1) * pageSize);

    const dataRes = await pool.query(`
      SELECT mp.*, vr.status, vr.notes, vr.verified_by, vr.verified_at
      FROM mandi.market_prices_v3 mp
      ${LATEST_VERIFICATION_JOIN}
      ${where}
      ORDER BY ${orderBy}
      LIMIT ${limitParam} OFFSET ${offsetParam}
    `, params);

    res.json({
      records: dataRes.rows.map(toRecord),
      total: Number(countRes.rows[0].total),
      page,
      pageSize,
    });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.get('/api/records/:id', async (req, res) => {
  try {
    const pool = await getPool();
    const result = await pool.query(
      `
        SELECT mp.*, vr.status, vr.notes, vr.verified_by, vr.verified_at
        FROM mandi.market_prices_v3 mp
        ${LATEST_VERIFICATION_JOIN}
        WHERE mp.market_price_id = $1::bigint
      `,
      [req.params.id]
    );
    if (result.rows.length === 0) return res.json(null);
    res.json(toRecord(result.rows[0]));
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.get('/api/records/:id/next-pending', async (req, res) => {
  try {
    const pool = await getPool();
    const query = `
      SELECT mp.market_price_id
      FROM mandi.market_prices_v3 mp
      ${LATEST_VERIFICATION_JOIN}
      WHERE vr.status IS NULL AND mp.market_price_id > $1::bigint
      ORDER BY mp.market_price_id
      LIMIT 1
    `;
    let result = await pool.query(query, [req.params.id]);
    if (result.rows.length === 0) {
      // wrap around
      result = await pool.query(query, [0]);
    }
    const foundId = result.rows[0]?.market_price_id;
    res.json({ id: foundId === undefined ? null : Number(foundId) });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.post('/api/records/:id/verify', async (req, res) => {
  try {
    const { status, fieldResults, issueType, expectedValue, scrapedValue, notes } = req.body;
    const pool = await getPool();
    await pool.query(
      `
        INSERT INTO mandi.verification_results
          (market_price_id, status, field_results, issue_type, expected_value, scraped_value, notes, verified_by)
        VALUES ($1::bigint, $2, $3, $4, $5, $6, $7, $8)
      `,
      [
        req.params.id, status, JSON.stringify(fieldResults ?? []), issueType || null,
        expectedValue || null, scrapedValue || null, notes || null, req.user.name,
      ]
    );
    res.json({ ok: true });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.get('/api/duplicates', async (req, res) => {
  try {
    const pool = await getPool();
    const groups = await pool.query(`
      SELECT state, district, market, commodity, arrival_date, COUNT(*) AS c
      FROM mandi.market_prices_v3
      GROUP BY state, district, market, commodity, arrival_date
      HAVING COUNT(*) > 1
    `);
    const out = [];
    for (const g of groups.rows.slice(0, 50)) {
      const rows = await pool.query(
        `
          SELECT * FROM mandi.market_prices_v3
          WHERE state=$1 AND district=$2 AND market=$3 AND commodity=$4 AND arrival_date=$5
        `,
        [g.state, g.district, g.market, g.commodity, g.arrival_date]
      );
      out.push(rows.rows.map(toRecord));
    }
    res.json(out);
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.get('/api/issues', async (req, res) => {
  try {
    const pool = await getPool();
    const result = await pool.query(`
      SELECT vr.verification_id, vr.market_price_id, vr.status, vr.issue_type, vr.expected_value,
             vr.scraped_value, vr.notes, vr.verified_at, mp.state, mp.district, mp.market, mp.commodity
      FROM mandi.verification_results vr
      JOIN mandi.market_prices_v3 mp ON mp.market_price_id = vr.market_price_id
      WHERE vr.status IN ('mismatch', 'missing', 'duplicate')
      ORDER BY vr.verified_at DESC
      LIMIT 200
    `);
    res.json(result.rows.map((r) => ({
      id: Number(r.verification_id),
      recordId: Number(r.market_price_id),
      record: { state: r.state, district: r.district, market: r.market, commodity: r.commodity },
      issueType: r.issue_type,
      expectedValue: r.expected_value,
      scrapedValue: r.scraped_value,
      notes: r.notes,
      status: r.status,
      createdAt: new Date(r.verified_at).toISOString(),
    })));
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

const PORT = process.env.PORT || 4000;
app.listen(PORT, () => console.log(`Verification API listening on http://localhost:${PORT}`));
