#!/usr/bin/env bash
# Pulls the v2 scraper's raw CSV archive from the VM into
# downloads/agmarknet_daily_prices/<date>/<commodity>.csv locally, so a
# row in mandi.market_prices_v2 can be checked against the exact CSV file
# Agmarknet returned for that commodity/date, without SSHing into the VM
# each time. Re-run anytime to pick up newly-scraped commodities — scp -r
# only transfers files that don't already exist locally with the same
# name (a rescrape overwrites the same filename on the VM, so re-running
# this after a rescrape does re-pull the changed file too, just not
# incrementally — it's a full copy each time, cheap since the archive is
# only a few hundred KB).
set -euo pipefail

KEY="$(dirname "$0")/../sethu-admin1_key (1).pem"
DEST="$(dirname "$0")/../downloads/agmarknet_daily_prices"

mkdir -p "$DEST"
scp -r -i "$KEY" azureuser@100.98.24.27:/home/azureuser/scraper_csv/downloaded_csvs/* "$DEST/"
echo "synced $(find "$DEST" -type f | wc -l) CSV files into $DEST"
