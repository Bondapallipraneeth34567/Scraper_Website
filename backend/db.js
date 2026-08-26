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
  pool: { max: 10, min: 0, idleTimeoutMillis: 30000 },
};

let poolPromise;

/** Shared connection pool — one per process, reused across requests. */
export function getPool() {
  if (!poolPromise) poolPromise = sql.connect(config);
  return poolPromise;
}

export { sql };
