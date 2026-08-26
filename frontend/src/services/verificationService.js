// Real backend-backed service layer — talks to the Express API in
// backend/server.js, which reads/writes the actual Azure SQL database
// (dbo.MarketPrices for scraped data, dbo.VerificationResults for tester
// verdicts). No mock data, no localStorage — every export here is a real
// network call. Every page imports only from this file, never mockData.js
// or the DB directly, so this was the one file that had to change to go
// from sample data to live data.

const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:4000/api';

const listeners = new Set();
/** Subscribe to any write (verify) — used by pages to re-fetch after a change. */
export function subscribe(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}
function notify() {
  listeners.forEach((fn) => fn());
}

async function apiGet(path) {
  const res = await fetch(`${API_BASE}${path}`);
  if (!res.ok) throw new Error(`API error ${res.status}`);
  return res.json();
}

async function apiPost(path, body) {
  const res = await fetch(`${API_BASE}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`API error ${res.status}`);
  return res.json();
}

export async function getRecords(filters = {}) {
  const params = new URLSearchParams();
  Object.entries(filters).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== '') params.set(key, value);
  });
  params.set('pageSize', filters.pageSize || 200);
  // { records, total } — total is the real count matching the filters in
  // the database, not just how many rows were fetched into this page
  // (pageSize caps at 200 per request, but total reflects e.g. all
  // 72,000+ rows when no filter narrows it).
  return apiGet(`/records?${params.toString()}`);
}

export async function getRecordById(id) {
  return apiGet(`/records/${id}`);
}

// NOTE on auth: this posts to the live Azure-SQL-backed API with no
// Authorization header, and verifiedBy is plain client-supplied data — the
// backend (backend/server.js, not in this repo) MUST independently verify
// the caller's identity and set/validate verifiedBy server-side before this
// goes live against production data. Nothing in this file can enforce that
// from the frontend; removing the old 'Praneeth' fallback below only stops
// the frontend from *silently* misattributing verifications when a caller
// forgets to pass verifiedBy — it is not a substitute for server-side auth.
export async function verifyRecord(id, { status, fieldResults, issueType, expectedValue, scrapedValue, notes, verifiedBy }) {
  if (!verifiedBy) {
    throw new Error('verifyRecord: verifiedBy is required (pass the current user’s identity, not a default)');
  }
  await apiPost(`/records/${id}/verify`, {
    status, fieldResults, issueType, expectedValue, scrapedValue, notes, verifiedBy,
  });
  notify();
  return getRecordById(id);
}

export async function getIssues() {
  return apiGet('/issues');
}

export async function getOverviewStats() {
  return apiGet('/overview');
}

export async function getStates() {
  return apiGet('/filters/states');
}

export async function getDistricts(state) {
  const q = state && state !== 'All' ? `?state=${encodeURIComponent(state)}` : '';
  return apiGet(`/filters/districts${q}`);
}

export async function getMarkets(state, district) {
  const params = new URLSearchParams();
  if (state && state !== 'All') params.set('state', state);
  if (district && district !== 'All') params.set('district', district);
  const q = params.toString();
  return apiGet(`/filters/markets${q ? `?${q}` : ''}`);
}

export async function getCommodities() {
  return apiGet('/filters/commodities');
}

export async function getCompletenessByDate() {
  return apiGet('/completeness');
}

export async function getDuplicateGroups() {
  return apiGet('/duplicates');
}

export async function getNextPendingId(currentId) {
  const { id } = await apiGet(`/records/${currentId}/next-pending`);
  return id;
}

/** CSV import into the live production table isn't supported — see
 * ImportCsvModal, which surfaces this as an error toast rather than
 * pretending to import into dbo.MarketPrices. */
export async function importRecords() {
  throw new Error('CSV import into the live database is not available yet.');
}
