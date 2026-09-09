"""One-off network probe for the Agmarknet "Daily Price and Arrival
Report" backend — to test the "solve the captcha once, then reuse a
session/ticket for many commodities" idea before building on it.

WHAT IT DOES
  1. Records a full HAR (every request + response, headers + bodies) for
     the whole session -> _vm_scratch/agmarknet_probe.har
  2. Also logs agmarknet.gov.in / api.agmarknet.gov.in calls live to
     _vm_scratch/agmarknet_netlog.jsonl and prints a summary.
  3. Opens the form, fixes State/UT = All States/UTs, then scrapes 3
     DIFFERENT commodities back to back, solving the captcha each time
     with the production OCR path (imported from the real scraper).

WHAT TO LOOK FOR
  * POST .../v1/daily-price-arrival/report  — the request body. Does it
    carry the captcha text as a field, and is that value the SAME across
    all 3 commodities (=> solved once, reused) or fresh each time?
  * A Set-Cookie on the first success, then that cookie replayed on
    calls 2 and 3 with no new captcha  => session-token model.
  * A response field like ticket / token / report_id / request_id /
    jobId, then a separate GET that returns the rows  => async ticket
    model (batchable: one captcha, many commodities).
  * Is the CSV a GET .../report/<id>?format=csv (replayable) or a fresh
    POST?

RUN ON THE VM (fix the path first — see SCRAPER_DIR below):
    cd <scraper dir> && source venv/bin/activate
    python _vm_scratch/inspect_agmarknet_api.py            # headless
    HEADED=1 python _vm_scratch/inspect_agmarknet_api.py   # watch it

Then send back both _vm_scratch/agmarknet_probe.har and
_vm_scratch/agmarknet_netlog.jsonl.
"""
import json
import os
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

# The dir that contains the real agmarknet_scraper.py on the VM. Confirm with:
#   systemctl cat agmarknet-scraper-v2 | grep -E 'WorkingDirectory|ExecStart'
SCRAPER_DIR = os.environ.get("SCRAPER_DIR") or os.path.expanduser("~/scraper_csv")
sys.path.insert(0, SCRAPER_DIR)
import agmarknet_scraper as s  # noqa: E402

HERE = Path(__file__).parent
HAR_OUT = HERE / "agmarknet_probe.har"
LOG_OUT = HERE / "agmarknet_netlog.jsonl"
HOST = "agmarknet.gov.in"  # substring match also catches api.agmarknet.gov.in
COMMODITIES = [
    ("Cereals", "Wheat"),
    ("Vegetables", "Tomato"),
    ("Spices", "Turmeric"),
]
_BODY_CAP = 4000
_TICKETISH = ("ticket", "token", "report_id", "reportid", "request_id",
              "requestid", "txn", "jobid", "job_id", '"ref"', "session")


def _short(t):
    return t if t is None or len(t) <= _BODY_CAP else t[:_BODY_CAP] + f"...(+{len(t) - _BODY_CAP} bytes)"


def main():
    events = []
    fh = LOG_OUT.open("w", encoding="utf-8")

    def _log(kind, **rec):
        rec = {"t": round(time.time(), 3), "kind": kind, **rec}
        events.append(rec)
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        fh.flush()

    def on_request(req):
        if HOST not in req.url:
            return
        try:
            body = req.post_data
        except Exception:
            body = None
        _log("request", method=req.method, url=req.url,
             headers={k.lower(): v for k, v in req.headers.items()
                      if k.lower() in ("cookie", "authorization", "content-type", "referer")
                      or k.lower().startswith("x-")},
             body=_short(body))

    def on_response(resp):
        if HOST not in resp.url:
            return
        text = None
        try:
            ct = resp.headers.get("content-type", "")
            if "json" in ct or "text" in ct or "csv" in ct:
                text = resp.text()
        except Exception:
            pass
        _log("response", status=resp.status, url=resp.url,
             set_cookie=resp.headers.get("set-cookie"),
             content_type=resp.headers.get("content-type"), body=_short(text))

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=not os.environ.get("HEADED"),
            args=["--ignore-certificate-errors"],
        )
        context = browser.new_context(
            viewport={"width": 1400, "height": 1000},
            ignore_https_errors=True,
            user_agent=s._USER_AGENT,
            record_har_path=str(HAR_OUT),
            record_har_content="embed",
        )
        page = context.new_page()
        page.on("request", on_request)
        page.on("response", on_response)

        reader = s._new_ocr_reader()
        s._open_daily_price_form(page, mode="Both")
        s._pick(page, "State/UT*", "All States/UTs")

        for i, (group, commodity) in enumerate(COMMODITIES, 1):
            print(f"\n=== [{i}/{len(COMMODITIES)}] {group} / {commodity} ===")
            _log("marker", note=f"begin {commodity}")
            s._pick(page, "Commodity Group*", group)
            s._pick(page, "Commodity*", commodity)
            page.keyboard.press("Escape")
            page.wait_for_timeout(400)
            t0 = time.monotonic()
            s._click_go(page, reader)
            print(f"    captcha+Go took {time.monotonic() - t0:.1f}s")
            page.wait_for_timeout(1500)
            rows = page.locator("table tbody tr").count()
            print(f"    -> {rows} rows on screen")
            _log("marker", note=f"end {commodity}", rows_on_screen=rows)
            time.sleep(1.5)

        context.close()   # flushes the HAR
        browser.close()

    fh.close()

    # ---------- summary ----------
    print(f"\n\n===== SUMMARY ({len(events)} logged events) =====")
    print(f"HAR  -> {HAR_OUT}")
    print(f"JSONL-> {LOG_OUT}")

    reports = [e for e in events if "daily-price-arrival/report" in e["url"]]
    print(f"\n/daily-price-arrival/report calls: {len(reports)}")
    for e in reports:
        if e["kind"] == "request":
            print(f"  REQ {e['method']}  body={e['body']}")
        else:
            print(f"  RES {e['status']}  set-cookie={bool(e['set_cookie'])}  body={e['body']}")

    hits = [e for e in events if e.get("body") and any(k in e["body"].lower() for k in _TICKETISH)]
    print(f"\nBodies mentioning a ticket/token-ish key: {len(hits)}")
    for e in hits:
        print(f"  {e['kind']:8} {e['url']}\n           {e['body']}")

    cookies = [e for e in events if e.get("set_cookie")]
    print(f"\nSet-Cookie responses: {len(cookies)}")
    for e in cookies:
        print(f"  {e['url']} -> {e['set_cookie']}")

    print("\nDistinct endpoints touched:")
    for u in sorted({e["url"].split("?")[0] for e in events}):
        print(f"  {u}")


if __name__ == "__main__":
    main()
