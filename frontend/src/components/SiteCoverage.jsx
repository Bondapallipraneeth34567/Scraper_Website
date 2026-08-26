import { SITE_TOTALS } from '../data/siteTotals';

/** True site-wide taxonomy totals vs. what's actually landed in the DB so
 * far — the DB-observed counts alone understate reality, since they only
 * ever reflect what's been scraped, not what exists on agmarknet.gov.in. */
export default function SiteCoverage({ overview }) {
  const rows = [
    { label: 'States', dbCount: overview.states, siteTotal: SITE_TOTALS.states },
    { label: 'Districts', dbCount: overview.districts, siteTotal: SITE_TOTALS.districts },
    { label: 'Commodity Groups', dbCount: null, siteTotal: SITE_TOTALS.commodityGroups },
    { label: 'Commodities', dbCount: overview.commodities, siteTotal: SITE_TOTALS.commodities },
    { label: 'Market Yards', dbCount: overview.markets, siteTotal: SITE_TOTALS.markets },
  ];

  return (
    <div className="rounded-card border border-line bg-surface p-4 shadow-card">
      <div className="mb-0.5 text-sm font-semibold text-ink">Site Coverage</div>
      <div className="mb-4 text-xs text-ink-soft">
        DB-observed counts against the true agmarknet.gov.in taxonomy — manually confirmed on{' '}
        {SITE_TOTALS.checkedOn}. Commodity Groups has no column in dbo.MarketPrices, so it's shown as a
        reference total only.
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
    </div>
  );
}
