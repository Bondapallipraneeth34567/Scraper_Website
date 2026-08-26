import 'dotenv/config';
import sql from 'mssql';

const config = {
  server: process.env.MSSQL_HOST,
  port: Number(process.env.MSSQL_PORT),
  database: process.env.MSSQL_DB,
  user: process.env.MSSQL_USER,
  password: process.env.MSSQL_PASSWORD,
  options: {
    encrypt: true,
    trustServerCertificate: false,
  },
  connectionTimeout: 15000,
};

try {
  const pool = await sql.connect(config);
  const result = await pool.request().query(
    "SELECT COUNT(*) AS total FROM dbo.MarketPrices"
  );
  console.log('CONNECTED OK');
  console.log('MarketPrices row count:', result.recordset[0].total);
  await pool.close();
  process.exit(0);
} catch (err) {
  console.error('CONNECTION FAILED:', err.message);
  process.exit(1);
}
