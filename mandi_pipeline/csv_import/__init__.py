"""Method: bulk CSV file import + reconciliation.

Loads price CSVs you download yourself (from the data.gov.in portal, the
AGMARKNET site, or anywhere) straight into a target table, then verifies
every CSV row against the DB. No API calls, no key, no 10k-row window --
just files on disk.

  ingest.py   python -m mandi_pipeline.csv_import.ingest PATH [PATH ...]

Kept separate from agmarknet/ (direct-API scrape) and datagovin/ (API
pull) because it is a distinct method: file-in, no live source.
"""
