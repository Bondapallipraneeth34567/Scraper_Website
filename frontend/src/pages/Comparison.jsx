import { Fragment, useEffect, useMemo, useState } from 'react';
import { CheckCircle2, AlertTriangle, ChevronDown, ChevronRight } from 'lucide-react';
import DashboardLayout from '../components/layout/DashboardLayout';
import { getComparisons } from '../services/verificationService';
import { formatDate } from '../utils/format';

const n = (v) => (v === null || v === undefined ? '—' : Number(v).toLocaleString('en-IN'));

function Diff({ label, items }) {
  if (!items?.length) return null;
  return (
    <div className="mb-2">
      <div className="text-[10.5px] font-semibold uppercase tracking-wide text-ink-soft">{label}</div>
      <ul className="mt-1 space-y-0.5 text-xs text-ink">
        {items.map((it, i) => (
          <li key={i} className="tnum">{typeof it === 'string' ? it : `${it.key} — ${it.field}: site ${it.site ?? '∅'} vs csv ${it.csv ?? '∅'}`}</li>
        ))}
      </ul>
    </div>
  );
}

export default function Comparison() {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [date, setDate] = useState('');
  const [onlyBad, setOnlyBad] = useState(true);
  const [open, setOpen] = useState(null);

  useEffect(() => {
    setData(null);
    getComparisons(date).then(setData).catch((e) => setError(e.message));
  }, [date]);

  const rows = useMemo(() => (data?.rows || []).filter((r) => !onlyBad || !r.inSync), [data, onlyBad]);
  const totals = useMemo(() => {
    const all = data?.rows || [];
    return {
      checked: all.length,
      mismatched: all.filter((r) => !r.inSync).length,
      site: all.reduce((a, r) => a + (r.siteDeclared || 0), 0),
      db: all.reduce((a, r) => a + (r.dbRows || 0), 0),
    };
  }, [data]);

  return (
    <DashboardLayout
      title="Site Comparison"
      subtitle="Rows stored in our database compared with what the live AGMARKNET site returns, per commodity"
    >
      {error && <div className="mb-4 rounded-card border border-line bg-surface p-4 text-sm text-bad">{error}</div>}
      {!data && !error && <div className="text-sm text-ink-soft">Loading…</div>}

      {data && data.dates.length === 0 && (
        <div className="rounded-card border border-line bg-surface p-6 text-sm text-ink-soft shadow-card">
          No comparison has been run yet. The check needs a CAPTCHA per commodity, so it runs from the pipeline:
          <pre className="mt-3 overflow-x-auto rounded-lg bg-canvas p-3 text-xs text-ink">
            python -m mandi_pipeline.run --verify-csv --date YYYY-MM-DD --deep
          </pre>
          Results appear here automatically.
        </div>
      )}

      {data && data.dates.length > 0 && (
        <>
          <div className="mb-4 flex flex-wrap items-center gap-3">
            <select
              value={data.date}
              onChange={(e) => setDate(e.target.value)}
              className="rounded-lg border border-line bg-surface px-3 py-2 text-xs font-semibold text-ink shadow-card"
            >
              {data.dates.map((d) => (
                <option key={d.date} value={d.date}>
                  {formatDate(d.date)} — {d.checked} checked, {d.mismatched} mismatched
                </option>
              ))}
            </select>
            <label className="flex items-center gap-1.5 text-xs text-ink">
              <input type="checkbox" checked={onlyBad} onChange={(e) => setOnlyBad(e.target.checked)} />
              Only show mismatches
            </label>
            <span className="text-xs text-ink-soft">
              Last checked {data.dates.find((d) => d.date === data.date)?.lastChecked.replace('T', ' ').slice(0, 16)} UTC
            </span>
          </div>

          <div className="mb-4 flex flex-wrap gap-3">
            {[
              ['Commodities checked', totals.checked],
              ['In sync', totals.checked - totals.mismatched],
              ['Mismatched', totals.mismatched],
              ['Rows on site', totals.site],
              ['Rows in DB', totals.db],
            ].map(([label, v]) => (
              <div key={label} className="w-full rounded-card border border-line bg-surface p-4 shadow-card sm:w-44">
                <div className="text-[10.5px] font-semibold uppercase tracking-wide text-ink-soft">{label}</div>
                <div className="tnum mt-1 text-2xl font-bold text-ink">{n(v)}</div>
              </div>
            ))}
          </div>

          <div className="rounded-card border border-line bg-surface shadow-card">
            {rows.length === 0 ? (
              <div className="flex flex-col items-center gap-2 px-4 py-12 text-center text-sm text-ink-soft">
                <CheckCircle2 size={22} className="text-good" />
                Every checked commodity matches the site for this date.
              </div>
            ) : (
              <table className="w-full text-sm">
                <thead>
                  <tr className="bg-canvas text-left text-[10.5px] font-semibold uppercase tracking-wide text-ink-soft">
                    <th className="w-8 px-3.5 py-2.5" />
                    <th className="px-3.5 py-2.5">Commodity</th>
                    <th className="px-3.5 py-2.5 text-right">Site</th>
                    <th className="px-3.5 py-2.5 text-right">DB</th>
                    <th className="px-3.5 py-2.5 text-right">CSV</th>
                    <th className="px-3.5 py-2.5 text-right">Missing in DB</th>
                    <th className="px-3.5 py-2.5 text-right">Stale in DB</th>
                    <th className="px-3.5 py-2.5 text-right">Cell diffs</th>
                    <th className="px-3.5 py-2.5">Result</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r) => {
                    const isOpen = open === r.commodity;
                    const hasDetail = !r.inSync || r.fixed;
                    return (
                      <Fragment key={r.commodity}>
                        <tr
                          className={`border-t border-line-soft ${hasDetail ? 'cursor-pointer hover:bg-canvas' : ''}`}
                          onClick={() => hasDetail && setOpen(isOpen ? null : r.commodity)}
                        >
                          <td className="px-3.5 py-2.5 text-ink-soft">
                            {hasDetail && (isOpen ? <ChevronDown size={14} /> : <ChevronRight size={14} />)}
                          </td>
                          <td className="px-3.5 py-2.5 font-semibold">{r.commodity}</td>
                          <td className="tnum px-3.5 py-2.5 text-right">{n(r.siteDeclared)}</td>
                          <td className="tnum px-3.5 py-2.5 text-right">{n(r.dbRows)}</td>
                          <td className="tnum px-3.5 py-2.5 text-right">{n(r.csvRows)}</td>
                          <td className="tnum px-3.5 py-2.5 text-right">{n(r.missingInDb)}</td>
                          <td className="tnum px-3.5 py-2.5 text-right">{n(r.staleInDb)}</td>
                          <td className="tnum px-3.5 py-2.5 text-right">{n(r.fieldDiffCount)}</td>
                          <td className="px-3.5 py-2.5">
                            <span className={`inline-flex items-center gap-1 text-xs font-semibold ${r.inSync ? 'text-good' : 'text-bad'}`}>
                              {r.inSync ? <CheckCircle2 size={13} /> : <AlertTriangle size={13} />}
                              {r.inSync ? (r.fixed ? 'Fixed' : 'In sync') : r.verdict}
                            </span>
                          </td>
                        </tr>
                        {isOpen && (
                          <tr className="border-t border-line-soft bg-canvas">
                            <td />
                            <td colSpan={8} className="px-3.5 py-3">
                              {r.fixed && <div className="mb-2 text-xs text-ink-soft">{r.verdict}</div>}
                              <Diff label="On the site, missing from DB (state/district/market/variety/grade)" items={r.details?.missing} />
                              <Diff label="In DB, no longer on the site" items={r.details?.stale} />
                              <Diff label="In our CSV only" items={r.details?.only_csv} />
                              <Diff label="Cell differences (site vs CSV)" items={r.details?.field_diffs} />
                            </td>
                          </tr>
                        )}
                      </Fragment>
                    );
                  })}
                </tbody>
              </table>
            )}
          </div>
        </>
      )}
    </DashboardLayout>
  );
}
