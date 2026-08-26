// Minimal CSV read/write — no external dependency, per the "don't introduce
// unnecessary dependencies" requirement. Handles quoted fields and commas
// inside quotes, which covers real-world scraped exports well enough for
// an internal QA tool.

export function parseCSV(text) {
  const rows = [];
  let row = [];
  let field = '';
  let inQuotes = false;

  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (inQuotes) {
      if (c === '"') {
        if (text[i + 1] === '"') {
          field += '"';
          i++;
        } else {
          inQuotes = false;
        }
      } else {
        field += c;
      }
    } else if (c === '"') {
      inQuotes = true;
    } else if (c === ',') {
      row.push(field);
      field = '';
    } else if (c === '\n' || c === '\r') {
      if (c === '\r' && text[i + 1] === '\n') i++;
      row.push(field);
      field = '';
      if (row.some((v) => v !== '')) rows.push(row);
      row = [];
    } else {
      field += c;
    }
  }
  if (field !== '' || row.length > 0) {
    row.push(field);
    rows.push(row);
  }

  if (rows.length === 0) return [];
  const headers = rows[0].map((h) => h.trim());
  return rows.slice(1).map((r) => {
    const obj = {};
    headers.forEach((h, idx) => {
      obj[h] = (r[idx] ?? '').trim();
    });
    return obj;
  });
}

const EXPECTED_COLUMNS = [
  'date', 'state', 'district', 'market', 'commodity',
  'min_price', 'max_price', 'modal_price', 'source_url',
];

/** Maps raw CSV rows (snake_case columns) onto the app's record shape. */
export function mapImportedRows(rawRows) {
  return rawRows.map((row, i) => ({
    id: 90000 + i, // temp id until saved; a real backend would assign one
    date: row.date || '',
    state: row.state || '',
    district: row.district || '',
    market: row.market || '',
    commodity: row.commodity || '',
    minPrice: Number(row.min_price) || null,
    maxPrice: Number(row.max_price) || null,
    modalPrice: Number(row.modal_price) || null,
    sourceUrl: row.source_url || '',
    scrapedAt: '',
    status: 'pending',
  }));
}

/** Preview + validation summary shown before the import is committed. */
export function summarizeImport(records) {
  const requiredFields = ['date', 'state', 'market', 'commodity', 'modalPrice'];
  let missingFieldsCount = 0;
  const seen = new Set();
  let duplicateCount = 0;

  records.forEach((r) => {
    const hasAllRequired = requiredFields.every((f) => r[f] !== '' && r[f] !== null);
    if (!hasAllRequired) missingFieldsCount += 1;

    const key = `${r.date}|${r.state}|${r.market}|${r.commodity}`;
    if (seen.has(key)) duplicateCount += 1;
    else seen.add(key);
  });

  return {
    total: records.length,
    missingFieldsCount,
    duplicateCount,
    columnsExpected: EXPECTED_COLUMNS,
  };
}

/** Converts verification results into the export CSV's row shape. */
export function buildVerificationResultsCSV(results) {
  const headers = [
    'record_id', 'date', 'state', 'district', 'market', 'commodity',
    'scraped_value', 'source_value', 'status', 'issue_type', 'notes',
    'verified_by', 'verified_at',
  ];

  // Neutralize CSV/formula injection: a field starting with =, +, -, @, tab,
  // or CR is interpreted as a formula by Excel/Sheets when the file is
  // opened, and every field here can carry scraped or tester-entered text
  // (notes, market, commodity, ...) an attacker could shape. Prefixing with
  // a leading apostrophe forces spreadsheet apps to treat it as a literal
  // string; it's invisible in the rendered cell.
  const FORMULA_LEADING_CHARS = /^[=+\-@\t\r]/;
  const escape = (v) => {
    let s = v === null || v === undefined ? '' : String(v);
    if (FORMULA_LEADING_CHARS.test(s)) s = `'${s}`;
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };

  const lines = [headers.join(',')];
  results.forEach((r) => {
    lines.push(
      headers
        .map((h) => escape(r[toCamel(h)] ?? r[h] ?? ''))
        .join(',')
    );
  });
  return lines.join('\r\n');
}

function toCamel(snake) {
  return snake.replace(/_([a-z])/g, (_, c) => c.toUpperCase());
}

/** Triggers a browser download of the given CSV text. */
export function downloadCSV(filename, csvText) {
  const blob = new Blob([csvText], { type: 'text/csv;charset=utf-8;' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}
