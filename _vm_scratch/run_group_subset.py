"""Runs scrape_all() scoped to a specific set of commodity groups instead
of all 16 -- monkeypatches load_commodity_reference() to return only the
chosen groups before calling scrape_all(), so every other piece of logic
(per-commodity flush, captcha handling, the failure-recovery path) runs
completely unmodified, exactly as production would use it."""
import sys

sys.path.insert(0, "/home/azureuser/scraper_csv")
import agmarknet_scraper as scraper  # noqa: E402

GROUPS = ["Beverages", "Cereals", "Fibre Crops", "Oils and Fats", "Dry Fruits"]

_full_reference = scraper.load_commodity_reference()
_subset_reference = {g: _full_reference[g] for g in GROUPS if g in _full_reference}
total_commodities = sum(len(v) for v in _subset_reference.values())
print(f"Scoped to {len(_subset_reference)} groups, {total_commodities} commodities: {list(_subset_reference.keys())}")

scraper.load_commodity_reference = lambda: _subset_reference

if __name__ == "__main__":
    scraped = scraper.scrape_all(resume="--resume" in sys.argv)
    print(f"✅ run complete — {len(scraped)} entries scraped this run")
