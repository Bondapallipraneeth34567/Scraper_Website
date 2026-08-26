import 'dotenv/config';
import sql from 'mssql';

const config = {
  server: process.env.MSSQL_HOST,
  port: Number(process.env.MSSQL_PORT),
  database: process.env.MSSQL_DB,
  user: process.env.MSSQL_USER,
  password: process.env.MSSQL_PASSWORD,
  options: { encrypt: true, trustServerCertificate: false },
  connectionTimeout: 15000,
};

// Named DashboardUsers, not Users — dbo.Users already exists in this same
// database as a live production table for the unrelated WhatsApp/voice bot
// (farmer profiles: phone_number, village_town, preferred_market, etc.).
// Do not rename/reuse that table.
const ddl = `
IF OBJECT_ID('dbo.DashboardUsers', 'U') IS NULL
CREATE TABLE dbo.DashboardUsers (
    user_id        BIGINT IDENTITY(1,1) PRIMARY KEY,
    name           NVARCHAR(200) NOT NULL,
    email          NVARCHAR(256) NOT NULL,
    password_hash  NVARCHAR(200) NOT NULL,
    role           VARCHAR(50) NOT NULL DEFAULT 'QA Tester',
    created_at     DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
    CONSTRAINT UQ_DashboardUsers_Email UNIQUE (email)
);
`;

try {
  const pool = await sql.connect(config);
  await pool.request().batch(ddl);
  console.log('DashboardUsers table ready.');
  await pool.close();
  process.exit(0);
} catch (err) {
  console.error('FAILED:', err.message);
  process.exit(1);
}
