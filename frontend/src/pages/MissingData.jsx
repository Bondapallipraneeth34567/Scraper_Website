import { useEffect, useState } from 'react';
import { FileWarning } from 'lucide-react';
import DashboardLayout from '../components/layout/DashboardLayout';
import { getAllRecords, subscribe } from '../services/verificationService';
import { formatDate } from '../utils/format';

export default function MissingData() {
  const [records, setRecords] = useState(null);

  useEffect(() => {
    const load = () => getAllRecords({ status: 'missing' }).then(setRecords);
    load();
    return subscribe(load);
  }, []);

  return (
    <DashboardLayout
      title="Missing Data"
      subtitle="Records a tester checked against the live site and couldn't find — this can only be found by hand, not detected automatically"
    >
      <div className="rounded-card border border-line bg-surface shadow-card">
        {!records ? (
          <div className="px-4 py-10 text-center text-sm text-ink-soft">Loading…</div>
        ) : records.length === 0 ? (
          <div className="flex flex-col items-center gap-2 px-4 py-12 text-center text-sm text-ink-soft">
            <FileWarning size={22} className="opacity-50" />
            Nothing flagged missing yet — this fills in as testers verify records and mark ones the live site
            doesn't confirm.
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="bg-canvas text-left text-[10.5px] font-semibold uppercase tracking-wide text-ink-soft">
                <th className="px-3.5 py-2.5">ID</th>
                <th className="px-3.5 py-2.5">Date</th>
                <th className="px-3.5 py-2.5">State</th>
                <th className="px-3.5 py-2.5">Market</th>
                <th className="px-3.5 py-2.5">Commodity</th>
                <th className="px-3.5 py-2.5">Notes</th>
              </tr>
            </thead>
            <tbody>
              {records.map((r) => (
                <tr
                  key={r.id}
                  className="border-t border-line-soft hover:bg-canvas"
                >
                  <td className="tnum px-3.5 py-2.5">#{r.id}</td>
                  <td className="tnum px-3.5 py-2.5">{formatDate(r.date)}</td>
                  <td className="px-3.5 py-2.5">{r.state}</td>
                  <td className="px-3.5 py-2.5">{r.market}</td>
                  <td className="px-3.5 py-2.5 font-semibold">{r.commodity}</td>
                  <td className="max-w-xs truncate px-3.5 py-2.5 text-ink-soft">{r.notes || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </DashboardLayout>
  );
}
