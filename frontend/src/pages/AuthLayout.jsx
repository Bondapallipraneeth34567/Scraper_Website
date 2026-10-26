import { ClipboardCheck } from 'lucide-react';

export default function AuthLayout({ title, subtitle, children }) {
  return (
    <div className="flex min-h-screen items-center justify-center bg-canvas px-4">
      <div className="w-full max-w-sm">
        <div className="mb-6 flex flex-col items-center gap-2 text-center">
          <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-slate-900">
            <ClipboardCheck size={20} className="text-good" />
          </div>
          <div className="text-lg font-semibold text-ink"> Mandi Prices Dashboard</div>
          <div className="text-xs text-ink-soft">Scraped data verification</div>
        </div>

        <div className="rounded-card border border-line bg-surface p-6 shadow-card">
          <h1 className="mb-1 text-base font-semibold text-ink">{title}</h1>
          {subtitle && <p className="mb-5 text-xs text-ink-soft">{subtitle}</p>}
          {children}
        </div>
      </div>
    </div>
  );
}
