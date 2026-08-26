import { useMemo, useState } from 'react';
import { ArrowUpDown, ArrowUp, ArrowDown } from 'lucide-react';
import { formatCurrency, formatDate } from '../utils/format';

const COLUMNS = [
  { key: 'id', label: 'ID', sortable: true },
  { key: 'date', label: 'Date', sortable: true },
  { key: 'state', label: 'State', sortable: true },
  { key: 'district', label: 'District', sortable: true },
  { key: 'market', label: 'Market', sortable: true },
  { key: 'commodity', label: 'Commodity', sortable: true },
  { key: 'minPrice', label: 'Min Price', sortable: true, num: true },
  { key: 'maxPrice', label: 'Max Price', sortable: true, num: true },
  { key: 'modalPrice', label: 'Modal Price', sortable: true, num: true },
  { key: 'arrivalQty', label: 'Arrival Quantity', sortable: true, num: true },
  { key: 'arrivalUnit', label: 'Arrival Unit', sortable: true },
];

const PAGE_SIZE = 10;

export default function RecordsTable({ records, total, loading, error }) {
  const [sort, setSort] = useState({ key: 'id', dir: 'asc' });
  const [page, setPage] = useState(1);

  const sorted = useMemo(() => {
    const copy = [...records];
    copy.sort((a, b) => {
      const av = a[sort.key];
      const bv = b[sort.key];
      if (av === bv) return 0;
      const result = av > bv ? 1 : -1;
      return sort.dir === 'asc' ? result : -result;
    });
    return copy;
  }, [records, sort]);

  const totalPages = Math.max(1, Math.ceil(sorted.length / PAGE_SIZE));
  const pageSafe = Math.min(page, totalPages);
  const pageRecords = sorted.slice((pageSafe - 1) * PAGE_SIZE, pageSafe * PAGE_SIZE);

  function toggleSort(key) {
    setSort((s) => (s.key === key ? { key, dir: s.dir === 'asc' ? 'desc' : 'asc' } : { key, dir: 'asc' }));
  }

  return (
    <div className="rounded-card border border-line bg-surface shadow-card">
      <div className="overflow-x-auto">
        <table className="w-full min-w-[900px] border-collapse text-sm">
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
            ) : pageRecords.length === 0 ? (
              <tr>
                <td colSpan={COLUMNS.length} className="px-4 py-10 text-center text-sm text-ink-soft">
                  No records match these filters.
                </td>
              </tr>
            ) : (
              pageRecords.map((r) => (
                <tr
                  key={r.id}
                  className="border-b border-line-soft last:border-0 hover:bg-canvas"
                >
                  <td className="tnum whitespace-nowrap px-3.5 py-2.5 text-ink-soft">{r.id}</td>
                  <td className="tnum whitespace-nowrap px-3.5 py-2.5">{formatDate(r.date)}</td>
                  <td className="whitespace-nowrap px-3.5 py-2.5">{r.state}</td>
                  <td className="whitespace-nowrap px-3.5 py-2.5">{r.district}</td>
                  <td className="whitespace-nowrap px-3.5 py-2.5">{r.market}</td>
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
          Showing {sorted.length === 0 ? 0 : (pageSafe - 1) * PAGE_SIZE + 1}
          –{Math.min(pageSafe * PAGE_SIZE, sorted.length)} of {sorted.length.toLocaleString('en-IN')} loaded
          {total !== undefined && total > sorted.length && (
            <>
              {' '}· <b className="text-ink">{total.toLocaleString('en-IN')}</b> total match these filters
              (narrow the filters or add server-side paging to browse the rest)
            </>
          )}
        </span>
        <div className="flex items-center gap-1">
          <button
            type="button"
            disabled={pageSafe <= 1}
            onClick={() => setPage((p) => p - 1)}
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
            onClick={() => setPage((p) => p + 1)}
            className="rounded-md border border-line px-2 py-1 font-mono disabled:opacity-40"
          >
            ›
          </button>
        </div>
      </div>
    </div>
  );
}
