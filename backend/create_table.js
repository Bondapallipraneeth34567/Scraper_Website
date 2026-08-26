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

const ddl = `
IF OBJECT_ID('dbo.VerificationResults', 'U') IS NULL
CREATE TABLE dbo.VerificationResults (
    verification_id  BIGINT IDENTITY(1,1) PRIMARY KEY,
    market_price_id  BIGINT NOT NULL,
    status           VARCHAR(20) NOT NULL,
    field_results    NVARCHAR(MAX) NULL,
    issue_type       VARCHAR(50) NULL,
    expected_value   NVARCHAR(200) NULL,
    scraped_value    NVARCHAR(200) NULL,
    notes            NVARCHAR(MAX) NULL,
    verified_by      NVARCHAR(100) NULL,
    verified_at      DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
    CONSTRAINT FK_VerificationResults_MarketPrices
        FOREIGN KEY (market_price_id) REFERENCES dbo.MarketPrices(market_price_id)
);
`;

const indexDdl = `
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_VerificationResults_MarketPriceId' AND object_id = OBJECT_ID('dbo.VerificationResults'))
CREATE INDEX IX_VerificationResults_MarketPriceId ON dbo.VerificationResults(market_price_id, verified_at DESC);
`;

try {
  const pool = await sql.connect(config);
  await pool.request().batch(ddl);
  await pool.request().batch(indexDdl);
  console.log('VerificationResults table ready.');
  await pool.close();
  process.exit(0);
} catch (err) {
  console.error('FAILED:', err.message);
  process.exit(1);
}
