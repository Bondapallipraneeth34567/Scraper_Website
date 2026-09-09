"""Probe 2 — prove the report_ticket reuse model and measure the real
per-day cost.

Probe 1 confirmed: POST /v1/daily-price-arrival/report returns JSON, and
a captcha-authenticated call hands back
  report_access = {report_ticket, remaining_calls, max_calls(=30), expires_at}

This script:
  1. GET /v1/daily-price-arrival/filters  -> full commodity list
     (cmdt_id, cmdt_name, cmdt_group_id), NO captcha. Saved to
     _vm_scratch/agmarknet_filters.json
  2. ONE browser captcha solve (reusing the production _click_go) to get
     a first report_ticket + capture the exact working /report body.
  3. Browser closed. Then with plain urllib and ONLY the report_ticket
     (no captcha), call /report for ~15 diverse commodities -- printing
     remaining_calls, row counts, total_count/pages and timing after
     each. Tries limit=50000 to see if one call returns the whole set.
     Tries the ticket 3 ways (body field / header / query) and reports
     which the server accepts.
  4. Notes when the ticket hits 0 or expires.
  5. Extrapolates to all 605 commodities.

Run on the VM:
    cd ~/scraper_csv && source venv/bin/activate
    SCRAPER_DIR=$(pwd) python _vm_scratch/inspect_agmarknet_api2.py
Then send back the console output + _vm_scratch/agmarknet_filters.json
"""
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

SCRAPER_DIR = os.environ.get("SCRAPER_DIR") or os.path.expanduser("~/scraper_csv")
sys.path.insert(0, SCRAPER_DIR)
import agmarknet_scraper as s  # noqa: E402

HERE = Path(__file__).parent
API = "https://api.agmarknet.gov.in/v1/daily-price-arrival"
HEADERS = {
    "User-Agent": s._USER_AGENT,
    "Referer": "https://agmarknet.gov.in/",
    "Content-Type": "application/json",
    "Accept": "application/json",
}
_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE


def _req(url, method="GET", body=None, extra_headers=None):
    data = json.dumps(body).encode() if body is not None else None
    h = dict(HEADERS)
    if extra_headers:
        h.update(extra_headers)
    r = urllib.request.Request(url, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(r, timeout=60, context=_CTX) as resp:
            return resp.status, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode() or "{}")
        except Exception:
            return e.code, {"_raw": "unparseable error body"}


