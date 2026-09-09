"""One-off script: renders a detailed report on the 2026-09-02 Agmarknet
CSV scraper (v2) investigation/fix session as a .docx — every problem
faced, how it was fixed, and the resulting end-to-end flow.

Run: python docs/generate_csv_scraper_report_docx.py
"""

from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Pt, RGBColor, Inches
from docx.oxml.ns import qn

OUT_PATH = Path(__file__).parent / "csv_scraper_investigation_report.docx"

CODE_FONT = "Consolas"
BODY_FONT = "Calibri"
ACCENT = RGBColor(0x1F, 0x5C, 0x3D)  # dark green, agri-themed
DARK_TEXT = RGBColor(0x22, 0x22, 0x22)
WARN = RGBColor(0xA0, 0x3D, 0x1F)


def set_code_block_shading(paragraph):
    pPr = paragraph._p.get_or_add_pPr()
    shd = pPr.makeelement(qn("w:shd"), {
        qn("w:val"): "clear",
        qn("w:color"): "auto",
        qn("w:fill"): "F2F2F2",
    })
    pPr.append(shd)


def add_code_block(doc, text):
    for line in text.rstrip("\n").split("\n"):
        p = doc.add_paragraph()
        p.paragraph_format.space_after = Pt(0)
        p.paragraph_format.space_before = Pt(0)
        set_code_block_shading(p)
        run = p.add_run(line if line.strip() else " ")
        run.font.name = CODE_FONT
        run.font.size = Pt(9.5)
        run.font.color.rgb = DARK_TEXT


def add_body(doc, text):
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(8)
    run = p.add_run(text)
    run.font.name = BODY_FONT
    run.font.size = Pt(10.5)
    return p


def add_bullet(doc, label, rest):
    p = doc.add_paragraph(style="List Bullet")
    p.paragraph_format.space_after = Pt(6)
    if label:
        r1 = p.add_run(label)
        r1.bold = True
        r1.font.name = CODE_FONT
        r1.font.size = Pt(10)
        r1.font.color.rgb = ACCENT
    r2 = p.add_run(rest)
    r2.font.name = BODY_FONT
    r2.font.size = Pt(10.5)
    return p


def add_warning(doc, text):
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(8)
    r1 = p.add_run("⚠ ")
    r1.bold = True
    r1.font.color.rgb = WARN
    r1.font.size = Pt(10.5)
    r2 = p.add_run(text)
    r2.font.name = BODY_FONT
    r2.font.size = Pt(10.5)
    r2.italic = True
    return p


def add_heading(doc, text, level):
    h = doc.add_heading(text, level=level)
    for run in h.runs:
        run.font.color.rgb = ACCENT
        run.font.name = BODY_FONT
    return h


def add_table(doc, headers, rows):
    table = doc.add_table(rows=1, cols=len(headers))
    table.style = "Light Grid Accent 1"
    hdr_cells = table.rows[0].cells
    for i, h in enumerate(headers):
        hdr_cells[i].text = ""
        run = hdr_cells[i].paragraphs[0].add_run(h)
        run.bold = True
        run.font.name = BODY_FONT
        run.font.size = Pt(9.5)
    for row in rows:
        cells = table.add_row().cells
        for i, val in enumerate(row):
            cells[i].text = ""
            run = cells[i].paragraphs[0].add_run(str(val))
            run.font.name = BODY_FONT
            run.font.size = Pt(9.5)
    doc.add_paragraph().paragraph_format.space_after = Pt(4)
    return table


