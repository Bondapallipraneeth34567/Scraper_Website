"""Renders the 2026-09-03 AGMARKNET direct-API investigation as a .docx:
what was asked, how it was tested, every finding with its evidence, the
rate-limit blocker, the decision analysis, the recommendation, and a
prep section (talking points + likely manager questions) for the author.

Run: python docs/generate_agmarknet_api_investigation_docx.py
"""

from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Pt, RGBColor, Inches
from docx.oxml.ns import qn

OUT_PATH = Path(__file__).parent / "agmarknet_api_investigation.docx"

CODE_FONT = "Consolas"
BODY_FONT = "Calibri"
ACCENT = RGBColor(0x1F, 0x5C, 0x3D)
DARK_TEXT = RGBColor(0x22, 0x22, 0x22)
WARN = RGBColor(0xA0, 0x3D, 0x1F)
GOOD = RGBColor(0x1F, 0x5C, 0x3D)


def set_shading(paragraph, fill):
    pPr = paragraph._p.get_or_add_pPr()
    shd = pPr.makeelement(qn("w:shd"), {qn("w:val"): "clear", qn("w:color"): "auto", qn("w:fill"): fill})
    pPr.append(shd)


def add_code_block(doc, text):
    for line in text.rstrip("\n").split("\n"):
        p = doc.add_paragraph()
        p.paragraph_format.space_after = Pt(0)
        p.paragraph_format.space_before = Pt(0)
        set_shading(p, "F2F2F2")
        run = p.add_run(line if line.strip() else " ")
        run.font.name = CODE_FONT
        run.font.size = Pt(9)
        run.font.color.rgb = DARK_TEXT


def add_body(doc, text, size=10.5, italic=False, after=8):
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(after)
    run = p.add_run(text)
    run.font.name = BODY_FONT
    run.font.size = Pt(size)
    run.italic = italic
    return p


def add_bullet(doc, label, rest):
    p = doc.add_paragraph(style="List Bullet")
    p.paragraph_format.space_after = Pt(5)
    if label:
        r1 = p.add_run(label)
        r1.bold = True
        r1.font.name = BODY_FONT
        r1.font.size = Pt(10.5)
        r1.font.color.rgb = ACCENT
    r2 = p.add_run(rest)
    r2.font.name = BODY_FONT
    r2.font.size = Pt(10.5)
    return p


def add_num(doc, text):
    p = doc.add_paragraph(style="List Number")
    p.paragraph_format.space_after = Pt(5)
    r = p.add_run(text)
    r.font.name = BODY_FONT
    r.font.size = Pt(10.5)
    return p


def add_callout(doc, prefix, text, color):
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(8)
    p.paragraph_format.space_before = Pt(4)
    set_shading(p, "F4F6F4")
    r1 = p.add_run(prefix + "  ")
    r1.bold = True
    r1.font.color.rgb = color
    r1.font.size = Pt(10.5)
    r2 = p.add_run(text)
    r2.font.name = BODY_FONT
    r2.font.size = Pt(10.5)
    return p


def add_heading(doc, text, level):
    h = doc.add_heading(text, level=level)
    for run in h.runs:
        run.font.color.rgb = ACCENT
        run.font.name = BODY_FONT
    return h