def get_filters():
    st, j = _req(f"{API}/filters")
    data = j.get("data", j)
    (HERE / "agmarknet_filters.json").write_text(
        json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    cmdts = data.get("cmdt_data") or data.get("commodity_data") or []
    grps = data.get("cmdt_group_data") or data.get("group_data") or []
    print(f"[filters] HTTP {st}  commodities={len(cmdts)}  groups={len(grps)}  -> agmarknet_filters.json")
    return cmdts


def get_first_ticket():
    """One captcha solve via the real form; capture report_ticket + the
    exact /report request body the site sent."""
    cap = {}

    with sync_playwright() as p:
        b = p.chromium.launch(headless=not os.environ.get("HEADED"),
                              args=["--ignore-certificate-errors"])
        pg = b.new_page(user_agent=s._USER_AGENT, ignore_https_errors=True,
                        viewport={"width": 1400, "height": 1000})

        def on_req(req):
            if req.method == "POST" and req.url.rstrip("/").endswith("/report"):
                try:
                    cap["body"] = json.loads(req.post_data or "{}")
                except Exception:
                    pass

        def on_resp(resp):
            if resp.url.rstrip("/").endswith("/report"):
                try:
                    j = resp.json()
                    ra = j.get("report_access") or j.get("data", {}).get("report_access")
                    if ra:
                        cap["report_access"] = ra
                except Exception:
                    pass

        pg.on("request", on_req)
        pg.on("response", on_resp)

        reader = s._new_ocr_reader()
        s._open_daily_price_form(pg, mode="Both")
        s._pick(pg, "State/UT*", "All States/UTs")
        s._pick(pg, "Commodity Group*", "Cereals")
        s._pick(pg, "Commodity*", "Wheat")
        pg.keyboard.press("Escape")
        pg.wait_for_timeout(400)
        s._click_go(pg, reader)
        pg.wait_for_timeout(2500)
        b.close()

    return cap


def main():
    cmdts = get_filters()

    print("\n[browser] solving ONE captcha to mint a report_ticket ...")
    cap = get_first_ticket()
    if "report_access" not in cap or "body" not in cap:
        print("!! could not capture ticket/body:", json.dumps(cap, indent=2)[:2000])
        return
    ticket = cap["report_access"]["report_ticket"]
    template = cap["body"]
    print(f"[browser] ticket={ticket}  report_access={cap['report_access']}")
    print(f"[browser] captured /report body template keys: {sorted(template)}")

    # --- figure out how the ticket is passed on follow-up calls ---
    base = {k: v for k, v in template.items() if k not in ("captcha", "captcha_key")}
    base["limit"] = "50000"
    base["page"] = "1"

    def try_call(cmdt_id, group_id, mode):
        body = dict(base)
        body["commodity"] = str(cmdt_id)
        body["group"] = str(group_id)
        eh = None
        if mode == "body":
            body["report_ticket"] = ticket
        elif mode == "header":
            eh = {"X-Report-Ticket": ticket, "Report-Ticket": ticket}
        elif mode == "query":
            pass
        url = f"{API}/report" + (f"?report_ticket={ticket}" if mode == "query" else "")
        t0 = time.monotonic()
        st, j = _req(url, "POST", body, eh)
        dt = time.monotonic() - t0
        recs = (j.get("data") or {}).get("records") or []
        rows = recs[0]["data"] if recs else []
        pag = (recs[0].get("pagination") or [{}])[0] if recs else {}
        ra = j.get("report_access") or (j.get("data") or {}).get("report_access") or {}
        return dict(http=st, ok=j.get("status"), msg=j.get("message"), dt=dt,
                    rows=len(rows), total=pag.get("total_count"),
                    pages=pag.get("total_pages"), remaining=ra.get("remaining_calls"))

    probe = cmdts[0]
    print("\n[reuse] testing how the ticket is accepted (no captcha):")
    good_mode = None
    for mode in ("body", "header", "query"):
        r = try_call(probe["cmdt_id"], probe["cmdt_group_id"], mode)
        print(f"   mode={mode:6} -> HTTP {r['http']} ok={r['ok']} msg={r['msg']!r} "
              f"rows={r['rows']} remaining={r['remaining']}")
        if r["ok"]:
            good_mode = mode
            break

    if not good_mode:
        print("\n!! ticket not accepted any of the 3 ways — a fresh captcha may be "
              "required per call after all. Full first response for inspection:")
        r = try_call(probe["cmdt_id"], probe["cmdt_group_id"], "body")
        print(json.dumps(r, indent=2))
        return

    print(f"\n[reuse] ticket accepted via: {good_mode}. Firing a batch, limit=50000:\n")
    sample = cmdts[:18]
    t_start = time.monotonic()
    calls = 0
    for c in sample:
        r = try_call(c["cmdt_id"], c["cmdt_group_id"], good_mode)
        calls += 1
        print(f"  {c['cmdt_name'][:30]:30} HTTP{r['http']} rows={r['rows']:>5} "
              f"total={r['total']} pages={r['pages']} remaining={r['remaining']} "
              f"{r['dt'] * 1000:>5.0f}ms  {r['msg']}")
        if not r["ok"] or r["remaining"] in (0, None):
            print(f"  -> stop: ticket exhausted/rejected after {calls} reuse calls")
            break
        time.sleep(0.4)
    elapsed = time.monotonic() - t_start

    print(f"\n[timing] {calls} reuse calls in {elapsed:.1f}s -> {elapsed / calls * 1000:.0f}ms/call avg")
    per_ticket = 30
    tickets_needed = -(-605 // per_ticket)
    print(f"[extrapolation] 605 commodities / {per_ticket} per ticket = {tickets_needed} captcha solves")
    print(f"   ~{elapsed / calls * 605:.0f}s of HTTP + {tickets_needed} captcha solves "
          f"(~10-30s each) => rough full-day total ~{(elapsed / calls * 605 + tickets_needed * 20) / 60:.0f} min")
    print("   (limit=50000 test: if 'pages' above is always 1, one call == whole commodity)")


if __name__ == "__main__":
    main()
