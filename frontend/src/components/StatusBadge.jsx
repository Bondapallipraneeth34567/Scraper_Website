import { CheckCircle2, Clock, XCircle, AlertTriangle, Copy, HelpCircle } from 'lucide-react';

export const STATUS_META = {
  pending: { label: 'Pending', icon: Clock, className: 'bg-amber-50 text-amber-700 ring-amber-200' },
  verified: { label: 'Verified', icon: CheckCircle2, className: 'bg-green-50 text-green-700 ring-green-200' },
  mismatch: { label: 'Mismatch', icon: XCircle, className: 'bg-red-50 text-red-700 ring-red-200' },
  missing: { label: 'Missing', icon: AlertTriangle, className: 'bg-orange-50 text-orange-700 ring-orange-200' },
  duplicate: { label: 'Duplicate', icon: Copy, className: 'bg-purple-50 text-purple-700 ring-purple-200' },
  unable: { label: 'Unable to Verify', icon: HelpCircle, className: 'bg-gray-100 text-gray-600 ring-gray-200' },
};

export default function StatusBadge({ status, className = '' }) {
  const meta = STATUS_META[status] || STATUS_META.pending;
  const Icon = meta.icon;
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-semibold ring-1 ring-inset ${meta.className} ${className}`}
    >
      <Icon size={12} strokeWidth={2.5} />
      {meta.label}
    </span>
  );
}
