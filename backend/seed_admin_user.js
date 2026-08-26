// One-time seed for the single account this dashboard accepts logins from.
// Signup is disabled (server.js) — this script is the only way an account
// gets created. Safe to re-run: upserts rather than failing on conflict.
import 'dotenv/config';
import sql from 'mssql';
import { hashPassword } from './auth.js';

const config = {
  server: process.env.MSSQL_HOST,
  port: Number(process.env.MSSQL_PORT),
  database: process.env.MSSQL_DB,
  user: process.env.MSSQL_USER,
  password: process.env.MSSQL_PASSWORD,
  options: { encrypt: true, trustServerCertificate: false },
  connectionTimeout: 15000,
};

const ADMIN_NAME = 'CEO';
const ADMIN_EMAIL = 'ceo@farmvaidya.ai';
const ADMIN_PASSWORD = 'Admin@1212';

try {
  const pool = await sql.connect(config);
  const passwordHash = await hashPassword(ADMIN_PASSWORD);

  await pool.request()
    .input('name', sql.NVarChar, ADMIN_NAME)
    .input('email', sql.NVarChar, ADMIN_EMAIL)
    .input('passwordHash', sql.NVarChar, passwordHash)
    .query(`
      MERGE dbo.DashboardUsers AS target
      USING (SELECT @email AS email) AS src
      ON target.email = src.email
      WHEN MATCHED THEN
        UPDATE SET name = @name, password_hash = @passwordHash
      WHEN NOT MATCHED THEN
        INSERT (name, email, password_hash) VALUES (@name, @email, @passwordHash);
    `);

  console.log(`Seeded admin account: ${ADMIN_EMAIL}`);
  await pool.close();
  process.exit(0);
} catch (err) {
  console.error('FAILED:', err.message);
  process.exit(1);
}
