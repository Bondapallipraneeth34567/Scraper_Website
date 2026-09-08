import 'dotenv/config';
import pg from 'pg';

const { Client } = pg;

const client = new Client({
  host: process.env.POSTGRES_HOST,
  port: Number(process.env.POSTGRES_PORT),
  database: process.env.POSTGRES_DB,
  user: process.env.POSTGRES_USER,
  password: process.env.POSTGRES_PASSWORD,
  ssl: process.env.POSTGRES_SSL === 'true' ? { rejectUnauthorized: false } : false,
  connectionTimeoutMillis: 15000,
});

try {
  await client.connect();
  const result = await client.query('SELECT COUNT(*) AS total FROM mandi.market_prices_v3');
  console.log('CONNECTED OK');
  console.log('market_prices_v3 row count:', result.rows[0].total);
  await client.end();
  process.exit(0);
} catch (err) {
  console.error('CONNECTION FAILED:', err.message);
  process.exit(1);
}
