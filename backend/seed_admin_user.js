// One-time seed for the single account this dashboard accepts logins from.
// Signup is disabled (server.js) — this script is the only way an account
// gets created. Safe to re-run: upserts rather than failing on conflict.
import 'dotenv/config';
import pg from 'pg';
import { hashPassword } from './auth.js';

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

const ADMIN_NAME = 'CEO';
const ADMIN_EMAIL = 'ceo@farmvaidya.ai';
const ADMIN_PASSWORD = 'Admin@1212';

try {
  await client.connect();
  const passwordHash = await hashPassword(ADMIN_PASSWORD);

  await client.query(
    `
      INSERT INTO mandi.dashboard_users (name, email, password_hash)
      VALUES ($1, $2, $3)
      ON CONFLICT (email) DO UPDATE SET name = EXCLUDED.name, password_hash = EXCLUDED.password_hash
    `,
    [ADMIN_NAME, ADMIN_EMAIL, passwordHash]
  );

  console.log(`Seeded admin account: ${ADMIN_EMAIL}`);
  await client.end();
  process.exit(0);
} catch (err) {
  console.error('FAILED:', err.message);
  process.exit(1);
}
