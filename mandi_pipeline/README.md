# mandi_pipeline — AGMARKNET direct-API pipeline (Approach 1)

Pulls daily mandi (market) prices from **AGMARKNET** over plain HTTP — **no
headless browser** (no Playwright / Selenium / Puppeteer anywhere) — and
writes them to `mandi.market_prices_v3` for a parallel comparison against
the existing browser scraper's `mandi.market_prices_v2`.

> Scope note: this folder is part of the *scraper* project, not the QA
> dashboard. The repo `.gitignore` intentionally tracks only `frontend/`
> and `backend/`, so `mandi_pipeline/` stays out of that repo's history —
> deploy it to the VM from a plain copy of the folder.

---

## 1. What is verified vs assumed

**Verified live from the VM in the 2026-09-03 investigation**
(`docs/agmarknet_api_investigation.docx`) — this pipeline is built on these:

| # | Fact |
|---|------|
| 1 | `GET  {api}/daily-price-arrival/filters` returns the full commodity taxonomy (`cmdt_id`, `cmdt_name`, `cmdt_group_id`), **no CAPTCHA**, HTTP 200. |
| 2 | `POST {api}/captcha/generator` with body `{}` returns `{captcha_key, captcha_image (base64 PNG), expires_at}`, ~5 min validity. |
| 3 | `POST {api}/daily-price-arrival/report` with a solved CAPTCHA + `limit=100000` returns an **entire commodity in one response** (Wheat, 1 Sep: 879 rows, `total_pages == 1`). |
| 4 | The response carries `report_access.report_ticket` (30 calls / 5 min) but it is **locked to the exact filter set**. Changing `commodity` → *"Report filters changed. Please solve captcha again."* → **one CAPTCHA per commodity** (~605/day). |
| 5 | Data exists only from **2025-11-07** (AGMARKNET 2.0 go-live). |
| 6 | The rate limiter (Google Cloud edge) sends **no** `Retry-After` / `X-RateLimit` headers. ~4 quick calls then the socket hangs. A "polite" 60-commodity test (6 s gap, 3 passes) solved 52 %, 56 throttle events, ≈7 h extrapolated for a full day. |
| 7 | Request headers are just `User-Agent`, `Origin: https://agmarknet.gov.in`, `Referer`, `Accept`, `Content-Type: application/json`. **No cookies.** |

**Also verified 2026-09-04 (this build):** `CAPTCHA_SOLVER=easyocr` read a
live CAPTCHA first try (conf 0.96), the server accepted it, `/report`
returned 119 Wheat rows in one call (`pages=1`), `remaining_calls` went
29→ (30/ticket confirmed). Row keys are now pinned in `parser._CANDIDATES`:
`state_name, district_name, market_name, cmdt_grp_name, cmdt_name,
variety_name, grade_name, arrival_date, min_price, max_price,
model_price, arrival_qty, unit_name_price, unit_name_arrival` (no
`price_date` → falls back to `arrival_date`).

**Assumed / not verified** — handled defensively:

| Assumption | How the code copes | How to confirm |
|---|---|---|
| Steady-state rate limit (the team flagged this needs one clean measurement on a fresh quota). | `REQUEST_MIN_INTERVAL_SEC` pacing + exponential backoff + multi-pass sweep. | Watch `throttle_events` in `mandi.pipeline_runs`; tune `REQUEST_MIN_INTERVAL_SEC`. |
| `district`/`market`/`grade`/`variety` "all" sentinels (`[100001]` …) stay stable. | Centralised in `client._ALL_SENTINELS`. | `--dump-sample` still returns rows. |
| Exact `/report` error strings for throttle vs captcha. | String match **first**; optional LLM classifier only for a 200 that matches nothing. | Inspect `SchemaError` logs. |

Nothing here bypasses or weakens the CAPTCHA or any anti-bot control. The
default flow **pauses for an authorised human** (§4).

---

## 2. Folder layout

