"""Probe: how is a follow-up /report call authorized without solving a
new captcha?

Confirmed so far:
  - POST /v1/daily-price-arrival/report with captcha + captcha_key -> 200,
    rows, and report_access {report_ticket, remaining_calls, max_calls=30}
  - limit="100000" returns the whole commodity in ONE call (no paging)
  - wrong captcha -> HTTP 400

Unknown: how to spend the other 29 calls. This tries every plausible way
and prints the RAW response body for each so the server's own error text
tells us what it wants.

Run on the VM:
  cd /home/azureuser/scraper_csv && source venv/bin/activate
  python3 _vm_scratch/probe_ticket.py
"""
import base64
import json
import ssl
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, "/home/azureuser/scraper_csv")
import agmarknet_scraper as s

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE
API = "https://api.agmarknet.gov.in/v1"
BASEH = {
    "Content-Type": "application/json",
    "Origin": "https://agmarknet.gov.in",
    "Referer": "https://agmarknet.gov.in/",
    "User-Agent": s._USER_AGENT,
    "Accept": "application/json, text/plain, */*",
}

BASE = {
    "from_date": "2026-09-01", "to_date": "2026-09-01", "data_type": "100006",
    "state": "[100000]", "district": "[100001]", "market": "[100002]",
    "grade": "[100003]", "variety": "[100007]", "page": "1", "limit": "100000",
}


def req(url, body=None, extra=None):
    h = dict(BASEH)
    if extra:
        h.update(extra)
    data = json.dumps(body).encode() if body is not None else None
    method = "POST" if body is not None else "GET"
    r = urllib.request.Request(url, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(r, timeout=40, context=CTX) as x:
            return x.status, dict(x.headers), x.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read().decode()


def get_auth():
    """Solve a captcha and make one successful /report call. Return
    (captcha_key, report_ticket, full report_access dict)."""
    reader = s._new_ocr_reader()
    for attempt in range(1, 11):
        st, _, txt = req(f"{API}/captcha/generator", {})
        cap = json.loads(txt)
        k = cap["captcha_key"]
        guess = "".join(reader.readtext(
            s._preprocess_captcha_image(base64.b64decode(cap["captcha_image"])),
            detail=0, allowlist=s._CAPTCHA_ALPHABET))
        b = dict(BASE, group="1", commodity="1", captcha=guess, captcha_key=k)
        st, _, txt = req(f"{API}/daily-price-arrival/report", b)
        try:
            j = json.loads(txt)
        except Exception:
            j = {}
        ok = j.get("status")
        print(f"  captcha attempt {attempt}: OCR={guess!r} HTTP {st} status={ok}")
        if ok:
            ra = j.get("report_access") or {}
            recs = (j.get("data") or {}).get("records") or []
            rows = recs[0]["data"] if recs else []
            print(f"  SUCCESS: rows={len(rows)}  report_access={json.dumps(ra)}")
            return k, ra.get("report_ticket"), ra
        else:
            print(f"    body: {txt[:300]}")
    return None, None, None


def variant(name, body=None, extra=None, qs=""):
    st, hdrs, txt = req(f"{API}/daily-price-arrival/report{qs}", body, extra)
    sc = hdrs.get("Set-Cookie")
    print(f"\n=== {name}")
    print(f"    HTTP {st}" + (f"   Set-Cookie: {sc}" if sc else ""))
    print(f"    {txt[:600]}")


def main():
    print("[1] getting auth (captcha + first report call)")
    key, ticket, ra = get_auth()
    if not key:
        print("!! could not authenticate after 10 tries - stop (OCR luck or rate limit)")
        return
    print(f"\n[2] captcha_key={key}\n    report_ticket={ticket}\n    report_access={json.dumps(ra)}")

    O = dict(BASE, group="6", commodity="23")   # Onion - a DIFFERENT commodity
    W = dict(BASE, group="1", commodity="1")    # Wheat - the SAME query as the auth call

    print("\n[3] follow-up call variants (Onion unless noted) - raw bodies:")
    variant("A  report_ticket in body", dict(O, report_ticket=ticket))
    variant("B  captcha_key + report_ticket in body", dict(O, captcha_key=key, report_ticket=ticket))
    variant("C  captcha_key only (reuse UUID), no captcha", dict(O, captcha_key=key))
    variant("D  captcha=<ticket> + captcha_key=<key>", dict(O, captcha=ticket, captcha_key=key))
    variant("E  captcha_key=<ticket>", dict(O, captcha_key=ticket))
    variant("F  header X-Report-Ticket + Report-Ticket", dict(O), {"X-Report-Ticket": ticket, "Report-Ticket": ticket})
    variant("G  header Authorization: Bearer <ticket>", dict(O), {"Authorization": f"Bearer {ticket}"})
    variant("H  ?report_ticket=<ticket> in query", O, None, f"?report_ticket={ticket}")
    variant("I  ?ticket=<ticket> in query", O, None, f"?ticket={ticket}")
    variant("J  report_ticket in body, SAME query as auth (Wheat)", dict(W, report_ticket=ticket))
    variant("K  report_ticket + page=2 (Wheat, maybe ticket is for paging)", dict(W, report_ticket=ticket, page="2", limit="10"))

    print("\n[4] sanity: does a brand-new captcha call still work right now?")
    k2, t2, ra2 = get_auth()
    print(f"    -> {'yes' if k2 else 'no'}   remaining on that key: {(ra2 or {}).get('remaining_calls')}")


if __name__ == "__main__":
    main()
