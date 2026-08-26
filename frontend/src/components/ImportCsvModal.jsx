import { useState } from 'react';
import toast from 'react-hot-toast';
import { Upload } from 'lucide-react';
import Modal from './Modal';
import { parseCSV, mapImportedRows, summarizeImport } from '../utils/csv';
import { importRecords } from '../services/verificationService';

export default function ImportCsvModal({ open, onClose }) {
  const [rows, setRows] = useState(null);
  const [summary, setSummary] = useState(null);
  const [fileName, setFileName] = useState('');
  const [importing, setImporting] = useState(false);

  function handleFile(e) {
    const file = e.target.files?.[0];
    if (!file) return;
    setFileName(file.name);
    const reader = new FileReader();
    reader.onload = () => {
      const parsed = mapImportedRows(parseCSV(String(reader.result)));
      setRows(parsed);
      setSummary(summarizeImport(parsed));
    };
    reader.readAsText(file);
  }

  async function handleImport() {
    if (!rows) return;
    setImporting(true);
    const toastId = toast.loading('Importing records...');
    try {
      const result = await importRecords(rows);
      toast.success(`${result.imported.toLocaleString('en-IN')} records imported successfully`, { id: toastId });
      if (result.missingFields > 0) toast.error(`${result.missingFields} records contain missing fields`);
      if (result.duplicates > 0) toast.error(`${result.duplicates} duplicate records skipped`);
      reset();
      onClose();
    } catch {
      toast.error('Import failed', { id: toastId });
    } finally {
      setImporting(false);
    }
  }

  function reset() {
    setRows(null);
    setSummary(null);
    setFileName('');
  }

  return (
    <Modal open={open} onClose={() => { reset(); onClose(); }} title="Import Scraped CSV" width="max-w-2xl">
      <p className="mb-3 text-xs text-ink-soft">
        Expected columns: <code className="rounded bg-canvas px-1 py-0.5">date, state, district, market, commodity, min_price, max_price, modal_price, source_url</code>
      </p>

      <label className="flex cursor-pointer flex-col items-center gap-2 rounded-card border-2 border-dashed border-line px-6 py-8 text-center hover:bg-canvas">
        <Upload size={22} className="text-ink-soft" />
        <span className="text-sm font-medium text-ink">{fileName || 'Click to choose a CSV file'}</span>
        <input type="file" accept=".csv" className="hidden" onChange={handleFile} />
      </label>

      {summary && (
        <div className="mt-4 space-y-1.5 rounded-card border border-line bg-canvas p-3.5 text-sm">
          <div className="flex items-center gap-1.5 text-good">
            ✓ {summary.total.toLocaleString('en-IN')} records ready to import
          </div>
          {summary.missingFieldsCount > 0 && (
            <div className="flex items-center gap-1.5 text-warn">
              ⚠ {summary.missingFieldsCount} record(s) contain missing fields
            </div>
          )}
          {summary.duplicateCount > 0 && (
            <div className="flex items-center gap-1.5 text-bad">
              ✗ {summary.duplicateCount} duplicate record(s) detected in this file
            </div>
          )}
        </div>
      )}

      {rows && rows.length > 0 && (
        <div className="mt-4 max-h-48 overflow-auto rounded-card border border-line">
          <table className="w-full text-xs">
            <thead className="bg-canvas">
              <tr>
                {['date', 'state', 'market', 'commodity', 'modalPrice'].map((h) => (
                  <th key={h} className="px-2.5 py-1.5 text-left font-semibold uppercase text-ink-soft">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.slice(0, 5).map((r) => (
                <tr key={r.id} className="border-t border-line-soft">
                  <td className="px-2.5 py-1.5">{r.date}</td>
                  <td className="px-2.5 py-1.5">{r.state}</td>
                  <td className="px-2.5 py-1.5">{r.market}</td>
                  <td className="px-2.5 py-1.5">{r.commodity}</td>
                  <td className="px-2.5 py-1.5">{r.modalPrice}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="border-t border-line-soft px-2.5 py-1.5 text-[11px] text-ink-soft">
            Showing preview of first 5 of {rows.length} rows
          </div>
        </div>
      )}

      <div className="mt-5 flex justify-end gap-2">
        <button
          type="button"
          onClick={() => { reset(); onClose(); }}
          className="rounded-lg border border-line px-4 py-2 text-sm font-medium text-ink-soft hover:bg-canvas"
        >
          Cancel
        </button>
        <button
          type="button"
          disabled={!rows || importing}
          onClick={handleImport}
          className="rounded-lg bg-good px-4 py-2 text-sm font-semibold text-white hover:bg-green-600 disabled:opacity-40"
        >
          Import {rows ? rows.length.toLocaleString('en-IN') : ''} Records
        </button>
      </div>
    </Modal>
  );
}
