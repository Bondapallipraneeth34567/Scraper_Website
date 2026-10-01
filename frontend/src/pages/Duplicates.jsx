import { useCallback, useEffect, useState } from 'react';
import toast from 'react-hot-toast';
import { CheckCircle2, Trash2 } from 'lucide-react';
import DashboardLayout from '../components/layout/DashboardLayout';
import {
  getDuplicateGroups, getDuplicateAudit, resolveDuplicate,
} from '../services/verificationService';
import { formatCurrency, formatDate } from '../utils/format';

const TABS = [
  { kind: 'exact', label: 'True duplicates' },
  { kind: 'variants', label: 'Variety / grade variants' },
];

export default function Duplicates() {
  const [kind, setKind] = useState('exact');
  const [page, setPage] = useState(1);
  const [data, setData] = useState(null);
  const [audit, setAudit] = useState([]);
  const [keep, setKeep] = useState({}); // groupIndex -> id of row to keep
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    setData(null);
    getDuplicateGroups({ kind, page }).then(setData).catch((e) => toast.error(e.message));
    getDuplicateAudit().then(setAudit).catch(() => {});
  }, [kind, page]);

  useEffect(load, [load]);

  async function resolve(group, i) {
    const keepId = keep[i] ?? group[0].id; // default: the oldest row
    const deleteIds = group.map((r) => r.id).filter((id) => id !== keepId);
    if (!window.confirm(`Keep #${keepId} and permanently delete ${deleteIds.length} duplicate row(s)? The deleted rows are saved to the audit log.`)) return;
    setBusy(true);
    try {
      await resolveDuplicate(keepId, deleteIds);
      toast.success(`Kept #${keepId}, deleted ${deleteIds.length}`);
      load();
    } catch (e) {
      toast.error(e.message);
    } finally {
      setBusy(false);
    }
  }

  const totalPages = data ? Math.max(1, Math.ceil(data.total / data.pageSize)) : 1;
  const exact = kind === 'exact';

  return (
    <DashboardLayout
      title="Duplicates"
      subtitle="Rows that look like the same price line more than once — checked directly against the full table"
    >
      <div className="mb-5 flex flex-wrap gap-3">
        <div className="w-full rounded-card border border-line bg-surface p-5 shadow-card sm:w-72">
          <div className="text-xs font-semibold uppercase tracking-wide text-ink-soft">True duplicates</div>
          <div className="tnum mt-1 text-3xl font-bold text-ink">{data ? data.exactGroups.toLocaleString('en-IN') : '…'}</div>
          <div className="text-xs text-ink-soft">
            Same state, district, market, commodity, <b>variety, grade</b> and date (ignoring case/spacing). Safe to merge.
          </div>
        </div>
        <div className="w-full rounded-card border border-line bg-surface p-5 shadow-card sm:w-72">
          <div className="text-xs font-semibold uppercase tracking-wide text-ink-soft">Variety / grade variants</div>
          <div className="tnum mt-1 text-3xl font-bold text-ink">{data ? data.variantGroups.toLocaleString('en-IN') : '…'}</div>
          <div className="text-xs text-ink-soft">
            Same market, commodity and date but a different variety or grade (e.g. Grade Range-1/2/3). Legitimate separate
            prices — <b>not</b> duplicates, so they cannot be deleted here.
          </div>
        </div>
      </div>

      <div className="mb-3 flex gap-2">
        {TABS.map((t) => (
          <button
            key={t.kind}
            type="button"
            onClick={() => { setKind(t.kind); setPage(1); }}
            className={`rounded-lg border px-3.5 py-2 text-xs font-semibold shadow-card ${
              kind === t.kind ? 'border-slate-800 bg-slate-800 text-white' : 'border-line bg-surface text-ink hover:bg-canvas'
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      <div className="rounded-card border border-line bg-surface shadow-card">
        {!data ? (
          <div className="px-4 py-10 text-center text-sm text-ink-soft">Checking…</div>
        ) : data.groups.length === 0 ? (
          <div className="flex flex-col items-center gap-2 px-4 py-10 text-center text-sm text-ink-soft">
            <CheckCircle2 size={22} className="text-good" />
            {exact
              ? 'No true duplicates in the database. (The table’s unique key also blocks new ones.)'
              : 'No variety/grade variants.'}
          </div>
        ) : (
          <div className="divide-y divide-line-soft">
            {data.groups.map((group, i) => (
              <div key={`${group[0].id}-${i}`} className="px-4 py-3">
                <div className="mb-1.5 flex items-center justify-between gap-3">
                  <div className="text-xs font-semibold text-ink">
                    {group[0].state} · {group[0].district} · {group[0].market} · {group[0].commodity} · {formatDate(group[0].date)}
                  </div>
                  {exact && (
                    <button
                      type="button"
                      disabled={busy}
                      onClick={() => resolve(group, i)}
                      className="inline-flex flex-none items-center gap-1.5 rounded-lg border border-line bg-surface px-3 py-1.5 text-xs font-semibold text-bad hover:bg-canvas disabled:opacity-50"
                    >
                      <Trash2 size={13} /> Keep selected, delete rest
                    </button>
                  )}
                </div>
                <div className="flex flex-wrap gap-2">
                  {group.map((r) => (
                    <label
                      key={r.id}
                      className={`flex items-center gap-1.5 rounded-lg border px-2.5 py-1 text-xs text-ink ${
                        exact && (keep[i] ?? group[0].id) === r.id ? 'border-good bg-canvas' : 'border-line'
                      }`}
                    >
                      {exact && (
                        <input
                          type="radio"
                          name={`keep-${i}`}
                          checked={(keep[i] ?? group[0].id) === r.id}
                          onChange={() => setKeep((k) => ({ ...k, [i]: r.id }))}
                        />
                      )}
                      #{r.id} · {r.variety || '—'} / {r.grade || '—'} · {formatCurrency(r.modalPrice)}
                    </label>
                  ))}
                </div>
              </div>
            ))}
          </div>
        )}
        {data && data.total > data.pageSize && (
          <div className="flex items-center justify-between border-t border-line px-4 py-2.5 text-xs text-ink-soft">
            <span>{data.total.toLocaleString('en-IN')} groups · page {page} of {totalPages.toLocaleString('en-IN')}</span>
            <span className="flex gap-2">
              <button type="button" disabled={page <= 1} onClick={() => setPage(page - 1)} className="rounded-lg border border-line px-3 py-1 font-semibold disabled:opacity-40">Prev</button>
              <button type="button" disabled={page >= totalPages} onClick={() => setPage(page + 1)} className="rounded-lg border border-line px-3 py-1 font-semibold disabled:opacity-40">Next</button>
            </span>
          </div>
        )}
      </div>

      {audit.length > 0 && (
        <div className="mt-5 rounded-card border border-line bg-surface shadow-card">
          <div className="border-b border-line px-4 py-3 text-xs font-semibold uppercase tracking-wide text-ink-soft">
            Deletion audit log (latest {audit.length})
          </div>
          <ul className="divide-y divide-line-soft text-xs text-ink">
            {audit.map((a) => (
              <li key={a.id} className="px-4 py-2">
                #{a.deletedId} deleted (kept #{a.keptId}) by {a.deletedBy} · {a.deletedAt.replace('T', ' ').slice(0, 16)} UTC ·{' '}
                {a.row.market} / {a.row.commodity} / {a.row.arrival_date?.slice(0, 10)}
              </li>
            ))}
          </ul>
        </div>
      )}
    </DashboardLayout>
  );
}
