import { useEffect, useMemo, useState } from 'react';
import { ChevronDown, ChevronRight } from 'lucide-react';
import { SITE_TOTALS } from '../data/siteTotals';
import { getCommodityCoverage } from '../services/verificationService';

/** True site-wide taxonomy totals vs. what's actually landed in the DB so
 * far — the DB-observed counts alone understate reality, since they only
 * ever reflect what's been scraped, not what exists on agmarknet.gov.in.
 * Commodities are cross-referenced against the harvested
 * agmarknet_commodities.json taxonomy (see backend's /api/filters/
 * commodity-coverage) so the specific gaps are visible, not just a count. */
export default function SiteCoverage({ overview }) {
  const [coverage, setCoverage] = useState(null);
  const [openGroup, setOpenGroup] = useState(null);

  useEffect(() => {
    getCommodityCoverage().then(setCoverage);
  }, []);

  const { missingTotal, groupsWithGaps } = useMemo(() => {
    if (!coverage) return { missingTotal: 0, groupsWithGaps: [] };
    return {
      missingTotal: coverage.reduce((sum, g) => sum + g.missing.length, 0),
      groupsWithGaps: coverage.filter((g) => g.missing.length > 0),
    };
  }, [coverage]);

  const rows = [
    { label: 'States', dbCount: overview.states, siteTotal: SITE_TOTALS.states },
    { label: 'Districts', dbCount: overview.districts, siteTotal: SITE_TOTALS.districts },
    // No commodity_group column on dbo.MarketPrices, and "coverage" for a
    // group isn't a single number the way it is for the other rows — a
    // group with 1 of 40 commodities scraped isn't meaningfully "covered".
    // The per-group breakdown below is the real measure; this row stays a
    // reference total only, same as before groups had any coverage data.
    { label: 'Commodity Groups', dbCount: null, siteTotal: SITE_TOTALS.commodityGroups },
    { label: 'Commodities', dbCount: overview.commodities, siteTotal: SITE_TOTALS.commodities },
    { label: 'Market Yards', dbCount: overview.markets, siteTotal: SITE_TOTALS.markets },
  ];

  return (
    <div className="rounded-card border border-line bg-surface p-4 shadow-card">
      <div className="mb-0.5 text-sm font-semibold text-ink">Site Coverage</div>
      <div className="mb-4 text-xs text-ink-soft">
        DB-observed counts against the true agmarknet.gov.in taxonomy — manually confirmed on{' '}
        {SITE_TOTALS.checkedOn}. Commodity Groups has no column in dbo.MarketPrices, so it's shown as a reference
        total only — see the per-group breakdown below for real coverage.
      </div>
      <div className="flex flex-col gap-3">
        {rows.map((r) => {
          const pct = r.dbCount === null ? null : Math.min(100, Math.round((r.dbCount / r.siteTotal) * 100));
          return (
            <div key={r.label}>
              <div className="mb-1 flex items-baseline justify-between text-xs">
                <span className="font-semibold text-ink">{r.label}</span>
                <span className="tnum text-ink-soft">
                  {r.dbCount === null ? (
                    <span className="text-ink-soft">— · {r.siteTotal.toLocaleString('en-IN')} on site</span>
                  ) : (
                    <>
                      <b className="text-ink">{r.dbCount.toLocaleString('en-IN')}</b> in DB /{' '}
                      {r.siteTotal.toLocaleString('en-IN')} on site
                      <span className="ml-1.5 font-semibold text-good">({pct}%)</span>
                    </>
                  )}
                </span>
              </div>
              {r.dbCount !== null && (
                <div className="h-1.5 w-full overflow-hidden rounded-full bg-canvas">
                  <div className="h-full rounded-full bg-good" style={{ width: `${pct}%` }} />
                </div>
              )}
            </div>
          );
        })}
      </div>

      {coverage && (
        <div className="mt-4 border-t border-line pt-3">
          <div className="mb-2 text-xs font-semibold text-ink">
            Commodities not yet in the DB, by group ({missingTotal} of {SITE_TOTALS.commodities})
          </div>
          <div className="flex flex-col gap-0.5">
            {groupsWithGaps.map((g) => {
              const isOpen = openGroup === g.group;
              return (
                <div key={g.group}>
                  <button
                    type="button"
                    onClick={() => setOpenGroup(isOpen ? null : g.group)}
                    className="flex w-full items-center gap-1.5 rounded px-1 py-1 text-left text-xs text-ink-soft hover:bg-canvas hover:text-ink"
                  >
                    {isOpen ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
                    <span className="font-medium text-ink">{g.group}</span>
                    <span>
                      — {g.scraped}/{g.total} scraped
                    </span>
                  </button>
                  {isOpen && (
                    <div className="ml-5 mb-1 flex flex-wrap gap-1.5">
                      {g.missing.map((c) => (
                        <span key={c} className="rounded-md border border-line bg-canvas px-1.5 py-0.5 text-[11px] text-ink-soft">
                          {c}
                        </span>
                      ))}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
