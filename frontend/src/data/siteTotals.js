// True Agmarknet site-wide taxonomy totals — these can't be derived from
// dbo.MarketPrices (the DB only ever contains what's been scraped so far,
// and the site's dropdown counts aren't exposed through any API). Manually
// confirmed against agmarknet.gov.in on 2026-08-20 and recorded here so the
// dashboard can show real coverage instead of just "how much DB has".
//
// Update these by hand if re-checked against the live site later.
export const SITE_TOTALS = {
  states: 36,
  districts: 600,
  commodityGroups: 16,
  commodities: 604,
  markets: 3800,
  checkedOn: '2026-08-20',
};
