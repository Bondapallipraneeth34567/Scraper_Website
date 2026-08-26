import { useEffect, useState } from 'react';
import { CheckCircle2 } from 'lucide-react';
import DashboardLayout from '../components/layout/DashboardLayout';
import { getDuplicateGroups, getOverviewStats } from '../services/verificationService';
import { formatCurrency, formatDate } from '../utils/format';

export default function Duplicates() {
  const [groups, setGroups] = useState(null);
  const [overview, setOverview] = useState(null);

  useEffect(() => {
    getDuplicateGroups().then(setGroups);
    getOverviewStats().then(setOverview);
  }, []);

  return (
    <DashboardLayout
      title="Duplicates"
      subtitle="Same (state, district, market, commodity, date) appearing more than once — a pure DB check, no live site needed"
    >
      <div className="mb-5 rounded-card border border-line bg-surface p-5 shadow-card">
        <div className="text-xs font-semibold uppercase tracking-wide text-ink-soft">Production DB — full table</div>
        <div className="tnum mt-1 text-3xl font-bold text-ink">
          {overview ? overview.duplicate_groups.toLocaleString('en-IN') : '…'}
        </div>
        <div className="text-xs text-ink-soft">
          duplicate groups found across all {overview ? overview.total_rows.toLocaleString('en-IN') : '…'} rows in
          dbo.MarketPrices, checked directly against the database — not a sample.
        </div>
      </div>

      <div className="rounded-card border border-line bg-surface shadow-card">
        <div className="border-b border-line px-4 py-3 text-xs font-semibold uppercase tracking-wide text-ink-soft">
          Loaded sample — {groups ? groups.length : '…'} duplicate group(s)
        </div>
        {!groups ? (
          <div className="px-4 py-10 text-center text-sm text-ink-soft">Checking…</div>
        ) : groups.length === 0 ? (
          <div className="flex flex-col items-center gap-2 px-4 py-10 text-center text-sm text-ink-soft">
            <CheckCircle2 size={22} className="text-good" />
            No duplicates in the loaded sample — matches what the full production check found.
          </div>
        ) : (
          <div className="divide-y divide-line-soft">
            {groups.map((group, i) => (
              <div key={i} className="px-4 py-3">
                <div className="mb-1.5 text-xs font-semibold text-ink">
                  {group[0].state} · {group[0].district} · {group[0].market} · {group[0].commodity} ·{' '}
                  {formatDate(group[0].date)}
                </div>
                <div className="flex flex-wrap gap-2">
                  {group.map((r) => (
                    <span
                      key={r.id}
                      className="rounded-lg border border-line px-2.5 py-1 text-xs text-ink"
                    >
                      #{r.id} · {formatCurrency(r.modalPrice)}
                    </span>
                  ))}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </DashboardLayout>
  );
}