def build():
    doc = Document()

    normal = doc.styles["Normal"]
    normal.font.name = BODY_FONT
    normal.font.size = Pt(10.5)
    normal.font.color.rgb = DARK_TEXT

    for section in doc.sections:
        section.left_margin = Inches(1)
        section.right_margin = Inches(1)

    title = doc.add_heading("Agmarknet CSV Scraper (v2) — Investigation & Fixes", level=0)
    for run in title.runs:
        run.font.color.rgb = ACCENT
    subtitle = doc.add_paragraph()
    subtitle.alignment = WD_ALIGN_PARAGRAPH.LEFT
    sr = subtitle.add_run(
        "Session log: 2026-09-02. Every problem found in the two Agmarknet mandi-price "
        "scrapers (DOM-based “Web_Scraper” and CSV-based “scraper_csv”, "
        "writing to mandi.market_prices and mandi.market_prices_v2 respectively), what "
        "was actually wrong, how each was fixed, and the resulting end-to-end pipeline."
    )
    sr.italic = True
    sr.font.size = Pt(11)
    sr.font.color.rgb = RGBColor(0x55, 0x55, 0x55)

    # ------------------------------------------------------------------
    add_heading(doc, "Background", 1)
    add_body(
        doc,
        "Two independent scrapers were both hitting agmarknet.gov.in's “Daily Price and "
        "Arrival Report” and writing to two separate Postgres tables on the shared "
        "Azure server (sethu-admin.postgres.database.azure.com):"
    )
    add_bullet(doc, "Web_Scraper (“v1”) — ", "reads the on-screen results table row-by-row, clicking “Next page” in a loop. Writes mandi.market_prices. This is what the production dashboard (backend/server.js) read from before this session.")
    add_bullet(doc, "scraper_csv (“v2”) — ", "downloads the site's own “Download CSV” export (every row, every page, in one file) instead of reading the DOM. Writes mandi.market_prices_v2. Built 2026-08-31 as a parallel verification copy, explicitly not meant to replace v1 until a comparison judged it worth it.")
    add_body(
        doc,
        "Both run on an Azure VM (sethu-admin1 / agmarknet-scraper-vm, 2 vCPU / 4GiB), "
        "reachable over Tailscale. Both share the same captcha-solving and form-driving "
        "logic (Playwright + EasyOCR)."
    )

    # ------------------------------------------------------------------
    add_heading(doc, "1. Yesterday's scrape was incomplete — why?", 1)
    add_body(
        doc,
        "First question asked: did the previous day's CSV scrape actually land in "
        "mandi.market_prices_v2? The first check queried the wrong table (mandi.market_prices, "
        "v1) — corrected once it was clear “mandi v2” meant the literal second table, "
        "market_prices_v2."
    )
    add_body(doc, "What the DB showed: v2's data for that day only covered 6 of 16 commodity groups (Beverages through Flowers), stopping abruptly with a ~10h49m gap in writes right after.")
    add_body(
        doc,
        "Initial hypothesis (wrong): the process crashed and something restarted it "
        "hours later, skipping groups. SSH'd into the VM directly to check — the real "
        "picture, from the scraper's own log file (scraper_csv_full_run.log), was different:"
    )
    add_bullet(doc, "", "the process was the SAME one, running continuously the entire time — never crashed, never restarted.")
    add_bullet(doc, "", "it was simply slow: captcha OCR gets the site's 6-character captcha right “well under half the time,” each wrong guess costs a refresh-and-retry cycle (up to 12 attempts), and the site's own loading overlay routinely blocks the “Go” button for 20+ seconds at a time (up to 6 retries).")
    add_bullet(doc, "", "measured real progress: 1→605 commodities took roughly 25–30 hours end to end at this pace — far longer than a single calendar day, so the run's own arrival_date naturally rolled over from one day to the next mid-flight.")
    add_body(doc, "Conclusion: not a bug — the scraper was working the whole time, just slower than a 24-hour day.")

    # ------------------------------------------------------------------
    add_heading(doc, "2. Production scraper (v1) was silently disabled", 1)
    add_body(
        doc,
        "While investigating, found that web-scraper.service (v1, feeding the live "
        "dashboard table) had crashed with a Node.js EPIPE error at 2026-09-01 05:31 UTC "
        "— and in that same instant, its daily systemd timer (web-scraper.timer) had been "
        "manually stopped and disabled. It had not run since, meaning the production "
        "dashboard's data had been stale for roughly a day."
    )
    add_body(doc, "Likely cause: switched off during the same day's active development on the v2 script (many .bak-pre-* files from that day), and never switched back on.")
    add_bullet(doc, "Fix — ", "re-enabled web-scraper.timer (its Persistent=true setting immediately triggered a missed-run catch-up) and added Restart=on-failure / RestartSec=60 to web-scraper.service, so a transient crash like the EPIPE retries automatically instead of waiting a full day for the next scheduled run.")

    # ------------------------------------------------------------------
    add_heading(doc, "3. v2 had zero crash supervision", 1)
    add_body(
        doc,
        "The v2 scraper was running as a bare `nohup ... &` shell process — no systemd, "
        "no restart policy, no monitoring. If it ever died for real, nothing would bring "
        "it back."
    )
    add_bullet(doc, "Fix — ", "installed a proper systemd unit (agmarknet-scraper-v2.service, Restart=always, RestartSec=60, ExecStart=... --resume) and handed off from the bare process to the managed one without losing progress (--resume picks up from the DB's own checkpoint state).")

    # ------------------------------------------------------------------
    add_heading(doc, "4. Commodity coverage bug: 605 vs 586", 1)
    add_body(
        doc,
        "v1's own logs showed “X/586 commodities done” even though the full taxonomy "
        "has 605 real commodities. Root cause, found in v2's own code comments (already "
        "fixed there 2026-09-01): both scrapers used to build their work list as"
    )
    add_code_block(doc, 'targets[crop_keyword_for(commodity)] = (group, commodity)')
    add_body(
        doc,
        "crop_keyword_for() strips everything after “(” before lowercasing — so "
        "Mango and Mango(Raw-Ripe), Paddy(Basmati) and Paddy(Common), all 3 Rose "
        "varieties, all 3 Tube Rose varieties, etc. collapse onto the same dict key. "
        "Whichever commodity got inserted last silently overwrote the other — the "
        "other was never queried, run after run. 19 real commodities were permanently "
        "invisible this way (605 − 19 = 586, exactly matching the logs)."
    )
    add_bullet(doc, "Fix — ", "v2 had already switched to a plain list (no dedup key) on 2026-09-01. Ported the identical fix into v1 (Web_Scraper/agmarknet_scraper.py): targets became a list of (group, commodity) pairs, and the --resume matching (which had the same bug — comparing against the wrong key segment, meaning resume silently never skipped anything) was fixed alongside it.")

    # ------------------------------------------------------------------
    add_heading(doc, "5. Data-quality comparison: which scraper is actually better?", 1)
    add_body(doc, "Ran a direct SQL diff between the two tables for the one day both had data for (2026-09-01), joining on (state, district, market, commodity, variety, grade):")
    add_table(
        doc,
        ["Metric", "v1 (DOM)", "v2 (CSV)"],
        [
            ["Rows that day", "911", "1,831"],
            ["Distinct commodities", "41", "51"],
            ["Rows matched on both sides", "786", "786"],
            ["Price agreement on matched rows", "750 / 786 (95.4%)", "—"],
            ["Price disagreement", "36 / 786 (4.6%)", "—"],
        ],
    )
    add_body(
        doc,
        "On the mismatches, v1's values carried long, unrealistic floating-point tails "
        "(e.g. 21224.72000000000116415321826934814453125) while v2's were always clean "
        "numbers matching the CSV export exactly (e.g. 22200). Read: Agmarknet's own "
        "on-screen table appears to render an aggregated/averaged figure for some rows, "
        "while the CSV export gives the clean, individually-reported value."
    )
    add_body(
        doc,
        "Combined with the structural advantage already known — CSV export returns "
        "every row in one request, while DOM pagination risks losing a whole commodity "
        "partway through a multi-page “Next page” loop — v2 won on both robustness "
        "and accuracy."
    )
    add_bullet(doc, "Decision — ", "retire v1 entirely. Stopped and disabled web-scraper.service / web-scraper.timer for good.")

    # ------------------------------------------------------------------
    add_heading(doc, "6. Dashboard backend was still reading the retired table", 1)
    add_body(
        doc,
        "backend/server.js (the dashboard API) queried mandi.market_prices in every "
        "single endpoint — which had just stopped receiving new data. Repointed every "
        "query (backend/server.js, create_table.js, test_connection.js, db.js) to "
        "mandi.market_prices_v2."
    )
    add_body(doc, "While in there, found three unrelated pre-existing bugs, left over from an earlier Azure SQL → Postgres schema migration that server.js's read side was never updated to match:")
    add_bullet(doc, "", "min_price_quintal / max_price_quintal / modal_price_quintal / arrival_qty were read from every record, but the real columns had been renamed to min_price / max_price / modal_price / arrival_quantity on 2026-08-31. Every record's prices were silently coming back as NaN in the API response.")
    add_bullet(doc, "", "/api/completeness selected COUNT(DISTINCT crop_keyword) — a column dropped from the schema on 2026-08-31. This endpoint errored on every call.")
    add_bullet(doc, "", "SORT_COLUMNS mapped price sorting to the old *_quintal column names — sorting by price would have thrown a SQL error.")
    add_body(doc, "All three fixed alongside the table switch. Verified for real (not just compiled): started the backend, logged in as the seeded admin account, and hit every endpoint — /api/overview, /api/completeness, /api/records (confirmed real numeric prices, not NaN), /api/records/:id, /next-pending, /api/duplicates, /api/issues, and the /verify write path.")

    # ------------------------------------------------------------------
    add_heading(doc, "7. verification_results' foreign key pointed at the wrong table", 1)
    add_body(
        doc,
        "mandi.verification_results (human review notes on individual scraped rows) had "
        "a foreign key straight into market_prices' row IDs. Since those rows would no "
        "longer be browsed, the FK was repointed to market_prices_v2, and (per explicit "
        "instruction) the existing review notes — which only made sense against v1's now-"
        "irrelevant row numbers — were cleared out so reviewers start fresh against v2's "
        "(better) data."
    )

    # ------------------------------------------------------------------
    add_heading(doc, "8. Rebuilding v2's table from the CSV archive (no re-scraping)", 1)
    add_body(
        doc,
        "Discovered the CSV scraper already archives every raw CSV it downloads to "
        "downloaded_csvs/<date>/<commodity>.csv (added 2026-09-01) before parsing it. "
        "This meant the table could be safely wiped and rebuilt from source without "
        "re-hitting the live site at all."
    )
    add_bullet(doc, "Built — ", "rebuild_from_archive.py: TRUNCATEs mandi.market_prices_v2, then re-parses every archived CSV through the scraper's own existing _row_to_result / save_scraped logic (reused, not re-implemented, so it can never drift from what a live scrape does).")
    add_body(doc, "Result: 2,462 rows rebuilt cleanly from 82 archived files, verified against the DB afterward.")

    # ------------------------------------------------------------------
    add_heading(doc, "9. Speed vs. order: the sharding detour", 1)
    add_body(
        doc,
        "At ~24 commodities/hour, a full 605-commodity day takes ~25–30 hours — too "
        "slow. First attempt: split the whole commodity list up front across 2 parallel "
        "OS processes (stride-based, targets[i::2]), each its own browser + OCR reader. "
        "Measured ~2x speedup (~47/hr combined, ~13h projected for a full day)."
    )
    add_warning(
        doc,
        "Problem: this meant two DIFFERENT commodity groups were being scraped at the "
        "same time — e.g. some Cereals rows landing interleaved with some Drug and "
        "Narcotics rows. From the outside this looked like commodities were being "
        "skipped or scraped out of order (“you scraped Cereals, then Drug and Narco, "
        "then back to Cereals”)."
    )
    add_bullet(doc, "Reverted — ", "removed sharding entirely, back to one single, strictly sequential process (group by group, commodity by commodity, exact taxonomy order). Table wiped and restarted clean.")
    add_bullet(doc, "Added — ", "_resolve_target_date(): walks forward from the earliest date already in the table and only ever targets a date that isn't 100% complete — never starts date+1 while date is still incomplete. A _CAMPAIGN_START_DATE anchor (2026-09-01) was added so an empty-table restart resumes the intended backfill date instead of jumping to “today.” Verified directly before each restart (called the function standalone, confirmed the expected date came back) rather than just assumed.")

    # ------------------------------------------------------------------
    add_heading(doc, "10. The compromise: parallel workers WITHIN a group", 1)
    add_body(
        doc,
        "Once strict ordering was back, asked directly: could parallelism return without "
        "the disorder? Design agreed with the user: groups still run strictly in "
        "taxonomy order (never two groups at once), but within one group, 2 concurrent "
        "browser sessions pull commodities off a shared queue — whichever finishes its "
        "captcha first writes first. Only the order WITHIN a group can shuffle; group "
        "boundaries stay exactly as clean as a single-worker run."
    )
    add_bullet(doc, "_scrape_group_parallel() — ", "spins up N worker threads per group, each with its own browser, page, and OCR reader (EasyOCR isn't safe for concurrent inference across threads — each worker gets its own instance rather than sharing one).")
    add_bullet(doc, "scrape_all() — ", "rewritten to iterate groups in taxonomy order, dispatching one group at a time to _scrape_group_parallel, never starting the next group until the current one's queue is fully drained.")
    add_bullet(doc, "Old sharding code removed — ", "the --shard= flag and its stride-slicing logic were deleted outright rather than left dead in the codebase; replaced with --workers=N (default 2).")
    add_body(doc, "Smoke-tested with a small --limit before trusting it to the live service: confirmed two workers correctly processed different commodities within the same group concurrently, with the shared progress counter staying accurate under concurrency (no race conditions). Only then deployed to the real systemd service.")
    add_body(doc, "Estimated time for a full day at this design: ~13 hours — same throughput as the sharded version, without ever interleaving groups.")

    # ------------------------------------------------------------------
    add_heading(doc, "11. Why is it still slow? (live evidence, not just theory)", 1)
    add_body(
        doc,
        "Captured directly from the live service's own logs: a single commodity attempt "
        "stuck for over 3 minutes straight because Agmarknet's own loading overlay "
        "wouldn't clear — 8+ consecutive “Go click blocked by overlay” retries, "
        "roughly 15–25 seconds apart, before the code gave up, reloaded the form, and "
        "tried again. This is the live government site being slow/flaky on its own end, "
        "not scraper inefficiency — there is nothing in the code that could make this "
        "faster short of the site itself responding faster."
    )

    # ------------------------------------------------------------------
    add_heading(doc, "12. CSV → DB pipeline, explained function by function", 1)
    add_body(doc, "Walked through exactly what happens after a CSV downloads, since it wasn't obvious where Playwright's role ends and the DB write begins:")
    add_bullet(doc, "_download_and_parse_csv() — ", "Playwright triggers the download and saves the raw file to disk (download.save_as(archive_path)). Playwright's involvement ends here.")
    add_bullet(doc, "", "Plain Python (the csv stdlib module) then re-opens that same file from disk and parses it into row dicts — a completely separate step, no Playwright involved.")
    add_bullet(doc, "_row_to_result() — ", "converts one raw CSV row dict into the DB's column shape (parses Min/Max/Modal Price to floats, etc).")
    add_bullet(doc, "_flush_to_db() — ", "runs save_scraped() in a dedicated thread (needed because Playwright's sync API and asyncio can't share a thread).")
    add_bullet(doc, "save_scraped() + _UPSERT_SQL — ", "the actual DB write — INSERT ... ON CONFLICT (state, district, market, commodity, variety, grade, arrival_date) DO UPDATE, via asyncpg. A rescrape of the same row updates it in place; anything new inserts.")
    add_body(doc, "Confirmed: there is no way to skip the local-disk step even in principle — a browser “download” is inherently written to disk by the browser itself; Playwright's Download object only exposes .path() or .save_as(), never raw in-memory bytes. It also isn't a performance issue — reading a small local CSV back takes single-digit milliseconds, negligible next to the multi-second-to-minutes captcha/overlay waits that actually dominate runtime.")

    # ------------------------------------------------------------------
    add_heading(doc, "13. Azure Blob Storage integration", 1)
    add_body(
        doc,
        "User supplied a real Azure Storage connection string and container "
        "(mandi-prices-csv) to mirror the CSV archive off the VM, organized the same "
        "<date>/<commodity>.csv way."
    )
    add_bullet(doc, "Credential handling — ", "written directly into the VM's .env (AZURE_STORAGE_CONNECTION_STRING, AZURE_STORAGE_CONTAINER), never hardcoded into any script or logged.")
    add_bullet(doc, "_upload_to_blob() — ", "added right after the local save in _download_and_parse_csv(). Best-effort: a failed upload logs a warning and does not crash the scrape — a network blip shouldn't cost a whole commodity's DB write, which already succeeded independently.")
    add_bullet(doc, "Verified before trusting it — ", "tested connectivity with a throwaway blob first, then called the real upload function directly against an already-archived CSV and confirmed it appeared in the container, before wiring it into the live loop and before backfilling.")
    add_bullet(doc, "Backfilled — ", "all 22 CSVs already scraped that day were uploaded to the container, confirmed present by listing it back afterward.")

    # ------------------------------------------------------------------
    add_heading(doc, "14. Local disk cleanup once the blob has a confirmed copy", 1)
    add_body(
        doc,
        "Once every CSV is durably mirrored to Blob Storage, keeping a permanent local "
        "copy on the VM's disk is redundant. _upload_to_blob() was changed to return "
        "True/False instead of silently swallowing the result, and the local archive "
        "file is deleted immediately after parsing — but ONLY when the upload is "
        "confirmed successful. If a blob upload ever fails, the local copy is kept as "
        "the sole remaining safety net rather than deleted regardless."
    )
    add_body(doc, "Verified against a real file (upload → confirmed True → deleted → confirmed gone) before deploying, then cleaned up the 21 remaining already-mirrored local files as a one-time catch-up.")

    # ------------------------------------------------------------------
    add_heading(doc, "End-to-end flow (current state)", 1)
    add_code_block(
        doc,
        "agmarknet.gov.in (live site)\n"
        "        |\n"
        "        |  Playwright: pick Commodity Group -> Commodity -> All States/UTs\n"
        "        |  solve captcha (own OCR reader per worker) -> click Go\n"
        "        v\n"
        "  \"Download CSV\" -> browser writes file to local disk\n"
        "        |\n"
        "        |  download.save_as(downloaded_csvs/<date>/<commodity>.csv)\n"
        "        v\n"
        "  Python csv.reader() re-opens that same file, parses rows\n"
        "        |\n"
        "        +----------------------------+\n"
        "        v                            v\n"
        "  _upload_to_blob()            _row_to_result() per row\n"
        "  -> Azure Blob container            |\n"
        "  mandi-prices-csv                   v\n"
        "  <date>/<commodity>.csv       save_scraped() / _UPSERT_SQL (asyncpg)\n"
        "        |                            |\n"
        "        v                            v\n"
        "  if upload OK:                mandi.market_prices_v2 (Postgres)\n"
        "  delete local copy                  |\n"
        "  (else: keep as fallback)            v\n"
        "                              backend/server.js API\n"
        "                                      |\n"
        "                                      v\n"
        "                              dashboard (verification UI)",
    )
    add_body(
        doc,
        "Orchestration: scrape_all() walks commodity groups strictly in taxonomy order "
        "(Beverages → Cereals → … → Others), never starting a new group until the "
        "previous one's full commodity list is done. Within each group, "
        "_scrape_group_parallel() runs 2 concurrent browser sessions pulling commodities "
        "off a shared queue — order WITHIN a group can vary by completion time, group "
        "order never does. _resolve_target_date() gates which calendar date is active "
        "the same way: never touches date+1 while date is still incomplete. The whole "
        "process runs under systemd (agmarknet-scraper-v2.service, Restart=always), so "
        "a real crash recovers automatically and --resume picks up exactly where it left "
        "off, per commodity, per date."
    )

    doc.save(OUT_PATH)
    print(f"Wrote {OUT_PATH}")


if __name__ == "__main__":
    build()
