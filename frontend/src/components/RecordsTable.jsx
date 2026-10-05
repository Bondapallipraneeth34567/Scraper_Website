import { ArrowUpDown, ArrowUp, ArrowDown } from 'lucide-react';
import { formatCurrency, formatDate } from '../utils/format';

const COLUMNS = [
  { key: 'id', label: 'ID', sortable: true },
  { key: 'date', label: 'Date', sortable: true },
  { key: 'state', label: 'State', sortable: true },
  { key: 'district', label: 'District', sortable: true },
  { key: 'market', label: 'Market', sortable: true },
  { key: 'commodityGroup', label: 'Commodity Group', sortable: true },
  { key: 'commodity', label: 'Commodity', sortable: true },
  { key: 'minPrice', label: 'Min Price', sortable: true, num: true },
  { key: 'maxPrice', label: 'Max Price', sortable: true, num: true },
  { key: 'modalPrice', label: 'Modal Price', sortable: true, num: true },
  { key: 'arrivalQty', label: 'Arrival Quantity', sortable: true, num: true },
  { key: 'arrivalUnit', label: 'Arrival Unit', sortable: true },
];

/** Records/page/sort are all driven by the server (see useRecords) — this
 * component only renders the current page, it doesn't slice or sort
 * client-side. That's the fix for filtered results (e.g. one state with
 * thousands of matching rows) silently getting cut off at whatever the API
 * happened to return for page 1. */
export default function RecordsTable({ records, total, loading, error, page, pageSize, sort, onSortChange, onPageChange }) {
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const pageSafe = Math.min(page, totalPages);

  function toggleSort(key) {
    onSortChange(key === sort.key && sort.dir === 'asc' ? { key, dir: 'desc' } : { key, dir: 'asc' });
  }

  return (
    <div className="rounded-card border border-line bg-surface shadow-card">
      <div className="overflow-x-auto">
        <table className="w-full min-w-[1000px] border-collapse text-sm">
          <thead>
            <tr className="bg-canvas">
              {COLUMNS.map((col) => {
                const isActive = sort.key === col.key;
                const SortIcon = isActive ? (sort.dir === 'asc' ? ArrowUp : ArrowDown) : ArrowUpDown;
                return (
                  <th
                    key={col.key}
                    onClick={() => col.sortable && toggleSort(col.key)}
                    className={`whitespace-nowrap border-b border-line px-3.5 py-2.5 text-left text-[10.5px] font-semibold uppercase tracking-wide
                      ${isActive ? 'text-ink' : 'text-ink-soft'}
                      ${col.sortable ? 'cursor-pointer select-none hover:text-ink' : ''} ${col.num ? 'text-right' : ''}`}
                  >
                    <span className="inline-flex items-center gap-1">
                      {col.label}
                      {col.sortable && <SortIcon size={11} className={isActive ? 'text-good' : 'opacity-50'} />}
                    </span>
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr>
                <td colSpan={COLUMNS.length} className="px-4 py-10 text-center text-sm text-ink-soft">
                  Loading records…
                </td>
              </tr>
            ) : error ? (
              <tr>
                <td colSpan={COLUMNS.length} className="px-4 py-10 text-center text-sm text-red-600">
                  Unable to load records. The backend may be unavailable or the query failed.
                </td>
              </tr>
            ) : records.length === 0 ? (
              <tr>
                <td colSpan={COLUMNS.length} className="px-4 py-10 text-center text-sm text-ink-soft">
                  No records match these filters.
                </td>
              </tr>
            ) : (
              records.map((r) => (
                <tr
                  key={r.id}
                  className="border-b border-line-soft last:border-0 hover:bg-canvas"
                >
                  <td className="tnum whitespace-nowrap px-3.5 py-2.5 text-ink-soft">{r.id}</td>
                  <td className="tnum whitespace-nowrap px-3.5 py-2.5">{formatDate(r.date)}</td>
                  <td className="whitespace-nowrap px-3.5 py-2.5">{r.state}</td>
                  <td className="whitespace-nowrap px-3.5 py-2.5">{r.district}</td>
                  <td className="whitespace-nowrap px-3.5 py-2.5">{r.market}</td>
                  <td className="whitespace-nowrap px-3.5 py-2.5 text-ink-soft">{r.commodityGroup || '—'}</td>
                  <td className="whitespace-nowrap px-3.5 py-2.5 font-semibold">{r.commodity}</td>
                  <td className="tnum whitespace-nowrap px-3.5 py-2.5 text-right">{formatCurrency(r.minPrice)}</td>
                  <td className="tnum whitespace-nowrap px-3.5 py-2.5 text-right">{formatCurrency(r.maxPrice)}</td>
                  <td className="tnum whitespace-nowrap px-3.5 py-2.5 text-right font-semibold">{formatCurrency(r.modalPrice)}</td>
                  <td className="tnum whitespace-nowrap px-3.5 py-2.5 text-right text-ink-soft">
                    {r.arrivalQty ? r.arrivalQty.toLocaleString('en-IN') : '—'}
                  </td>
                  <td className="whitespace-nowrap px-3.5 py-2.5 text-ink-soft">{r.arrivalUnit || '—'}</td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      <div className="flex items-center justify-between border-t border-line px-3.5 py-2.5 text-xs text-ink-soft">
        <span>
          Showing {total === 0 ? 0 : (pageSafe - 1) * pageSize + 1}
          –{Math.min(pageSafe * pageSize, total)} of <b className="text-ink">{total.toLocaleString('en-IN')}</b> matching
          these filters
        </span>
        <div className="flex items-center gap-1">
          <button
            type="button"
            disabled={pageSafe <= 1}
            onClick={() => onPageChange(pageSafe - 1)}
            className="rounded-md border border-line px-2 py-1 font-mono disabled:opacity-40"
          >
            ‹
          </button>
          <span className="tnum px-2">
            {pageSafe} / {totalPages}
          </span>
          <button
            type="button"
            disabled={pageSafe >= totalPages}
            onClick={() => onPageChange(pageSafe + 1)}
            className="rounded-md border border-line px-2 py-1 font-mono disabled:opacity-40"
          >
            ›
          </button>
        </div>
      </div>
    </div>
  );
}
