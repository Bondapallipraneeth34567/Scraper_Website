import DashboardLayout from '../components/layout/DashboardLayout';
import { useAuth } from '../auth/AuthProvider';

export default function Settings() {
  const { user } = useAuth();

  return (
    <DashboardLayout title="Settings" subtitle="Tester profile and data source">
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <div className="rounded-card border border-line bg-surface p-5 shadow-card">
          <div className="mb-3 text-xs font-semibold uppercase tracking-wide text-ink-soft">Tester</div>
          <div className="text-sm text-ink">
            <div><span className="text-ink-soft">Name:</span> {user.name}</div>
            <div><span className="text-ink-soft">Role:</span> {user.role}</div>
          </div>
        </div>
        <div className="rounded-card border border-line bg-surface p-5 shadow-card">
          <div className="mb-3 text-xs font-semibold uppercase tracking-wide text-ink-soft">Data source</div>
          <div className="text-sm text-ink">
            Live — <b>dbo.MarketPrices</b> on Azure SQL, via the API at{' '}
            <code className="rounded bg-canvas px-1 py-0.5">backend/server.js</code>. Every row, filter, and
            duplicate check reflects the real database, not a sample. Verification verdicts (Correct / Incorrect /
            Missing / Duplicate) are saved to <b>dbo.VerificationResults</b> and persist for every tester, not just
            this browser.
          </div>
        </div>
      </div>
    </DashboardLayout>
  );
}