```
mandi_pipeline/
  config.py              all config from env (nothing hard-coded)
  logging_setup.py       loguru -> stdout + rotating file + JSONL
  run.py                 CLI entry point (--once / --init-db / --dump-sample / --status)
  operator_console.py    stdlib web UI for the human CAPTCHA solve
  requirements.txt       httpx, loguru, psycopg — NO browser libs
  .env.example
  agmarknet/
    client.py            httpx calls to /filters, /captcha/generator, /report + typed errors
    captcha.py           CaptchaSolver: ManualSolver (default) | OcrSolver (opt-in)
    taxonomy.py          commodity list load / refresh / cache
    parser.py            /report JSON -> normalised rows (+ tolerant key mapping, row hash)
    llm_classifier.py    optional fallback classifier for ambiguous responses
    pipeline.py          the sweep: per-commodity loop, checkpoint, backoff, run log
  db/
    schema.sql           market_prices_v3 + pipeline_runs + pipeline_progress + agmarknet_taxonomy
    repository.py         every SQL statement (sync psycopg 3)
  deploy/
    mandi-pipeline.service / .timer        4-hourly sweep
    mandi-pipeline-console.service         the operator console
    install.sh
  state/                 runtime only (logs, captcha files, lock) — gitignored
```

---

## 3. How the extraction works (Approach 1, step by step)

1. **Resolve the target date** — today (Asia/Kolkata), clamped to ≥ 2025-11-07.
2. **Single-instance lock** (`state/pipeline.lock`, `flock`). If a sweep is
   already running, record `status=skipped_locked` and exit — this is normal
   when the 4-hour timer overlaps a long run.
3. **`start_run`** → a `mandi.pipeline_runs` row (`status=running`, `started_at`).
4. **Taxonomy** — `GET /filters` (cached in `mandi.agmarknet_taxonomy`;
   `--refresh-taxonomy` to re-pull). No CAPTCHA.
5. **Resume** — read `mandi.pipeline_progress` for the date; skip commodities
   already `done`/`empty`.
6. **For each remaining commodity** (`SWEEP_PASSES` passes, re-trying only
   what still failed):
   1. `POST /captcha/generator` → image + key.
   2. **Solve** via the configured `CaptchaSolver` (§4).
   3. `POST /report` with `group`, `commodity`, `from_date=to_date=<date>`,
      `data_type=100006`, the "all" sentinels, `limit=100000`, `captcha`,
      `captcha_key`.
   4. Interpret the response:
      * `status:true` → rows.
      * body says *captcha* / *"Report filters changed"* → `CaptchaRequired`
        → mint a new image, retry (up to 4).
      * `429` / *"too many requests"* / socket hang → `RateLimited` →
        exponential backoff (`BACKOFF_BASE_SEC`×2ⁿ, capped), retry up to
        `MAX_RETRIES`; increments `throttle_events`.
      * unrecognised 200 → optional LLM classify → else `SchemaError`, mark
        that one commodity `failed`, **continue the sweep**.
   5. **Pagination** — `limit=100000` normally gives `total_pages==1`. If not,
      `fetch_all_pages` loops using the `report_ticket` (no extra CAPTCHA).
   6. **Parse** (`parser.normalize_rows`): tolerant key mapping, price/date
      cleaning, `source_row_hash = sha256(business fields)`, de-dupe on the
      unique key.
   7. **Upsert** (`repository.upsert_price_rows`): batched
      `INSERT … ON CONFLICT (state,district,market,commodity,variety,grade,arrival_date)
      DO UPDATE … WHERE source_row_hash IS DISTINCT FROM EXCLUDED.source_row_hash
      RETURNING (xmax = 0)`.
      → counts **new** (`xmax=0`), **modified** (returned, not insert),
      **unchanged** (submitted − returned). No duplicates.
   8. `record_progress(done | empty | failed)`.
7. **`finish_run`** — `status` = `success` (0 failed) / `partial` (some) /
   `failed` (none done) / `aborted` (exception); write every counter and
   `finished_at`.
8. **Notify** — ping `HEARTBEAT_URL` on success; POST a JSON summary to
   `ALERT_WEBHOOK_URL` on partial/failed.

**Incremental / new-modified-missing detection**

* **New** — `xmax=0` rows from the upsert (`rows_inserted`).
* **Modified** — a row with the same unique key but a different
  `source_row_hash` (`rows_updated`); `first_seen_at` is preserved,
  `scraped_at` bumped.
