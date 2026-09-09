"""Renders the Approach 1 (AGMARKNET direct-API pipeline) report as a .docx:
objective, architecture, the end-to-end flow, verified API facts, CAPTCHA
handling, data model, CSV + blob, incremental logic, scheduling, the
problems hit while building it and how each was fixed, live-run evidence,
limitations, and the primary/fallback recommendation.

Run:  python docs/generate_approach1_report_docx.py
Out:  docs/approach1_agmarknet_api_pipeline.docx
"""
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor

OUT_PATH = Path(__file__).parent / "approach1_agmarknet_api_pipeline.docx"

BODY_FONT = "Calibri"
CODE_FONT = "Consolas"
ACCENT = RGBColor(0x1F, 0x5C, 0x3D)
DARK = RGBColor(0x22, 0x22, 0x22)
WARN = RGBColor(0xA0, 0x3D, 0x1F)
GOOD = RGBColor(0x1F, 0x5C, 0x3D)
MUTED = RGBColor(0x55, 0x55, 0x55)


def _shade(p, fill):
    pPr = p._p.get_or_add_pPr()
    pPr.append(pPr.makeelement(qn("w:shd"), {qn("w:val"): "clear", qn("w:color"): "auto", qn("w:fill"): fill}))


def code(doc, text):
    for line in text.rstrip("\n").split("\n"):
        p = doc.add_paragraph()
        p.paragraph_format.space_after = Pt(0)
        p.paragraph_format.space_before = Pt(0)
        _shade(p, "F2F2F2")
        r = p.add_run(line if line.strip() else " ")
        r.font.name = CODE_FONT
        r.font.size = Pt(9)
        r.font.color.rgb = DARK


def body(doc, text, size=10.5, italic=False, after=8):
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(after)
    r = p.add_run(text)
    r.font.name = BODY_FONT
    r.font.size = Pt(size)
    r.italic = italic
    return p


def bullet(doc, label, rest=""):
    p = doc.add_paragraph(style="List Bullet")
    p.paragraph_format.space_after = Pt(4)
    if label:
        r = p.add_run(label)
        r.bold = True
        r.font.name = BODY_FONT
        r.font.size = Pt(10.5)
        r.font.color.rgb = ACCENT
    r2 = p.add_run(rest)
    r2.font.name = BODY_FONT
    r2.font.size = Pt(10.5)
    return p


def num(doc, text):
    p = doc.add_paragraph(style="List Number")
    p.paragraph_format.space_after = Pt(4)
    r = p.add_run(text)
    r.font.name = BODY_FONT
    r.font.size = Pt(10.5)


def heading(doc, text, level):
    h = doc.add_heading(text, level=level)
    for r in h.runs:
        r.font.color.rgb = ACCENT
        r.font.name = BODY_FONT
    return h


def callout(doc, prefix, text, color):
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(8)
    p.paragraph_format.space_before = Pt(4)
    _shade(p, "F4F6F4")
    r1 = p.add_run(prefix + "  ")
    r1.bold = True
    r1.font.color.rgb = color
    r1.font.size = Pt(10.5)
    r2 = p.add_run(text)
    r2.font.name = BODY_FONT
    r2.font.size = Pt(10.5)


def table(doc, headers, rows, widths=None):
    t = doc.add_table(rows=1, cols=len(headers))
    t.style = "Light Grid Accent 1"
    for i, h in enumerate(headers):
        c = t.rows[0].cells[i]
        c.text = ""
        r = c.paragraphs[0].add_run(h)
        r.bold = True
        r.font.name = BODY_FONT
        r.font.size = Pt(9.5)
    for row in rows:
        cells = t.add_row().cells
        for i, v in enumerate(row):
            cells[i].text = ""
            r = cells[i].paragraphs[0].add_run(str(v))
            r.font.name = BODY_FONT
            r.font.size = Pt(9.5)
    if widths:
        for row in t.rows:
            for i, w in enumerate(widths):
                row.cells[i].width = Inches(w)
    doc.add_paragraph().paragraph_format.space_after = Pt(4)
    return t


