const ACCENTS = {
  slate: 'border-l-slate-400 text-slate-700',
  green: 'border-l-good text-green-700',
  amber: 'border-l-warn text-amber-700',
  red: 'border-l-bad text-red-700',
  orange: 'border-l-orange-400 text-orange-700',
  purple: 'border-l-purple-400 text-purple-700',
};

export default function SummaryCard({ label, value, accent = 'slate', icon: Icon, active, onClick }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`flex w-full flex-col gap-1 rounded-card border border-line border-l-4 bg-surface px-4 py-3.5 text-left shadow-card transition
        ${ACCENTS[accent]} ${onClick ? 'cursor-pointer hover:-translate-y-0.5 hover:shadow-md' : 'cursor-default'}
        ${active ? 'ring-2 ring-offset-1 ring-good' : ''}`}
    >
      <div className="flex items-center justify-between">
        <span className="text-[11px] font-semibold uppercase tracking-wide text-ink-soft">{label}</span>
        {Icon && <Icon size={15} className="opacity-60" />}
      </div>
      <span className="tnum text-2xl font-bold text-ink">{value}</span>
    </button>
  );
}
