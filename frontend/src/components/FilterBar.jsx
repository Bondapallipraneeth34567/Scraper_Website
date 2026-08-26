import { useEffect, useState } from 'react';
import { Check, ChevronDown, Search, X } from 'lucide-react';
import { getStates, getDistricts, getMarkets, getCommodities } from '../services/verificationService';
import { STATUS_META } from './StatusBadge';

const TAXONOMY_TOTALS = {
  states: 36,
  districts: 600,
  markets: 3800,
  commodities: 608,
};

const FILTER_STATUSES = ['missing', 'duplicate'];

function SearchableDropdown({ value = 'All', options, placeholder, onChange, minWidth = '150px' }) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const selectedValue = value || 'All';
  const displayValue = selectedValue;
  const visibleOptions = options.filter((option) =>
    option.toLowerCase().includes(query.trim().toLowerCase())
  );

  function closeDropdown() {
    setOpen(false);
    setQuery('');
  }

  function selectOption(option) {
    onChange(option);
    closeDropdown();
  }

  return (
    <div className="relative" style={{ minWidth }}>
      <Search size={13} className="pointer-events-none absolute left-2.5 top-1/2 z-10 -translate-y-1/2 text-ink-soft" />
      <input
        type="text"
        value={open ? query : displayValue}
        placeholder={placeholder}
        onFocus={() => {
          setOpen(true);
          setQuery('');
        }}
        onChange={(e) => {
          setOpen(true);
          setQuery(e.target.value);
        }}
        onBlur={() => setTimeout(closeDropdown, 150)}
        className="w-full rounded-lg border border-line bg-surface py-1.5 pl-7 pr-7 text-xs text-ink outline-none focus:border-good"
        aria-label={placeholder}
      />
      <ChevronDown
        size={13}
        className={`pointer-events-none absolute right-2 top-1/2 -translate-y-1/2 text-ink-soft transition-transform ${open ? 'rotate-180' : ''}`}
      />
      {open && (
        <div className="absolute left-0 top-full z-30 mt-1 max-h-56 w-full min-w-[220px] overflow-y-auto rounded-lg border border-line bg-surface py-1 shadow-card">
          <button
            type="button"
            onMouseDown={(e) => {
              e.preventDefault();
              selectOption('All');
            }}
            className={`flex w-full items-center justify-between px-3 py-1.5 text-left text-xs text-ink hover:bg-canvas ${selectedValue === 'All' ? 'bg-canvas font-semibold' : ''}`}
          >
            All
            {selectedValue === 'All' && <Check size={13} className="text-good" />}
          </button>
          {visibleOptions.map((option) => (
            <button
              type="button"
              key={option}
              onMouseDown={(e) => {
                e.preventDefault();
                selectOption(option);
              }}
              className={`flex w-full items-center justify-between truncate px-3 py-1.5 text-left text-xs text-ink hover:bg-canvas ${selectedValue === option ? 'bg-canvas font-semibold' : ''}`}
            >
              <span className="truncate">{option}</span>
              {selectedValue === option && <Check size={13} className="ml-2 flex-none text-good" />}
            </button>
          ))}
          {visibleOptions.length === 0 && (
            <div className="px-3 py-2 text-xs text-ink-soft">No matches found</div>
          )}
        </div>
      )}
    </div>
  );
}

/** Filter row for the Data Verification table — State/District/Market
 * cascade off the live dbo.MarketPrices taxonomy via the API (not a
 * static snapshot), so counts and options grow automatically as more
 * scraping lands. */
