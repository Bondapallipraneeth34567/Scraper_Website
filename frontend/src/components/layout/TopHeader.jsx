import { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Search, RefreshCw, Bell, CalendarDays, LogOut } from 'lucide-react';
import toast from 'react-hot-toast';
import { useAuth } from '../../auth/AuthProvider';
import { getIssues, subscribe } from '../../services/verificationService';
import { formatDate } from '../../utils/format';

export default function TopHeader({ title, subtitle, onRefresh }) {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const [search, setSearch] = useState('');
  const [recentIssues, setRecentIssues] = useState([]);
  const [notifOpen, setNotifOpen] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const popoverRef = useRef(null);

  useEffect(() => {
    const load = () => getIssues().then((all) => setRecentIssues(all.slice(0, 5)));
    load();
    return subscribe(load);
  }, []);

  useEffect(() => {
    function handleClickOutside(e) {
      if (popoverRef.current && !popoverRef.current.contains(e.target)) setNotifOpen(false);
    }
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  function submitSearch(e) {
    e.preventDefault();
    if (search.trim()) navigate(`/verification?search=${encodeURIComponent(search.trim())}`);
  }

  async function handleRefresh() {
    setRefreshing(true);
    if (onRefresh) await onRefresh();
    toast.success('Data refreshed');
    setTimeout(() => setRefreshing(false), 400);
  }

  function handleLogout() {
    logout();
    toast.success('Logged out');
    navigate('/login', { replace: true });
  }

  const today = formatDate(new Date().toISOString().slice(0, 10));

  return (
    <header className="sticky top-0 z-10 flex items-center gap-4 border-b border-line bg-surface/95 px-6 py-3.5 backdrop-blur">
      <div className="min-w-0 flex-none">
        <h1 className="truncate text-lg font-semibold text-ink">{title}</h1>
        {subtitle && <p className="truncate text-xs text-ink-soft">{subtitle}</p>}
      </div>

      <form onSubmit={submitSearch} className="ml-2 flex-1">
        <div className="flex max-w-sm items-center gap-2 rounded-lg border border-line bg-canvas px-3 py-1.5">
          <Search size={15} className="text-ink-soft" />
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search records, markets, commodities…"
            className="w-full bg-transparent text-sm text-ink outline-none placeholder:text-ink-soft"
          />
        </div>
      </form>

      <div className="flex flex-none items-center gap-1.5 text-xs text-ink-soft">
        <CalendarDays size={14} />
        <span className="tnum">{today}</span>
      </div>

      <button
        type="button"
        onClick={handleRefresh}
        className="flex flex-none items-center gap-1.5 rounded-lg border border-line px-3 py-1.5 text-xs font-medium text-ink hover:bg-canvas"
      >
        <RefreshCw size={14} className={refreshing ? 'animate-spin' : ''} />
        Refresh
      </button>

      <div className="relative flex-none" ref={popoverRef}>
        <button
          type="button"
          onClick={() => setNotifOpen((v) => !v)}
          className="relative rounded-lg border border-line p-2 text-ink hover:bg-canvas"
          aria-label="Notifications"
        >
          <Bell size={15} />
          {recentIssues.length > 0 && (
            <span className="absolute -right-1 -top-1 flex h-4 w-4 items-center justify-center rounded-full bg-bad text-[9px] font-bold text-white">
              {recentIssues.length > 9 ? '9+' : recentIssues.length}
            </span>
          )}
        </button>
        {notifOpen && (
          <div className="absolute right-0 z-20 mt-2 w-80 rounded-card border border-line bg-surface shadow-card">
            <div className="border-b border-line px-3.5 py-2.5 text-xs font-semibold uppercase tracking-wide text-ink-soft">
              Recent issues
            </div>
            {recentIssues.length === 0 ? (
              <div className="px-3.5 py-4 text-sm text-ink-soft">No issues logged yet.</div>
            ) : (
              <ul className="max-h-72 overflow-y-auto">
                {recentIssues.map((i) => (
                  <li key={i.id} className="border-b border-line-soft px-3.5 py-2.5 text-xs last:border-0">
                    <div className="font-semibold text-ink">
                      #{i.recordId} · {i.record?.market}
                    </div>
                    <div className="text-ink-soft">{i.notes || 'No notes added'}</div>
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
      </div>

      <div className="flex flex-none items-center gap-2 border-l border-line pl-3.5">
        <div className="flex h-8 w-8 items-center justify-center rounded-full bg-good/15 text-xs font-bold text-good">
          {user.name.slice(0, 1)}
        </div>
        <div className="leading-tight">
          <div className="text-xs font-semibold text-ink">{user.name}</div>
          <div className="text-[10px] text-ink-soft">{user.role}</div>
        </div>
        <button
          type="button"
          onClick={handleLogout}
          title="Log out"
          className="rounded-md p-1.5 text-ink-soft hover:bg-canvas hover:text-bad"
        >
          <LogOut size={14} />
        </button>
      </div>
    </header>
  );
}