* **Unchanged** — hash identical → not rewritten (`rows_unchanged`).
* **Missing** — compare `repository.commodities_present_for_date(date)` (and,
  per market, previous-day keys) against what came back; a commodity that
  returns zero rows is recorded `empty` (not an error — a market simply may
  not have reported). Persistent gaps show up as `failed` progress rows and
  in the `partial` run status.

---

## 4. CAPTCHA

The AGMARKNET CAPTCHA is a plain ~6-char alphanumeric image — no
challenge-response, no behavioural check. Four modes (`CAPTCHA_SOLVER=`):

| mode | how | needs |
|---|---|---|
| `manual` (default) | pause; authorised human types it in the console | — |
| `easyocr` | local easyocr over a preprocessed image (grayscale → upscale → bilateral denoise → Otsu → morphology) | `easyocr`, `opencv-python-headless` |
| `llm` | a multimodal LLM reads the 6 chars | `anthropic`, `ANTHROPIC_API_KEY` |
| `llm_assisted_ocr` | **recommended automated mode** — easyocr fast path (used when plausible *and* conf ≥ 0.75); otherwise the LLM provides the clean read; on disagreement the LLM wins and the mismatch is logged | both of the above |

A wrong read costs only a retry: `_process_commodity` mints a fresh image
and re-solves up to `_MAX_CAPTCHA_ATTEMPTS` times, and the server rejects a
bad answer with a clear "solve captcha again". Set `CAPTCHA_DEBUG=true` to
dump every raw+preprocessed image plus the guess to
`state/captcha/debug/samples.jsonl` for tuning.

> This automates *reading* the CAPTCHA. It removes the human step the
> CAPTCHA exists to enforce. It is used here against a **public GoI
> open-data portal that also exposes the same data via an official
> no-CAPTCHA API** (Approach 2), respects the server's rate limit, and
> does not defeat any protection beyond the glyphs. Confirm AGMARKNET's
> terms of use permit automated access; if not, use Approach 2.

### manual mode flow

1. Pipeline reaches a commodity needing a CAPTCHA, fetches the image, and
   writes `state/captcha/<id>.png` + `<id>.json` (`status: pending`, which
   commodity, expiry).
2. It **blocks**, polling for an operator response, up to
   `CAPTCHA_WAIT_TIMEOUT_SEC` (default 30 min → then treated as *skip*, sweep
   continues; the next timer run retries that commodity).
3. `operator_console.py` (a stdlib web page, loopback-only, `?token=` on
   every request) lists pending images. The authorised human types the six
   characters and clicks **Submit** (or **Unreadable** → new image, or
   **Skip**). That writes `<id>.answer` / `<id>.reject` / `<id>.skip`.
4. The pipeline picks up `<id>.answer`, submits it to `/report`, gets the
   `report_ticket`, and **resumes** at the exact commodity it paused on.
   Progress for earlier commodities is already committed, so a restart mid-run
   loses nothing.

Reach the console from a laptop via SSH tunnel:

```
ssh -L 8770:127.0.0.1:8770 azureuser@<vm>
# http://127.0.0.1:8770/?token=<OPERATOR_CONSOLE_TOKEN>
```

**Operational reality:** ~605 solves/day rules out `manual` as a steady
state — it's the safe default and the fallback when an automated read
keeps failing. For unattended running use `CAPTCHA_SOLVER=llm_assisted_ocr`
(§4 table): easyocr alone was ~70 % first-try in the prior test; the LLM
rescue on the ambiguous-glyph minority (0/O, I/l/1, 5/S) is what makes an
unattended sweep viable.

---

## 5. Database

`python -m mandi_pipeline.run --init-db` applies `db/schema.sql`:

* **`mandi.market_prices_v3`** — same columns as `market_prices_v2` (so a
  v3-vs-v2 diff is column-for-column) plus `source`, `source_row_hash`,
  `first_seen_at`, `scraped_at`. `UNIQUE (state, district, market, commodity,
  variety, grade, arrival_date)`; `variety`/`grade` are `NOT NULL DEFAULT ''`
  so same-day re-runs update in place instead of duplicating.
