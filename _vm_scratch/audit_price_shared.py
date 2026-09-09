"""Constants shared between src.services.mandi_service (reads dbo.MarketPrices,
drives the live conversation) and src.services.agmarknet_scraper (writes it, on a
schedule and on demand).

Ported from the working PIPECAT-AGENT-V1 project's
bot_processors/pricing/price_shared.py — same split, same reasoning: avoids a
circular import between the scraper and the price-lookup service.
"""

_LAST_KNOWN_KEY_SEP = "||"


# Shared commodity-name -> lookup-key normalization. Both agmarknet_scraper
# (writing a scraped commodity's key) and mandi_service (resolving a caller's
# spoken commodity to the same key) must derive identical keys from identical
# commodity names, or a freshly scraped row would silently never be found by
# a live call's lookup — kept here as the one definition both sides import
# instead of risking two copies drifting apart.
def normalize_commodity_name(name: str) -> str:
    return name.split("(")[0].strip().lower()


def crop_keyword_for(name: str) -> str:
    return normalize_commodity_name(name).replace(" ", "_").replace("/", "_")
