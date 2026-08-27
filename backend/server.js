import express from 'express';
import cors from 'cors';
import cookieParser from 'cookie-parser';
import { readFileSync } from 'fs';
import { dirname, join } from 'path';
import { fileURLToPath } from 'url';
import { getPool, sql } from './db.js';
import { comparePassword, signToken, COOKIE_NAME, setCookieOptions, clearCookieOptions, requireAuth } from './auth.js';

const app = express();

// Full Agmarknet commodity-group taxonomy (group -> [commodity, ...]),
// harvested straight from the live site by
// bot_processors/pricing/agmarknet_scraper.py::harvest_commodity_reference()
// (see frontend/src/data/siteTotals.js — 16 groups / 604 commodities,
// confirmed against agmarknet.gov.in on 2026-08-20). dbo.MarketPrices has
// no commodity_group column, so this reference file is the only source of
// truth for the group a commodity belongs to.
const __dirname = dirname(fileURLToPath(import.meta.url));
const COMMODITY_GROUPS = JSON.parse(
  readFileSync(join(__dirname, '..', 'bot_processors', 'data', 'agmarknet_commodities.json'), 'utf8')
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

app.post('/api/auth/login', async (req, res) => {
  try {
    const email = normalizeEmail(req.body.email);
    const password = String(req.body.password || '');

    const pool = await getPool();
    const result = await pool.request()
      .input('email', sql.NVarChar, email)
      .query('SELECT user_id, name, email, password_hash, role FROM dbo.DashboardUsers WHERE email = @email');

    const row = result.recordset[0];
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

const TARGET_COMMODITIES_PER_DAY = 585;

// Row -> frontend record shape (mirrors what mockData.js used to produce,
// so the frontend's existing components don't need to change).
function toRecord(row) {
  return {
    // mssql returns BIGINT columns as strings (can't safely fit all 64-bit
    // values in a JS number) -- market_price_id is nowhere near that range
    // in practice, and the frontend sorts/compares ids numerically, so
    // coercing here avoids "100" sorting before "27" as strings would.
    id: Number(row.market_price_id),
    date: row.arrival_date instanceof Date ? row.arrival_date.toISOString().slice(0, 10) : row.arrival_date,
    state: row.state,
    district: row.district,
    market: row.market,
    commodity: row.commodity,
    minPrice: row.min_price_quintal === null ? null : Number(row.min_price_quintal),
    maxPrice: row.max_price_quintal === null ? null : Number(row.max_price_quintal),
    modalPrice: row.modal_price_quintal === null ? null : Number(row.modal_price_quintal),
    arrivalQty: row.arrival_qty === null ? null : Number(row.arrival_qty),
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
    FROM dbo.VerificationResults
  ) vr ON vr.market_price_id = mp.market_price_id AND vr.rn = 1
`;

app.get('/api/overview', async (req, res) => {
  try {
    const pool = await getPool();
    const totals = await pool.request().query(`
      SELECT COUNT(*) AS total_rows, COUNT(DISTINCT state) AS states,
             COUNT(DISTINCT district) AS districts, COUNT(DISTINCT market) AS markets,
             COUNT(DISTINCT commodity) AS commodities,
             MIN(arrival_date) AS min_date, MAX(arrival_date) AS max_date
      FROM dbo.MarketPrices
    `);
    const dupes = await pool.request().query(`
      SELECT COUNT(*) AS duplicate_groups FROM (
        SELECT state, district, market, commodity, arrival_date
        FROM dbo.MarketPrices
        GROUP BY state, district, market, commodity, arrival_date
        HAVING COUNT(*) > 1
      ) g
    `);
    const missing = await pool.request().query(`
      SELECT COUNT(*) AS missing_records
      FROM (
        SELECT vr.status,
               ROW_NUMBER() OVER (PARTITION BY vr.market_price_id ORDER BY vr.verified_at DESC) AS rn
        FROM dbo.VerificationResults vr
      ) latest
      WHERE latest.rn = 1 AND latest.status = 'missing'
    `);
    const t = totals.recordset[0];
    res.json({
      total_rows: t.total_rows,
      states: t.states,
      districts: t.districts,
      markets: t.markets,
      commodities: t.commodities,
      min_date: t.min_date?.toISOString().slice(0, 10),
      max_date: t.max_date?.toISOString().slice(0, 10),
      duplicate_groups: dupes.recordset[0].duplicate_groups,
      missing_records: missing.recordset[0].missing_records,
    });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.get('/api/completeness', async (req, res) => {
  try {
    const pool = await getPool();
    const result = await pool.request().query(`
      SELECT CAST(arrival_date AS date) AS d, COUNT(DISTINCT crop_keyword) AS commodities, COUNT(*) AS rows_count
      FROM dbo.MarketPrices GROUP BY CAST(arrival_date AS date) ORDER BY d
    `);
    res.json({
      dates: result.recordset.map((r) => ({
        date: r.d.toISOString().slice(0, 10),
        commodities: r.commodities,
        rows: r.rows_count,
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
    const result = await pool.request().query('SELECT DISTINCT state FROM dbo.MarketPrices ORDER BY state');
    res.json(result.recordset.map((r) => r.state));
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.get('/api/filters/districts', async (req, res) => {
  try {
    const pool = await getPool();
    const request = pool.request();
    let query = 'SELECT DISTINCT district FROM dbo.MarketPrices WHERE 1=1';
    if (req.query.state && req.query.state !== 'All') {
      request.input('state', sql.NVarChar, req.query.state);
      query += ' AND state = @state';
    }
    query += ' ORDER BY district';
    const result = await request.query(query);
    res.json(result.recordset.map((r) => r.district));
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.get('/api/filters/markets', async (req, res) => {
  try {
    const pool = await getPool();
    const request = pool.request();
    let query = 'SELECT DISTINCT market FROM dbo.MarketPrices WHERE 1=1';
    if (req.query.state && req.query.state !== 'All') {
      request.input('state', sql.NVarChar, req.query.state);
      query += ' AND state = @state';
    }
    if (req.query.district && req.query.district !== 'All') {
      request.input('district', sql.NVarChar, req.query.district);
      query += ' AND district = @district';
    }
    query += ' ORDER BY market';
    const result = await request.query(query);
    res.json(result.recordset.map((r) => r.market));
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// Commodity Group is a reference-file dimension (no DB column — see
// COMMODITY_GROUPS above), so this list doesn't depend on the DB at all.
app.get('/api/filters/commodity-groups', (req, res) => {
  res.json(Object.keys(COMMODITY_GROUPS));
});

app.get('/api/filters/commodities', async (req, res) => {
  try {
    const pool = await getPool();
    const result = await pool.request().query('SELECT DISTINCT commodity FROM dbo.MarketPrices ORDER BY commodity');
    let commodities = result.recordset.map((r) => r.commodity);
    const group = req.query.group;
    if (group && group !== 'All') {
      const groupSet = new Set((COMMODITY_GROUPS[group] || []).map((c) => c.toLowerCase()));
      commodities = commodities.filter((c) => groupSet.has(String(c).toLowerCase()));
    }
    res.json(commodities);
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// Cross-references the DB's scraped commodities against the full site
// taxonomy so the frontend can show exactly which commodities/groups have
// no data yet, instead of that gap being invisible.
app.get('/api/filters/commodity-coverage', async (req, res) => {
  try {
    const pool = await getPool();
    const result = await pool.request().query('SELECT DISTINCT commodity FROM dbo.MarketPrices');
    const dbSet = new Set(result.recordset.map((r) => String(r.commodity).toLowerCase()));
    const groups = Object.entries(COMMODITY_GROUPS).map(([group, commodities]) => {
      const missing = commodities.filter((c) => !dbSet.has(c.toLowerCase()));
      return { group, total: commodities.length, scraped: commodities.length - missing.length, missing };
    });
    res.json(groups);
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// Whitelisted so ORDER BY can never take raw user input.
const SORT_COLUMNS = {
  id: 'mp.market_price_id',
  date: 'mp.arrival_date',
  state: 'mp.state',
  district: 'mp.district',
  market: 'mp.market',
  commodity: 'mp.commodity',
  minPrice: 'mp.min_price_quintal',
  maxPrice: 'mp.max_price_quintal',
  modalPrice: 'mp.modal_price_quintal',
  arrivalQty: 'mp.arrival_qty',
  arrivalUnit: 'mp.arrival_unit',
};

app.get('/api/records', async (req, res) => {
  try {
    const pool = await getPool();
    const request = pool.request();
    const { date, state, district, market, commodity, commodityGroup, status, search, sortBy, sortDir } = req.query;
    const page = Math.max(1, Number(req.query.page) || 1);
    const pageSize = Math.min(200, Number(req.query.pageSize) || 50);

    let where = 'WHERE 1=1';
    if (date) {
      request.input('date', sql.Date, date);
      where += ' AND mp.arrival_date = @date';
    }
    if (state && state !== 'All') {
      request.input('state', sql.NVarChar, state);
      where += ' AND mp.state = @state';
    }
    if (district && district !== 'All') {
      request.input('district', sql.NVarChar, district);
      where += ' AND mp.district = @district';
    }
    if (market && market !== 'All') {
      request.input('market', sql.NVarChar, market);
      where += ' AND mp.market = @market';
    }
    if (commodity && commodity !== 'All') {
      request.input('commodity', sql.NVarChar, commodity);
      where += ' AND mp.commodity = @commodity';
    }
    if (commodityGroup && commodityGroup !== 'All') {
      // No commodity_group column on dbo.MarketPrices — translate the group
      // to its member commodity names via the reference taxonomy instead.
      const names = COMMODITY_GROUPS[commodityGroup] || [];
      if (names.length === 0) {
        where += ' AND 1=0';
      } else {
        const paramNames = names.map((name, i) => {
          request.input(`cg${i}`, sql.NVarChar, name);
          return `@cg${i}`;
        });
        where += ` AND mp.commodity IN (${paramNames.join(',')})`;
      }
    }
    if (search) {
      request.input('search', sql.NVarChar, `%${search}%`);
      where += ' AND (mp.market LIKE @search OR mp.commodity LIKE @search OR mp.district LIKE @search OR mp.state LIKE @search)';
    }
    if (status && status !== 'All') {
      request.input('status', sql.VarChar, status);
      where += status === 'pending' ? ' AND vr.status IS NULL' : ' AND vr.status = @status';
    }

    request.input('offset', sql.Int, (page - 1) * pageSize);
    request.input('limit', sql.Int, pageSize);

    const countResult = await pool.request();
    // rebuild inputs for count query (mssql requests aren't reusable across two .query calls with different param sets cleanly)
    Object.entries(request.parameters).forEach(([name, param]) => {
      if (name !== 'offset' && name !== 'limit') countResult.input(name, param.type, param.value);
    });
    const countRes = await countResult.query(`SELECT COUNT(*) AS total FROM dbo.MarketPrices mp ${LATEST_VERIFICATION_JOIN} ${where}`);

    const orderColumn = SORT_COLUMNS[sortBy] || SORT_COLUMNS.id;
    const orderDir = sortDir === 'desc' ? 'DESC' : 'ASC';
    // A tiebreaker on market_price_id keeps paging stable when the sorted
    // column has duplicate values — but SQL Server rejects the same column
    // listed twice, which happens whenever the sort column already IS id.
    const orderBy = orderColumn === SORT_COLUMNS.id
      ? `${orderColumn} ${orderDir}`
      : `${orderColumn} ${orderDir}, mp.market_price_id ${orderDir}`;

    const dataRes = await request.query(`
      SELECT mp.*, vr.status, vr.notes, vr.verified_by, vr.verified_at
      FROM dbo.MarketPrices mp
      ${LATEST_VERIFICATION_JOIN}
      ${where}
      ORDER BY ${orderBy}
      OFFSET @offset ROWS FETCH NEXT @limit ROWS ONLY
    `);

    res.json({
      records: dataRes.recordset.map(toRecord),
      total: countRes.recordset[0].total,
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
    const result = await pool.request()
      .input('id', sql.BigInt, req.params.id)
      .query(`
        SELECT mp.*, vr.status, vr.notes, vr.verified_by, vr.verified_at
        FROM dbo.MarketPrices mp
        ${LATEST_VERIFICATION_JOIN}
        WHERE mp.market_price_id = @id
      `);
    if (result.recordset.length === 0) return res.json(null);
    res.json(toRecord(result.recordset[0]));
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.get('/api/records/:id/next-pending', async (req, res) => {
  try {
    const pool = await getPool();
    const query = `
      SELECT TOP 1 mp.market_price_id
      FROM dbo.MarketPrices mp
      ${LATEST_VERIFICATION_JOIN}
      WHERE vr.status IS NULL AND mp.market_price_id > @id
      ORDER BY mp.market_price_id
    `;
    let result = await pool.request().input('id', sql.BigInt, req.params.id).query(query);
    if (result.recordset.length === 0) {
      // wrap around
      result = await pool.request().input('id', sql.BigInt, 0).query(query);
    }
    const foundId = result.recordset[0]?.market_price_id;
    res.json({ id: foundId === undefined ? null : Number(foundId) });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.post('/api/records/:id/verify', async (req, res) => {
  try {
    const { status, fieldResults, issueType, expectedValue, scrapedValue, notes } = req.body;
    const pool = await getPool();
    await pool.request()
      .input('marketPriceId', sql.BigInt, req.params.id)
      .input('status', sql.VarChar, status)
      .input('fieldResults', sql.NVarChar, JSON.stringify(fieldResults ?? []))
      .input('issueType', sql.VarChar, issueType || null)
      .input('expectedValue', sql.NVarChar, expectedValue || null)
      .input('scrapedValue', sql.NVarChar, scrapedValue || null)
      .input('notes', sql.NVarChar, notes || null)
      .input('verifiedBy', sql.NVarChar, req.user.name)
      .query(`
        INSERT INTO dbo.VerificationResults
          (market_price_id, status, field_results, issue_type, expected_value, scraped_value, notes, verified_by)
        VALUES (@marketPriceId, @status, @fieldResults, @issueType, @expectedValue, @scrapedValue, @notes, @verifiedBy)
      `);
    res.json({ ok: true });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.get('/api/duplicates', async (req, res) => {
  try {
    const pool = await getPool();
    const groups = await pool.request().query(`
      SELECT state, district, market, commodity, arrival_date, COUNT(*) AS c
      FROM dbo.MarketPrices
      GROUP BY state, district, market, commodity, arrival_date
      HAVING COUNT(*) > 1
    `);
    const out = [];
    for (const g of groups.recordset.slice(0, 50)) {
      const rows = await pool.request()
        .input('state', sql.NVarChar, g.state)
        .input('district', sql.NVarChar, g.district)
        .input('market', sql.NVarChar, g.market)
        .input('commodity', sql.NVarChar, g.commodity)
        .input('date', sql.Date, g.arrival_date)
        .query(`
          SELECT * FROM dbo.MarketPrices
          WHERE state=@state AND district=@district AND market=@market AND commodity=@commodity AND arrival_date=@date
        `);
      out.push(rows.recordset.map(toRecord));
    }
    res.json(out);
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.get('/api/issues', async (req, res) => {
  try {
    const pool = await getPool();
    const result = await pool.request().query(`
      SELECT TOP 200 vr.verification_id, vr.market_price_id, vr.status, vr.issue_type, vr.expected_value,
             vr.scraped_value, vr.notes, vr.verified_at, mp.state, mp.district, mp.market, mp.commodity
      FROM dbo.VerificationResults vr
      JOIN dbo.MarketPrices mp ON mp.market_price_id = vr.market_price_id
      WHERE vr.status IN ('mismatch', 'missing', 'duplicate')
      ORDER BY vr.verified_at DESC
    `);
    res.json(result.recordset.map((r) => ({
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
