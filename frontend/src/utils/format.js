export function formatCurrency(value) {
  // Number(null) is 0 and Number('') is 0 — neither is NaN, so both would
  // silently render as "₹0" (implying a real zero price) without this
  // explicit check first.
  if (value === null || value === undefined || value === '') return '—';
  const n = typeof value === 'number' ? value : Number(value);
  if (Number.isNaN(n)) return '—';
  return `₹${n.toLocaleString('en-IN')}`;
}

/** '2026-08-17' -> '17-08-2026' (DD-MM-YYYY, matching Agmarknet's own format). */
export function formatDate(isoDate) {
  if (!isoDate) return '—';
  const [y, m, d] = isoDate.split('-');
  if (!y || !m || !d) return isoDate;
  return `${d}-${m}-${y}`;
}

export function formatDateTime(isoDate, time) {
  return `${formatDate(isoDate)}${time ? ` · ${time}` : ''}`;
}

export function formatPercent(value, digits = 1) {
  if (value === null || value === undefined || Number.isNaN(value)) return '—';
  return `${value.toFixed(digits)}%`;
}
