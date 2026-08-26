import Sidebar from './Sidebar';
import TopHeader from './TopHeader';

export default function DashboardLayout({ title, subtitle, onRefresh, children }) {
  return (
    <div className="flex min-h-screen bg-canvas">
      <Sidebar />
      <div className="min-w-0 flex-1">
        <TopHeader title={title} subtitle={subtitle} onRefresh={onRefresh} />
        <main className="p-6">{children}</main>
      </div>
    </div>
  );
}
