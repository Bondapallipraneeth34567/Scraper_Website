import { NavLink } from 'react-router-dom';
import {
  LayoutDashboard, ListChecks, Copy, FileWarning, Settings, ClipboardCheck, GitCompareArrows,
} from 'lucide-react';

const NAV_ITEMS = [
  { to: '/dashboard', label: 'Dashboard', icon: LayoutDashboard },
  { to: '/verification', label: 'Data Verification', icon: ListChecks },
  { to: '/comparison', label: 'Site Comparison', icon: GitCompareArrows },
  { to: '/duplicates', label: 'Duplicates', icon: Copy },
  { to: '/missing', label: 'Missing Data', icon: FileWarning },
  { to: '/settings', label: 'Settings', icon: Settings },
];

export default function Sidebar() {
  return (
    <aside className="sticky top-0 flex h-screen w-60 flex-none flex-col border-r border-slate-800 bg-slate-900 text-slate-300">
      <div className="flex items-center gap-2.5 border-b border-slate-800 px-4 py-4">
        <div className="flex h-8 w-8 flex-none items-center justify-center rounded-md bg-slate-800">
          <ClipboardCheck size={17} className="text-good" />
        </div>
        <div>
          <div className="text-[15px] font-semibold leading-tight text-white">Verify QA</div>
          <div className="text-[10px] uppercase tracking-wide text-slate-500">Scraped data validation</div>
        </div>
      </div>

      <nav className="flex-1 overflow-y-auto px-2.5 py-3">
        {NAV_ITEMS.map(({ to, label, icon: Icon }) => (
          <NavLink
            key={to}
            to={to}
            className={({ isActive }) =>
              `mb-0.5 flex items-center gap-2.5 rounded-lg px-2.5 py-2 text-[13px] transition ${
                isActive
                  ? 'bg-slate-800 font-semibold text-white'
                  : 'text-slate-300 hover:bg-slate-800/60'
              }`
            }
          >
            <Icon size={16} strokeWidth={2} />
            {label}
          </NavLink>
        ))}
      </nav>

      <div className="border-t border-slate-800 px-4 py-3 text-[11px] text-slate-500">
        Live — connected to Azure SQL.
      </div>
    </aside>
  );
}