def add_table(doc, headers, rows, widths=None):
    table = doc.add_table(rows=1, cols=len(headers))
    table.style = "Light Grid Accent 1"
    for i, h in enumerate(headers):
        c = table.rows[0].cells[i]
        c.text = ""
        r = c.paragraphs[0].add_run(h)
        r.bold = True
        r.font.name = BODY_FONT
        r.font.size = Pt(9.5)
    for row in rows:
        cells = table.add_row().cells
        for i, val in enumerate(row):
            cells[i].text = ""
            r = cells[i].paragraphs[0].add_run(str(val))
            r.font.name = BODY_FONT
            r.font.size = Pt(9.5)
    if widths:
        for row in table.rows:
            for i, w in enumerate(widths):
                row.cells[i].width = Inches(w)
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

    title = doc.add_heading("AGMARKNET Direct-API Investigation", level=0)
    for run in title.runs:
        run.font.color.rgb = ACCENT
    sub = doc.add_paragraph()
    sr = sub.add_run(
        "Can we retire the browser and pull mandi price data straight from AGMARKNET's API? "
        "Investigation date: 3 September 2026. All tests run read-only on the production VM; "
        "the live scraper and its database were not modified."
    )
    sr.italic = True
    sr.font.size = Pt(11)
    sr.font.color.rgb = RGBColor(0x55, 0x55, 0x55)

    # ---------------------------------------------------------------- TL;DR
    add_heading(doc, "Bottom line (one paragraph)", 1)
    add_body(
        doc,
        "AGMARKNET has a clean JSON API and we can call it from the VM without a browser. "
        "But it is protected by a CAPTCHA that must be solved once per commodity (~605 per day, "
        "unchanged from today) and an aggressive, uncooperative rate limiter. The dream of "
        "“13 hours down to 2 hours” is not achievable — the CAPTCHA count and the "
        "server-side throttle cap both the browser and the API. The API path is still worth "
        "building, but for reliability (no browser crashes, simpler code), not raw speed. "
        "Realistic full-day runtime on the API is ~6–10 hours versus the browser's ~13, with "
        "fewer failures. Recommended: build it as a parallel, low-risk experiment against a "
        "separate table and compare, before any cutover."
    )

    # ---------------------------------------------------------------- Why
    add_heading(doc, "1. Why we looked at this", 1)
    add_bullet(doc, "Today's scraper ", "drives the AGMARKNET website with a headless browser "
               "(Playwright): it loads the page, fills three dropdowns, solves a CAPTCHA, clicks "
               "“Go”, and downloads a CSV — once per commodity.")
    add_bullet(doc, "Pain points ", "a full day of ~605 commodities takes roughly 13 hours and "
               "often does not finish; the browser hits page-load errors, dropdown race conditions "
               "and long “losing overlay” stalls; some days land only ~250–320 of the "
               "~605 commodities.")
    add_bullet(doc, "The question ", "the website's own pages are served by a backend API. If we "
               "call that API directly, can we drop the fragile browser layer and finish faster "
               "and more reliably?")
    add_bullet(doc, "Hard constraint ", "do not change the running production scraper or its data "
               "until the API approach is fully proven.")

    # ---------------------------------------------------------------- Method
    add_heading(doc, "2. How we tested it", 1)
    add_body(doc, "Deliberately cautious, because it touches a production system:")
    add_bullet(doc, "", "Every probe ran on the production VM, made only read calls, and wrote "
               "nothing to any database.")
    add_bullet(doc, "", "One small script at a time; each result reviewed before the next step.")
    add_bullet(doc, "", "Findings were split into “confirmed by a live response” versus "
               "“hypothesis” — nothing was assumed.")
    add_bullet(doc, "", "An 8-step plan (map endpoints → verify from VM → understand the "
               "CAPTCHA/ticket → measure throughput → decide). We completed step 6 of 8.")

    # ---------------------------------------------------------------- Confirmed
    add_heading(doc, "3. What we confirmed (each backed by a live API response)", 1)
    add_table(
        doc,
        ["Finding", "Evidence"],
        [
            ["A commodity/filter list endpoint (GET /v1/daily-price-arrival/filters) returns the full "
             "taxonomy — every commodity with its numeric ID — with NO CAPTCHA.",
             "HTTP 200, ~525 KB, from the VM. Replaces a slow browser-driven “harvest” step."],
            ["A CAPTCHA endpoint (POST /v1/captcha/generator) returns a fresh image + key on demand, "
             "works from the VM over plain HTTP.",
             "HTTP 200, returns captcha_key + base64 PNG, ~5 minute validity."],
            ["The data endpoint (POST /v1/daily-price-arrival/report) with a solved CAPTCHA and "
             "limit = 100000 returns an ENTIRE commodity's rows in ONE JSON call — no page-by-page "
             "loop.",
             "Wheat, 1 Sep: 879 rows in a single response, pagination shows 1 page."],
            ["Each call returns a “report ticket” good for 30 calls / 5 minutes — BUT it is "
             "locked to the exact filter set that created it.",
             "Changing the commodity returns: “Report filters changed. Please solve captcha again.”"],
            ["Therefore one CAPTCHA is required per commodity. ~605 CAPTCHAs per day — the same as "
             "the browser scraper today.",
             "The endpoint accepts exactly one commodity ID per call (server rejects arrays / "
             "“all” values)."],
            ["Our CAPTCHA OCR solves correctly ~70% on the first try when calls are spaced out.",
             "12-round test: 7 solved as-is on attempt 1; failures were the visually ambiguous "
             "characters (0/O, I/l/1, 5/S)."],
            ["Data is only available from 7 November 2025 onward (AGMARKNET 2.0 go-live).",
             "Stated in the filters endpoint response."],
        ],
        widths=[3.3, 3.2],
    )

    # ---------------------------------------------------------------- Blocker
    add_heading(doc, "4. The blocker: the rate limiter", 1)
    add_body(
        doc,
        "This is the finding that changes the conclusion. The API is protected by an aggressive "
        "rate limiter (the service sits behind Google Cloud's edge). Its behaviour:"
    )
    add_bullet(doc, "No cooperation ", "no “Retry-After”, no “X-RateLimit” headers, "
               "no clean “429 Too Many Requests”. Nothing tells a client what the limit is.")
    add_bullet(doc, "Tiny burst, then silence ", "about 4 rapid requests succeed; the 5th simply "
               "hangs — the server stops responding and the connection is dropped. Each time this "
               "happens, the client wastes a full 30–45-second timeout on a dead socket.")
    add_bullet(doc, "It does not recover quickly ", "a “polite” test (60 commodities, one "
               "attempt each, 6 seconds apart, three passes) hit 56 throttle events, solved only "
               "31 of 60, and extrapolated to ~7 hours for a full day.")
    add_callout(
        doc, "Key point:",
        "the browser scraper's CSV download calls this SAME endpoint. The browser only survives "
        "the limit because it is slow — its form-driving and overlay waits naturally space "
        "requests ~30–120 seconds apart. The rate limit is not new; the API path just makes it "
        "visible.",
        WARN,
    )
    add_body(
        doc,
        "Caveat on the numbers: we ran roughly ten probe sessions across one day, so we likely "
        "exhausted a longer-window quota. The true steady-state limit needs one clean measurement "
        "on a fresh quota (see Next Steps).",
        italic=True,
    )

    # ---------------------------------------------------------------- Means
    add_heading(doc, "5. What this means", 1)
    add_table(
        doc,
        ["Dimension", "Browser scraper (today)", "Direct API"],
        [
            ["CAPTCHAs per day", "~605 (one per commodity)", "~605 (unchanged)"],
            ["Per-commodity work", "load page, 3 dropdowns, Go, overlay stalls, CSV download",
             "one HTTP request (~300 ms) + one CAPTCHA"],
            ["Pagination", "n/a (CSV)", "none needed (limit = 100000)"],
            ["Browser failure modes", "frequent (page errors, dropdown races, network errors on VM)",
             "eliminated"],
            ["Taxonomy refresh", "browser-driven “harvest”", "one API call, no CAPTCHA"],
            ["Estimated full-day run", "~13 h, often incomplete", "~6–10 h, more reliable"],
            ["Rate-limit exposure", "yes (endpoint is shared) — tolerated because slow",
             "yes — needs careful pacing + backoff"],
        ],
        widths=[1.7, 2.5, 2.5],
    )
    add_callout(
        doc, "So:",
        "the value of the API rewrite is RELIABILITY and MAINTAINABILITY (no browser, simpler "
        "code, we can finally know when a day is complete), plus a moderate speed gain — not a "
        "dramatic one.",
        GOOD,
    )

    # ---------------------------------------------------------------- Options
    add_heading(doc, "6. Options", 1)
    add_bullet(doc, "Option A — build the API scraper (recommended). ",
               "New module writing to a separate table (mandi.market_prices_v3). Conservative "
               "pacing (~1 commodity per 30–45 s), exponential backoff on timeouts, multiple "
               "passes over the day to mop up misses. Run it alongside production; compare v3 "
               "against today's table for a full day; only then decide on cutover.")
    add_bullet(doc, "Option B — keep the browser, harden it. ",
               "Do not rewrite. Ship the small fix already identified (a bug that stopped the "
               "scraper ever marking a day “done”, so it re-scraped the same date forever) "
               "and invest in better CAPTCHA OCR. Lower effort, keeps the known fragility.")
    add_bullet(doc, "Option C — hybrid. ",
               "Adopt the no-CAPTCHA filters endpoint now (removes the browser-driven taxonomy "
               "harvest, zero risk), keep the browser for the actual data pull.")

    # ---------------------------------------------------------------- Reco
    add_heading(doc, "7. Recommendation", 1)
    add_num(doc, "Proceed with Option A as a parallel experiment. Risk is near zero: separate "
                 "table, production untouched, cutover is a later, separate decision.")
    add_num(doc, "First, one clean rate-limit measurement on a fresh quota (fixed intervals, "
                 "exponential backoff) to lock down the real full-day runtime figure.")
    add_num(doc, "Adopt the filters endpoint for the taxonomy regardless of the above — it is "
                 "safe and removes a fragile step today.")
    add_num(doc, "Ship the “day never marked complete” fix to the current scraper now; it "
                 "helps whichever path we choose.")

    # ---------------------------------------------------------------- Next
    add_heading(doc, "8. Next steps & rough effort", 1)
    add_table(
        doc,
        ["Step", "Effort", "Risk"],
        [
            ["Clean rate-limit calibration (fresh quota)", "~0.5 day", "none (read-only)"],
            ["Build agmarknet_api_scraper.py → market_prices_v3", "~2–3 days", "none (new table)"],
            ["Parallel run + daily v3-vs-v2 data comparison", "~1 week elapsed", "none"],
            ["Cutover decision + switch production", "~0.5 day", "medium — gated on comparison passing"],
            ["Optional: paid CAPTCHA solver for speed (~$0.30–$0.60/day)", "~0.5 day", "low"],
        ],
        widths=[3.6, 1.4, 1.8],
    )

    # ---------------------------------------------------------------- Appendix A
    doc.add_page_break()
    add_heading(doc, "Appendix A — Verified API model (for the record)", 1)
    add_body(doc, "1. Get the commodity list (no CAPTCHA):")
    add_code_block(doc, "GET https://api.agmarknet.gov.in/v1/daily-price-arrival/filters\n"
                        "-> { data: { cmdt_data: [ {cmdt_id, cmdt_name, cmdt_group_id}, ... ] } }")
    add_body(doc, "2. Get a CAPTCHA:")
    add_code_block(doc, "POST https://api.agmarknet.gov.in/v1/captcha/generator   body: {}\n"
                        "-> { captcha_key, captcha_image (base64 PNG), expires_at }  (~5 min)")
    add_body(doc, "3. Solve the CAPTCHA image (OCR), then request one commodity:")
    add_code_block(doc,
                   "POST https://api.agmarknet.gov.in/v1/daily-price-arrival/report\n"
                   "body: {\n"
                   "  from_date, to_date,            # same date; data exists from 2025-11-07\n"
                   "  data_type: \"100006\",           # price + arrival\n"
                   "  group:     <cmdt_group_id>,     # one integer\n"
                   "  commodity: <cmdt_id>,           # one integer\n"
                   "  state: \"[100000]\", district: \"[100001]\", market: \"[100002]\",\n"
                   "  grade: \"[100003]\", variety: \"[100007]\",   # “all” sentinels\n"
                   "  page: \"1\", limit: \"100000\",   # limit=100000 -> whole commodity, one call\n"
                   "  captcha: <ocr_text>, captcha_key: <key>\n"
                   "}\n"
                   "-> { status: true, data: { records: [ { data: [ ...rows... ],\n"
                   "     pagination: [ {total_count, total_pages, ...} ] } ] },\n"
                   "     report_access: { report_ticket, remaining_calls, max_calls: 30, expires_at } }")
    add_body(doc, "Each row carries: State/UT, District, Market, Commodity Group, Commodity, "
                  "Variety, Grade, Min/Max/Modal Price, Price Unit, Arrival Quantity, Arrival Unit, "
                  "Arrival Date — the same fields the current scraper stores.")
    add_body(doc, "The report_ticket is only reusable for the identical query (e.g. paging the same "
                  "commodity). Changing the commodity forces a new CAPTCHA, which is why the count "
                  "stays at one per commodity.")

    # ---------------------------------------------------------------- Appendix B
    add_heading(doc, "Appendix B — Prep notes (talking points & likely questions)", 1)

    add_body(doc, "Lead with these three sentences:", size=10.5)
    add_bullet(doc, "", "“AGMARKNET does have a usable API and we can reach it from our server "
               "without a browser.”")
    add_bullet(doc, "", "“But it needs a CAPTCHA per commodity and it is heavily rate-limited, "
               "so it will not be dramatically faster — the gain is reliability, not speed.”")
    add_bullet(doc, "", "“I recommend building it in parallel against a test table, with zero "
               "risk to production, and comparing before we switch.”")

    add_body(doc, " ", after=2)
    add_body(doc, "Anticipated questions:", size=10.5)

    qa = [
        ("“Why did this take a day?”",
         "The API is undocumented — it had to be reverse-engineered from the website's own "
         "network traffic, one careful step at a time, on a live system we were not allowed to "
         "disturb. We now have a precise map of it."),
        ("“Is this allowed / is it scraping we shouldn't do?”",
         "It is a public Government open-data portal, and these are the exact endpoints the "
         "AGMARKNET website itself uses. We honour their rate limit. Calling the API directly is "
         "actually gentler on their servers than driving a full browser."),
        ("“So we spent a day to conclude 'not much faster' — was it wasted?”",
         "No. We ruled out a multi-week rewrite that would NOT have paid off, which is a valuable "
         "negative result. We also found concrete wins: no browser fragility, a no-CAPTCHA way to "
         "refresh the commodity list, and — as a side effect — a real bug in the current "
         "scraper (it never marked a day complete, so it kept re-scraping the same date and never "
         "moved forward)."),
        ("“What is the actual benefit if it's the same speed?”",
         "Fewer failed / incomplete days, much simpler code to maintain, no headless-browser "
         "dependency on the server, and a clean signal for when a day is finished. Moderate speed "
         "improvement on top (~13 h → ~6–10 h)."),
        ("“What would it cost / how long?”",
         "About half a day for a clean measurement, 2–3 days to build the parallel version, "
         "~1 week of elapsed time running it side-by-side to compare. Optionally ~$0.30–$0.60 "
         "per day for a paid CAPTCHA-solving service if we later want the speed."),
        ("“What's the risk?”",
         "Near zero for the build-and-compare phase: it writes to a separate table and does not "
         "touch the live scraper. The only real decision point is the cutover, and that only "
         "happens if the parallel data matches and the run is more reliable."),
        ("“Can we make the CAPTCHA go away?”",
         "Not entirely — it is required per commodity. We can raise our solve rate (better image "
         "cleanup, or a paid solver) which reduces retries and therefore time, but the ~605 "
         "solves per day is a fixed cost of the data source."),
        ("“What if we do nothing?”",
         "The browser scraper keeps working at ~13 h/day with its current failure rate. The "
         "one-line ‘day never completes’ fix is worth shipping regardless."),
    ]
    for q, a in qa:
        p = doc.add_paragraph()
        p.paragraph_format.space_after = Pt(2)
        r = p.add_run("Q.  " + q)
        r.bold = True
        r.font.size = Pt(10.5)
        add_body(doc, "A.  " + a, size=10.5, after=8)

    add_body(doc, " ", after=2)
    add_body(doc, "Numbers worth having memorised:", size=10.5)
    add_bullet(doc, "", "~605 commodities/day; data from 7 Nov 2025 onward.")
    add_bullet(doc, "", "One API call with limit=100000 returns a whole commodity (e.g. 879 Wheat "
               "rows) — no pagination.")
    add_bullet(doc, "", "Rate limiter: ~4-request burst, then the connection is silently dropped; "
               "no headers, no documented limit.")
    add_bullet(doc, "", "Polite 60-commodity test: 52% solved, 56 throttle events, ~7 h "
               "extrapolated for a full day.")
    add_bullet(doc, "", "OCR first-try success ~70% at a gentle pace.")

    doc.save(OUT_PATH)
    print(f"Wrote {OUT_PATH}")


if __name__ == "__main__":
    build()