export default function FilterBar({ filters, onChange, hideStatus = false }) {
  const [draft, setDraft] = useState(filters);
  useEffect(() => setDraft(filters), [filters]);

  const [states, setStates] = useState([]);
  const [districts, setDistricts] = useState([]);
  const [markets, setMarkets] = useState([]);
  const [commodities, setCommodities] = useState([]);

  useEffect(() => {
    getStates().then(setStates);
    getCommodities().then(setCommodities);
  }, []);

  // District options depend on the selected state; refetch on change.
  useEffect(() => {
    getDistricts(draft.state).then(setDistricts);
  }, [draft.state]);

  // Market options depend on state + district; refetch on either change.
  useEffect(() => {
    getMarkets(draft.state, draft.district).then(setMarkets);
  }, [draft.state, draft.district]);

  function setField(key, value) {
    setDraft((d) => {
      const next = { ...d, [key]: value };
      // Cascading reset: a stale child selection outside the new parent's
      // range would otherwise silently filter to nothing.
      if (key === 'state') {
        next.district = 'All';
        next.market = 'All';
      } else if (key === 'district') {
        next.market = 'All';
      }
      return next;
    });
  }

  return (
    <div className="mb-5 flex flex-wrap items-end gap-3 rounded-card border border-line bg-surface p-3.5 shadow-card">
      {draft.search && (
        <div className="flex w-full items-center gap-2 rounded-lg border border-good/40 bg-good/10 px-3 py-1.5 text-xs text-ink">
          <span className="font-semibold uppercase tracking-wide text-ink-soft">Search:</span>
          <span className="font-medium">"{draft.search}"</span>
          <span className="text-ink-soft">— narrowing every match below to rows containing this text too</span>
          <button
            type="button"
            onClick={() => {
              const next = { ...draft, search: '' };
              setDraft(next);
              onChange(next);
            }}
            className="ml-auto inline-flex items-center gap-1 rounded-md border border-line bg-surface px-2 py-1 font-semibold text-ink hover:bg-canvas"
          >
            <X size={12} /> Clear search
          </button>
        </div>
      )}

      <div className="flex flex-col gap-1">
        <label className="text-[10px] font-semibold uppercase tracking-wide text-ink-soft">Date</label>
        <div className="relative">
          <Search size={13} className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-ink-soft" />
          <input
            type="date"
            value={draft.date || ''}
            onChange={(e) => setField('date', e.target.value)}
            className="rounded-lg border border-line py-1.5 pl-7 pr-2.5 text-xs text-ink"
          />
        </div>
      </div>

      <div className="flex flex-col gap-1">
        <label className="text-[10px] font-semibold uppercase tracking-wide text-ink-soft">
          State <span className="text-ink-soft">({TAXONOMY_TOTALS.states})</span>
        </label>
        <SearchableDropdown value={draft.state} options={states} placeholder="State" onChange={(value) => setField('state', value)} />
      </div>

      <div className="flex flex-col gap-1">
        <label className="text-[10px] font-semibold uppercase tracking-wide text-ink-soft">
          District <span className="text-ink-soft">({TAXONOMY_TOTALS.districts.toLocaleString('en-IN')})</span>
        </label>
        <SearchableDropdown value={draft.district} options={districts} placeholder="District" onChange={(value) => setField('district', value)} />
      </div>

      <div className="flex flex-col gap-1">
        <label className="text-[10px] font-semibold uppercase tracking-wide text-ink-soft">
          Market <span className="text-ink-soft">({TAXONOMY_TOTALS.markets.toLocaleString('en-IN')})</span>
        </label>
        <SearchableDropdown value={draft.market} options={markets} placeholder="Market" onChange={(value) => setField('market', value)} />
      </div>

      <div className="flex flex-col gap-1">
        <label className="text-[10px] font-semibold uppercase tracking-wide text-ink-soft">
          Commodity <span className="text-ink-soft">({TAXONOMY_TOTALS.commodities})</span>
        </label>
        <SearchableDropdown value={draft.commodity} options={commodities} placeholder="Commodity" onChange={(value) => setField('commodity', value)} />
      </div>

      {!hideStatus && (
        <div className="flex flex-col gap-1">
          <label className="text-[10px] font-semibold uppercase tracking-wide text-ink-soft">Status</label>
          <SearchableDropdown
            value={draft.status && draft.status !== 'All' ? STATUS_META[draft.status]?.label : 'All'}
            options={FILTER_STATUSES.map((value) => STATUS_META[value].label)}
            placeholder="Status"
            minWidth="130px"
            onChange={(label) => {
              const status = FILTER_STATUSES.find((value) => STATUS_META[value].label === label) || 'All';
              setField('status', status);
            }}
          />
        </div>
      )}

      <div className="ml-auto flex gap-2">
        <button
          type="button"
          onClick={() => {
            setDraft({});
            onChange({});
          }}
          className="rounded-lg border border-line px-3.5 py-1.5 text-xs font-medium text-ink-soft hover:bg-canvas"
        >
          Reset
        </button>
        <button
          type="button"
          onClick={() => onChange(draft)}
          className="rounded-lg bg-good px-3.5 py-1.5 text-xs font-semibold text-white hover:bg-green-600"
        >
          Apply Filters
        </button>
      </div>
    </div>
  );
}