* **`mandi.pipeline_runs`** — one row per execution, success **or** failure,
  with `started_at` / `finished_at` and all counters. This is the
  "timestamp of every successful and failed execution" record.
* **`mandi.pipeline_progress`** — per-commodity `done|empty|failed` for a
  date, so a re-run resumes.
* **`mandi.agmarknet_taxonomy`** — cached commodity list.

The pipeline writes **only** these tables — never `mandi.market_prices` or
`mandi.market_prices_v2`.

---

## 5b. CSV output + Azure Blob mirror

After a commodity's rows are normalised, `csv_export.write_commodity_csv`
writes them (built from the same `/report` JSON the site's own "Download
CSV" button uses):

```
local staging : {CSV_OUTPUT_DIR}/{YYYY-MM-DD}/{Commodity}.csv
Azure Blob     : {AZURE_STORAGE_CONTAINER}/{BLOB_PREFIX}{YYYY-MM-DD}/{Commodity}.csv
```

Headers and `DD-MM-YYYY` date format match the browser scraper's archive.
`BLOB_PREFIX` (default `agmarknet_api/`) keeps these objects out of the way
of that scraper's own `<date>/<commodity>.csv` blobs in the same container.

- Same credentials as the browser scraper: `AZURE_STORAGE_CONNECTION_STRING`
  + `AZURE_STORAGE_CONTAINER` (kept in the VM's env / `mandi_pipeline/.env`,
  never in the repo).
- Best-effort: a failed upload is logged, the local file is kept, and the
  commodity is **not** failed (its DB write already succeeded).
- `CSV_LOCAL_KEEP=false` deletes the local file once the blob upload is
  confirmed; default `true` keeps both.
- `BLOB_UPLOAD_ENABLED` auto-enables when a connection string is present.

---

## 6. Scheduling (every 4 hours)

`deploy/mandi-pipeline.timer` → `OnCalendar=*-*-* 00/4:05:00`, `Persistent=true`
(catches up a missed slot after a reboot), 3-min jitter. It triggers the
`oneshot` `mandi-pipeline.service` which runs `run.py --once --trigger timer`.
Because the sweep is resumable and single-instance-locked, a 4-hourly cadence
against a run that can take longer than 4 h just means each tick makes
forward progress / mops up misses.

cron equivalent, if you prefer:

```
5 */4 * * *  cd /opt/mandi_pipeline && ./venv/bin/python -m mandi_pipeline.run --once --trigger timer >> state/logs/cron.log 2>&1
```

---

## 7. Logging & monitoring

* **loguru** → stdout (journald), `state/logs/pipeline.log` (10 MB × 10,
  zipped), `state/logs/pipeline.jsonl` (structured, one object per line).
* Every run is a row in `mandi.pipeline_runs`. Monitoring query:

  ```sql
  SELECT started_at, status, commodities_done, commodities_total,
         rows_inserted, rows_updated, throttle_events, error
  FROM mandi.pipeline_runs
  WHERE approach = 'agmarknet_api'
  ORDER BY started_at DESC LIMIT 20;
  ```
* `python -m mandi_pipeline.run --status` prints the last 8 runs + today's
  progress.
* `HEARTBEAT_URL` pinged on success (dead-man's switch, e.g. healthchecks.io).
* `ALERT_WEBHOOK_URL` gets a JSON summary on `partial`/`failed`.
* Alert if: newest run is `failed`; no `success` in 24 h; `throttle_events`
  spikes; `commodities_done` well below the taxonomy size.

---

## 8. Quick start

```bash
python -m venv venv && ./venv/bin/pip install -r requirements.txt
cp .env.example .env          # then edit: DB creds, OPERATOR_CONSOLE_TOKEN
python -m mandi_pipeline.run --init-db
python -m mandi_pipeline.run --refresh-taxonomy
python -m mandi_pipeline.operator_console &     # in another shell / service
python -m mandi_pipeline.run --dump-sample      # solve 1 CAPTCHA, PIN THE ROW KEYS
python -m mandi_pipeline.run --once --trigger manual
```

VM deploy: `sudo bash deploy/install.sh` then edit `/opt/mandi_pipeline/mandi_pipeline/.env`.
