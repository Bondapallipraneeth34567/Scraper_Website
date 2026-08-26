import { useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import toast from 'react-hot-toast';
import { Upload, Download } from 'lucide-react';
import DashboardLayout from '../components/layout/DashboardLayout';
import FilterBar from '../components/FilterBar';
import RecordsTable from '../components/RecordsTable';
import ImportCsvModal from '../components/ImportCsvModal';
import useRecords from '../hooks/useRecords';
import { getIssues } from '../services/verificationService';
import { buildVerificationResultsCSV, downloadCSV } from '../utils/csv';
import { ISSUE_TYPE_LABELS } from '../data/issueTypes';

export default function DataVerification() {
  const [searchParams] = useSearchParams();
  const [filters, setFilters] = useState({ search: searchParams.get('search') || '' });
  const [importOpen, setImportOpen] = useState(false);
  const { records, total, loading, error, refetch } = useRecords(filters);

  async function handleExport() {
    if (records.length === 0) {
      toast.error('No records match the current filters to export');
      return;
    }

    // Cross-reference issues so a mismatched/missing/duplicate row's issue
    // type and the tester's recorded source value make it into the export
    // — not just the scraped side, which is all the record itself holds.
    const issues = await getIssues();
    const latestIssueByRecord = new Map();
    issues.forEach((i) => {
      if (!latestIssueByRecord.has(i.recordId)) latestIssueByRecord.set(i.recordId, i);
    });

    const rows = records.map((r) => {
      const issue = latestIssueByRecord.get(r.id);
      return {
        record_id: r.id,
        date: r.date,
        state: r.state,
        district: r.district,
        market: r.market,
        commodity: r.commodity,
        scraped_value: r.modalPrice,
        source_value: issue?.expectedValue || '',
        status: r.status,
        issue_type: issue ? ISSUE_TYPE_LABELS[issue.issueType] || issue.issueType : '',
        notes: r.notes || issue?.notes || '',
        verified_by: r.verifiedBy || '',
        verified_at: r.verifiedAt || '',
      };
    });
    downloadCSV(`verification-results-${new Date().toISOString().slice(0, 10)}.csv`, buildVerificationResultsCSV(rows));
    toast.success(`Exported ${rows.length.toLocaleString('en-IN')} records matching current filters`);
  }

  return (
    <DashboardLayout title="Data Verification" subtitle={`${total.toLocaleString('en-IN')} records match current filters`} onRefresh={refetch}>
      <div className="mb-4 flex justify-end gap-2">
        <button
          type="button"
          onClick={() => setImportOpen(true)}
          className="inline-flex items-center gap-1.5 rounded-lg border border-line bg-surface px-3.5 py-2 text-xs font-semibold text-ink shadow-card hover:bg-canvas"
        >
          <Upload size={14} /> Import CSV
        </button>
        <button
          type="button"
          onClick={handleExport}
          className="inline-flex items-center gap-1.5 rounded-lg border border-line bg-surface px-3.5 py-2 text-xs font-semibold text-ink shadow-card hover:bg-canvas"
        >
          <Download size={14} /> Export Verification Results
        </button>
      </div>

      <p className="mb-3 text-xs text-ink-soft">
        Data is available from August 1, 2026 and is updated daily.
      </p>

      <FilterBar filters={filters} onChange={setFilters} />
      <RecordsTable records={records} total={total} loading={loading} error={error} />

      <ImportCsvModal open={importOpen} onClose={() => setImportOpen(false)} />
    </DashboardLayout>
  );
}
