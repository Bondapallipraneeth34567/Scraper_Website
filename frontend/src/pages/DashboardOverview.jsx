import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Database, Layers, Copy, FileWarning, MapPinned } from 'lucide-react';
import DashboardLayout from '../components/layout/DashboardLayout';
import SummaryCard from '../components/SummaryCard';
import SiteCoverage from '../components/SiteCoverage';
import { getOverviewStats } from '../services/verificationService';
import { formatDate } from '../utils/format';

export default function DashboardOverview() {
  const navigate = useNavigate();
  const [overview, setOverview] = useState(null);

  useEffect(() => {
    getOverviewStats().then(setOverview);
  }, []);

  if (!overview) {
    return (
      <DashboardLayout title="Dashboard" subtitle="Scraped data verification overview">
        <div className="text-sm text-ink-soft">Loading…</div>
      </DashboardLayout>
    );
  }

  const cards = [
    { key: 'total', label: 'Total Rows in DB', value: overview.total_rows, accent: 'slate', icon: Database, to: '/verification' },
    { key: 'markets', label: 'Markets Covered', value: overview.markets, accent: 'slate', icon: MapPinned, to: '/verification' },
    { key: 'commodities', label: 'Commodities Seen', value: overview.commodities, accent: 'slate', icon: Layers, to: '/verification' },
    { key: 'duplicate', label: 'Duplicate Groups', value: overview.duplicate_groups, accent: 'purple', icon: Copy, to: '/duplicates' },
    { key: 'missing', label: 'Missing Data', value: overview.missing_records ?? 0, accent: 'orange', icon: FileWarning, to: '/missing' },
  ];

  return (
    <DashboardLayout
      title="Dashboard"
      subtitle={`Live dbo.MarketPrices — ${formatDate(overview.min_date)} to ${formatDate(overview.max_date)}`}
    >
      <div className="flex flex-wrap gap-3">
        {cards.map((c) => (
          <div key={c.key} className="w-full sm:w-56">
            <SummaryCard
              label={c.label}
              value={c.value.toLocaleString('en-IN')}
              accent={c.accent}
              icon={c.icon}
              onClick={() => navigate(c.to)}
            />
          </div>
        ))}
      </div>

      <div className="mt-5 max-w-2xl">
        <SiteCoverage overview={overview} />
      </div>
    </DashboardLayout>
  );
}