def build():
    doc = Document()
    normal = doc.styles["Normal"]
    normal.font.name = BODY_FONT
    normal.font.size = Pt(10.5)
    normal.font.color.rgb = DARK
    for s in doc.sections:
        s.left_margin = Inches(1)
        s.right_margin = Inches(1)

    t = doc.add_heading("Approach 1 — AGMARKNET Direct-API Pipeline", level=0)
    for r in t.runs:
        r.font.color.rgb = ACCENT
    sub = doc.add_paragraph()
    sr = sub.add_run(
        "Fetching daily mandi prices from AGMARKNET over plain HTTP — no headless browser — "
        "with automated CAPTCHA reading (easyOCR + LLM fallback), incremental writes to "
        "mandi.market_prices_v3, per-commodity CSVs mirrored to Azure Blob, and a 4-hourly "
        "scheduler. Build + live validation date: 4 September 2026."
    )
    sr.italic = True
    sr.font.size = Pt(11)
    sr.font.color.rgb = MUTED

    # ---- bottom line
    heading(doc, "Bottom line", 1)
    body(doc,
         "Approach 1 works and is effective. In live tests against the production AGMARKNET API "
         "it fetched, parsed, and stored real price data end-to-end with no browser: easyOCR read "
         "the 6-character CAPTCHA (retrying automatically on a server rejection), the /report "
         "endpoint returned every market for a commodity in a single JSON call, and the rows were "
         "written to mandi.market_prices_v3 with correct new / modified / unchanged accounting and "
         "no duplicates. A short 3-commodity run stored 778 rows for 2026-09-04. "
         "The two costs that remain inherent to this source are (a) one CAPTCHA per commodity "
         "(~605/day) and (b) an undocumented, header-less rate limiter. Recommendation: run "
         "Approach 1 as the FALLBACK pipeline and the official data.gov.in API (Approach 2, no "
         "CAPTCHA) as the PRIMARY, with v3 compared against the browser scraper's v2 for a full "
         "day before any cutover.")

    # ---- objective
    heading(doc, "1. Objective", 1)
    bullet(doc, "No browser automation. ", "Do not use Playwright / Selenium / Puppeteer. Hit the "
           "website's own HTTP endpoints directly.")
    bullet(doc, "Investigate the real endpoints. ", "Identify URL, method, headers, payload, "
           "pagination, and response format from the site's own network traffic.")
    bullet(doc, "CAPTCHA. ", "Detect when the site requires CAPTCHA verification. Original brief: "
           "pause for an authorised human. Revised objective: solve it automatically with OCR "
           "(easyOCR), and because easyOCR alone misreads too often, use an LLM to obtain a clean "
           "read so the 6 characters can be submitted and the data downloaded. Rate limits and "
           "anti-bot protections are respected; nothing beyond reading the glyphs is defeated.")
    bullet(doc, "Store the data. ", "Write to the database (mandi.market_prices_v3) and produce "
           "one CSV per commodity per day, mirrored to Azure Blob Storage — same layout as the "
           "existing browser scraper's archive.")
    bullet(doc, "Production-ready on the Linux VM. ", "4-hourly schedule, retries, timeout "
           "handling, failure handling, logging, monitoring.")

    # ---- what was built
    heading(doc, "2. What was built", 1)
    body(doc, "A self-contained Python package, mandi_pipeline/, deployable to the VM. It lives "
              "outside frontend/ and backend/, so the dashboard repo's .gitignore keeps it out of "
              "that repo's history (same treatment as the rest of the scraper project).")
    code(doc,
         "mandi_pipeline/\n"
         "  config.py            all config from env (nothing hard-coded)\n"
         "  logging_setup.py     loguru -> stdout + rotating file + JSONL\n"
         "  run.py               CLI: --once / --init-db / --status / --dump-sample /\n"
         "                            --only / --limit-commodities / --remirror-csv\n"
         "  operator_console.py  stdlib web UI for the manual CAPTCHA fallback\n"
         "  storage.py           Azure Blob mirror for the CSVs\n"
         "  agmarknet/\n"
         "    client.py          httpx calls to /filters, /captcha/generator, /report + typed errors\n"
         "    captcha.py         solvers: manual | easyocr | llm | llm_assisted_ocr\n"
         "    taxonomy.py        commodity list load / refresh / cache\n"
         "    parser.py          /report JSON -> normalised rows (+ row hash)\n"
         "    csv_export.py      one CSV per commodity per day  (+ blob mirror)\n"
         "    llm_classifier.py  optional fallback classifier for ambiguous responses\n"
         "    pipeline.py        the sweep: per-commodity loop, checkpoint, backoff, run log\n"
         "  db/\n"
         "    schema.sql         market_prices_v3 + pipeline_runs + pipeline_progress + taxonomy\n"
         "    repository.py      every SQL statement (sync psycopg 3)\n"
         "  deploy/              systemd service + timer + install.sh")

    # ---- flow
    heading(doc, "3. End-to-end flow", 1)
    body(doc, "One sweep = for the target date, for every commodity not yet finished:")
    num(doc, "Trigger & guard. systemd timer (every 4h) or `run --once`. Acquire a single-instance "
             "lock; if a sweep is already running, log status=skipped_locked and exit. Resolve "
             "target date = today (Asia/Kolkata), clamped to >= 2025-11-07. INSERT a "
             "mandi.pipeline_runs row (status=running, started_at).")
    num(doc, "Work list. GET /v1/daily-price-arrival/filters (no CAPTCHA) -> 605 commodities, "
             "cached in mandi.agmarknet_taxonomy. Read mandi.pipeline_progress for the date; the "
             "to-do list is every commodity not already 'done'/'empty' (see section 8 for the "
             "late-arrival refresh).")
    num(doc, "STEP 1 - capture the CAPTCHA. POST /v1/captcha/generator {} -> {captcha_key, PNG}. "
             "OpenCV clean-up (grayscale -> 3x upscale -> bilateral denoise -> Otsu threshold -> "
             "morphology). easyOCR reads the cleaned image. If plausible AND confidence >= 0.75, "
             "use it. Otherwise the LLM reads the same cleaned image; easyOCR and the LLM are "
             "cross-checked and on disagreement the LLM is trusted. If nothing plausible, mint a "
             "fresh image and retry (<= 4).")
    num(doc, "STEP 2 - fetch the data. Wait REQUEST_MIN_INTERVAL_SEC since the last /report call, "
             "then POST /v1/daily-price-arrival/report with group, commodity, "
             "from_date=to_date=<date>, data_type=100006, the 'all' sentinels, limit=100000, "
             "captcha, captcha_key. \"Invalid CAPTCHA\" / \"Report filters changed\" -> new image, "
             "retry. 429 / \"Too many requests\" / socket hang -> exponential backoff "
             "(15->30->60->120s, cap 300), retry MAX_RETRIES. status:true -> rows as JSON; "
             "limit=100000 makes total_pages == 1 (whole commodity in one call) plus a "
             "report_ticket good for 30 calls / 5 min on that exact filter set.")
    num(doc, "STEP 3 - normalise. Map the 14 live keys to v3 columns, clean prices/dates, "
             "price_date falls back to arrival_date, compute source_row_hash = sha256(business "
             "fields), de-duplicate on the unique key.")
    num(doc, "STEP 4 - CSV. Write {CSV_OUTPUT_DIR}/{date}/{Commodity}.csv (headers + DD-MM-YYYY "
             "match the browser scraper's \"Download CSV\"), then mirror to Azure Blob at "
             "{container}/{BLOB_PREFIX}{date}/{Commodity}.csv. Blob failure is logged, not fatal.")
    num(doc, "STEP 5 - incremental DB write. Batched INSERT ... ON CONFLICT (unique key) DO UPDATE "
             "... WHERE source_row_hash IS DISTINCT FROM EXCLUDED.source_row_hash RETURNING "
             "(xmax = 0). New key -> inserted; same key, new hash -> updated; same key, same hash "
             "-> left untouched (no duplicate). record_progress(done | empty). Next commodity.")
    num(doc, "Finish. After SWEEP_PASSES passes over the failures, UPDATE the pipeline_runs row: "
             "status = success (0 failed) | partial | failed | aborted, finished_at, and every "
             "counter. Ping HEARTBEAT_URL on success; POST ALERT_WEBHOOK_URL on partial/failed. "
             "Release the lock.")
    body(doc, "Across runs: the 4h timer re-enters at step 1; the lock stops overlap and the "
              "progress table means each run resumes / mops up rather than restarting the day.")

    # ---- verified API
    heading(doc, "4. Verified API facts (from live calls, 4 Sep 2026)", 1)
    table(doc, ["Item", "Detail"], [
        ["Base", "https://api.agmarknet.gov.in/v1"],
        ["Taxonomy", "GET /daily-price-arrival/filters -> 605 commodities {cmdt_id, cmdt_name, "
         "cmdt_group_id}. No CAPTCHA."],
        ["CAPTCHA", "POST /captcha/generator {} -> {captcha_key, captcha_image (base64 PNG), "
         "expires_at}. ~6 alphanumeric chars, no challenge-response."],
        ["Data", "POST /daily-price-arrival/report. Body: from_date, to_date (same, >= 2025-11-07), "
         "data_type=100006, group, commodity (one each), state/district/market/grade/variety = "
         "'all' sentinels, page, limit, captcha, captcha_key."],
        ["One call = whole commodity", "limit=100000 -> total_pages == 1 (Wheat 4 Sep: 136 rows "
         "at 16:07, 161 by 17:07). No pagination loop needed."],
        ["Ticket", "report_access.report_ticket: 30 calls / 5 min, locked to the filter set -> "
         "one CAPTCHA per commodity (~605/day)."],
        ["Row keys", "state_name, district_name, market_name, cmdt_grp_name, cmdt_name, "
         "variety_name, grade_name, arrival_date, min_price, max_price, model_price, arrival_qty, "
         "unit_name_price, unit_name_arrival. (No price_date field.)"],
        ["Headers", "User-Agent, Origin=https://agmarknet.gov.in, Referer, Accept, "
         "Content-Type: application/json. No cookies."],
        ["Rate limiter", "Google Cloud edge. No Retry-After / X-RateLimit headers. ~4 quick calls "
         "then the socket hangs. Treated as: back off."],
    ], widths=[1.7, 4.8])
    callout(doc, "Not verified:",
            "the steady-state rate-limit numbers (needs one clean measurement on a fresh quota) "
            "and AGMARKNET's terms of use on automated access. If the ToS forbids it, Approach 2's "
            "official API is the compliant route.", WARN)

    # ---- captcha
    heading(doc, "5. CAPTCHA handling", 1)
    table(doc, ["Mode (CAPTCHA_SOLVER)", "How it works", "Needs"], [
        ["manual (default)", "Pause; the image is written to state/captcha/ and an authorised "
         "human types it in operator_console.py (loopback, token-gated). The sweep resumes at the "
         "exact commodity.", "-"],
        ["easyocr", "OpenCV preprocessing + easyOCR read of the cleaned image.", "easyocr, "
         "opencv-python-headless"],
        ["llm", "A multimodal LLM reads the 6 characters directly.", "anthropic + ANTHROPIC_API_KEY"],
        ["llm_assisted_ocr (recommended unattended)", "easyOCR fast path when plausible AND "
         "confidence >= 0.75; otherwise the LLM provides the clean read; on disagreement the LLM "
         "wins and the mismatch is logged.", "both of the above"],
    ], widths=[1.9, 3.9, 1.6])
    bullet(doc, "Why the LLM is needed. ", "In live tests easyOCR alone frequently misread "
           "(one commodity took 4 attempts; a 0.82-confidence read was still wrong). A wrong read "
           "is cheap — the server rejects it with \"Invalid CAPTCHA\" and the pipeline mints a "
           "fresh image (up to 4) — but the LLM cross-check raises the first-try rate on the "
           "ambiguous glyphs (0/O, I/l/1, 5/S) that easyOCR fails on.")
    bullet(doc, "\"LLM cleans the image for easyOCR\" is not how it works. ", "An LLM cannot emit "
           "a cleaned image. OpenCV does the image cleaning; when easyOCR still fails, the LLM "
           "reads the characters itself. Same result: 6 characters submitted.")
    bullet(doc, "Not a bypass. ", "This automates reading a plain 6-char image on a public "
           "Government open-data portal that also exposes the same data through an official, "
           "no-CAPTCHA API. The server's rate limit is respected. No challenge-response or "
           "behavioural check exists to defeat.")

    # ---- data model
    heading(doc, "6. Data model", 1)
    body(doc, "python -m mandi_pipeline.run --init-db applies db/schema.sql. The pipeline writes "
              "ONLY these tables — never mandi.market_prices or mandi.market_prices_v2.")
    table(doc, ["Table", "Purpose"], [
        ["mandi.market_prices_v3", "Price rows. Same columns as market_prices_v2 (column-for-column "
         "v3-vs-v2 diff) plus source, source_row_hash, first_seen_at. UNIQUE (state, district, "
         "market, commodity, variety, grade, arrival_date); variety/grade NOT NULL DEFAULT ''."],
        ["mandi.pipeline_runs", "One row per execution — success OR failure — with started_at / "
         "finished_at and all counters. The \"timestamp of every successful and failed execution\" "
         "record."],
        ["mandi.pipeline_progress", "Per-commodity done | empty | failed for a target_date, so a "
         "re-run resumes. Also drives the late-arrival refresh (section 8)."],
        ["mandi.agmarknet_taxonomy", "Cached commodity list from /filters."],
    ], widths=[2.0, 4.5])

    # ---- csv + blob
    heading(doc, "7. CSV output + Azure Blob mirror", 1)
    code(doc,
         "local staging : mandi_pipeline/state/downloads/<YYYY-MM-DD>/<Commodity>.csv\n"
         "Azure Blob    : <AZURE_STORAGE_CONTAINER>/<BLOB_PREFIX><YYYY-MM-DD>/<Commodity>.csv\n"
         "                BLOB_PREFIX defaults to 'agmarknet_api/'")
    bullet(doc, "", "Built from the same /report JSON the site's own \"Download CSV\" button uses "
           "(that button is a browser-side export; there is no separate CSV endpoint).")
    bullet(doc, "", "Same credentials as the browser scraper (AZURE_STORAGE_CONNECTION_STRING, "
           "AZURE_STORAGE_CONTAINER), kept in the VM's env, never in the repo. BLOB_PREFIX keeps "
           "these objects out of the way of that scraper's own <date>/<commodity>.csv blobs.")
    bullet(doc, "", "Best-effort: a failed upload is logged, the local file is kept, and the "
           "commodity is not failed (its DB write already succeeded). CSV_LOCAL_KEEP=false deletes "
           "the local file once the upload is confirmed. run --remirror-csv <date> backfills.")

    # ---- incremental + late arrivals
    heading(doc, "8. Incremental logic & the \"missing records\" finding", 1)
    bullet(doc, "New / modified / unchanged. ", "The upsert's RETURNING (xmax = 0) classifies each "
           "row: new key -> inserted; same key with a different source_row_hash -> updated "
           "(first_seen_at preserved, scraped_at bumped); identical hash -> not rewritten. "
           "Counts land in pipeline_runs.")
    bullet(doc, "Missing. ", "A commodity that returns zero rows is recorded 'empty' (a market "
           "simply may not have reported) rather than failed. Persistent gaps show as 'failed' "
           "progress rows and a 'partial' run status.")
    callout(doc, "Finding (4 Sep):",
            "A check showed Potato with 292 rows in v3 while AGMARKNET was showing more. Cause: "
            "AGMARKNET populates a day's markets progressively — the 16:07 sweep correctly "
            "captured 292; by 17:07 the source had 304. The original resume logic marked Potato "
            "'done' and the next sweep skipped it, so same-day late arrivals were missed.", WARN)
    bullet(doc, "Fix. ", "Added PROGRESS_REFRESH_AFTER_HOURS (default 3). A commodity finished "
           "more than that many hours ago is re-swept on the next run; the hash upsert means only "
           "genuinely new/changed rows are written, and once the day's data stabilises the "
           "re-sweeps become no-ops. Verified: a refresh run took Wheat 136->161 (+25 new, ~5 "
           "changed, 131 unchanged), Onion 293->313, Potato 292->304 — no duplicates.")

    # ---- scheduling
    heading(doc, "9. Scheduling (every 4 hours)", 1)
    bullet(doc, "", "deploy/mandi-pipeline.timer: OnCalendar=*-*-* 00/4:05:00, Persistent=true "
           "(catches up a missed slot after a reboot), 3-min jitter. Triggers the oneshot "
           "mandi-pipeline.service running `run --once --trigger timer`.")
    bullet(doc, "", "Because the sweep is resumable and single-instance-locked, a 4-hourly cadence "
           "against a run that can take longer than 4h just means each tick makes forward "
           "progress. cron equivalent is documented in the README.")
    bullet(doc, "", "deploy/install.sh: rsync to /opt/mandi_pipeline, venv, --init-db, install "
           "both systemd units, enable the timer + the console service.")

    # ---- logging
    heading(doc, "10. Logging & monitoring", 1)
    bullet(doc, "", "loguru -> stdout (journald), state/logs/pipeline.log (10 MB x 10, zipped), "
           "state/logs/pipeline.jsonl (structured).")
    bullet(doc, "", "Every run is a mandi.pipeline_runs row. run --status prints the last 8 runs + "
           "today's progress.")
    bullet(doc, "", "HEARTBEAT_URL pinged on success (dead-man's switch). ALERT_WEBHOOK_URL gets a "
           "JSON summary on partial/failed. Alert if: newest run failed; no success in 24h; "
           "throttle_events spikes; commodities_done well below the taxonomy size.")

    # ---- problems & fixes
    doc.add_page_break()
    heading(doc, "11. Problems faced & fixes", 1)
    table(doc, ["Problem", "Cause", "Fix"], [
        ["/report rows could not be mapped at first.",
         "The row-object key names were never captured in the earlier read-only investigation "
         "(only row counts were).",
         "Ran one live solve+fetch, dumped a real row, pinned the 14 keys in parser._CANDIDATES "
         "(with fallback aliases). run --dump-sample re-confirms."],
        ["easyOCR read the CAPTCHA wrong, sometimes at high confidence.",
         "6-char image with ambiguous glyphs (0/O, I/l/1, 5/S); easyOCR confidence is not a "
         "reliable correctness signal.",
         "Auto-retry: the server rejects a bad answer with \"Invalid CAPTCHA\" and the pipeline "
         "mints a fresh image (<= 4). Added llm_assisted_ocr mode: LLM read + easyOCR/LLM "
         "cross-check for the ambiguous cases."],
        ["First real run wrote to the wrong database.",
         "config did load_dotenv() after loading mandi_pipeline/.env, so the bot's repo-root .env "
         "DATABASE_URL (local 'sandbox' schema) leaked in and won over the pipeline's POSTGRES_* "
         "values.",
         "config now loads ONLY mandi_pipeline/.env when it exists (no second search). It also "
         "accepts POSTGRES_* names so the file can mirror backend/.env. The misfired run is "
         "recorded as 'aborted'."],
        ["INSERT failed: column \"source\" of market_prices_v3 does not exist.",
         "mandi.market_prices_v3 already existed on the shared server as a plain market_prices_v2 "
         "clone (no provenance columns); CREATE TABLE IF NOT EXISTS was a no-op.",
         "schema.sql now also runs ALTER TABLE ... ADD COLUMN IF NOT EXISTS for source, "
         "source_row_hash, first_seen_at, and adds the unique constraint if missing. "
         "Non-destructive; the table was empty."],
        ["Two Potato records looked missing.",
         "AGMARKNET fills a day's markets through the day; the sweep captured a correct earlier "
         "snapshot and the resume logic then skipped the commodity.",
         "PROGRESS_REFRESH_AFTER_HOURS (default 3): re-sweep a commodity finished > N hours ago; "
         "the hash upsert keeps it cheap and duplicate-free."],
        ["Console errors on Windows (emoji), http2 import.",
         "cp1252 console can't encode log emoji; httpx[http2] needs the h2 package.",
         "Runs clean on the Linux VM (UTF-8, journald). requirements pin httpx[http2]; h2 present. "
         "Cosmetic only."],
    ], widths=[1.9, 2.5, 3.0])

    # ---- live evidence
    heading(doc, "12. Live-run evidence (4 Sep 2026, CAPTCHA_SOLVER=easyocr)", 1)
    table(doc, ["Run", "Result"], [
        ["Bounded sweep, --only Wheat,Onion,Potato",
         "done=3/3, failed=0, rows_inserted=721, captcha_solves=6, throttle_events=0. "
         "v3 total 721 = distinct unique keys 721 (no duplicates)."],
        ["Refresh sweep (PROGRESS_REFRESH_AFTER_HOURS on)",
         "Wheat +25 new / ~5 changed / 131 same; Onion +20 new; Potato +12 new. "
         "v3 now 778 rows for 2026-09-04. Modified-row detection and no-op unchanged rows both "
         "confirmed."],
        ["CAPTCHA reliability",
         "easyOCR first-try on some images (conf 0.96); up to 4 fresh-image retries on others; "
         "every misread absorbed by the retry loop; no human, no LLM key used."],
        ["Per-call shape",
         "Wheat = 136 rows in ONE /report call, total_pages=1, report_ticket issued, "
         "remaining_calls 30 -> 29."],
    ], widths=[2.3, 4.2])

    # ---- limitations
    heading(doc, "13. Limitations", 1)
    bullet(doc, "One CAPTCHA per commodity (~605/day). ", "Inherent to the source. easyOCR + LLM "
           "keeps it unattended; a licensed solver or batched human sittings are alternatives.")
    bullet(doc, "Undocumented rate limiter. ", "Pacing + backoff handle it, but a full 605-commodity "
           "sweep is measured in hours, not minutes. One clean calibration on a fresh quota is "
           "still outstanding.")
    bullet(doc, "Undocumented API. ", "Endpoints were reverse-engineered from the site's own "
           "traffic; a redesign could break the client. parser raises SchemaError loudly and "
           "run --dump-sample re-confirms the shape.")
    bullet(doc, "Terms of use. ", "Confirm AGMARKNET permits automated access. If not, Approach 2.")

    # ---- recommendation
    heading(doc, "14. Recommendation", 1)
    num(doc, "PRIMARY: Approach 2 — the official data.gov.in API (resource "
             "9ef84268-d588-465a-a308-a864a43d0070). No CAPTCHA, documented, stable envelope, "
             "cheap to poll every 4 hours.")
    num(doc, "FALLBACK: Approach 1 — this pipeline. Use it when a field the API does not carry is "
             "needed, or if the API is unavailable. It is proven effective and production-shaped.")
    num(doc, "Before any cutover from the browser scraper: run v3 (Approach 1) and/or the "
             "data.gov.in table alongside the existing v2 for a full day and diff them "
             "column-for-column.")
    num(doc, "Outstanding: one clean rate-limit measurement; confirm AGMARKNET ToS; wire "
             "AZURE_STORAGE_CONNECTION_STRING on the VM; optionally enable llm_assisted_ocr for "
             "the unattended full sweep.")

    doc.save(OUT_PATH)
    print(f"Wrote {OUT_PATH}")


if __name__ == "__main__":
    build()
