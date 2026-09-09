"""Probe: can /daily-price-arrival/report return MORE THAN ONE commodity
per call?

Why it matters: the report_ticket is locked to the exact filter set of
the call that minted it (server: "Report filters changed. Please solve
captcha again."). With limit=100000 one call already returns a whole
commodity, so the ticket's 29 spare calls only help if a single query
can span many commodities. If a group-level or all-commodities query
works, one captcha could cover a whole group (or the whole day via
ticket-paged calls).

Tries several "wider" filter shapes, each with its own captcha solve
(3s apart to stay under the rate limit). For each it prints the distinct
commodity names in the response so we can see whether it's multi.

Run on the VM:
  cd /home/azureuser/scraper_csv && source venv/bin/activate
  python3 _vm_scratch/probe_scope.py
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
READER = s._new_ocr_reader()


def req(url, body=None, extra=None):
    h = dict(BASEH)
    if extra:
        h.update(extra)
    data = json.dumps(body).encode() if body is not None else None
    method = "POST" if body is not None else "GET"
    r = urllib.request.Request(url, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(r, timeout=45, context=CTX) as x:
            return x.status, x.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def solved_report(filters):
    """Solve a captcha (up to 8 tries, 3s apart) then POST /report with
    BASE + filters. Return (http, parsed_json, report_ticket)."""
    for attempt in range(1, 9):
        st, txt = req(f"{API}/captcha/generator", {})
        try:
            cap = json.loads(txt)
        except Exception:
            print(f"    captcha gen HTTP {st}: {txt[:150]}")
            time.sleep(3); continue
        k = cap["captcha_key"]
        guess = "".join(READER.readtext(
            s._preprocess_captcha_image(base64.b64decode(cap["captcha_image"])),
            detail=0, allowlist=s._CAPTCHA_ALPHABET))
        body = dict(BASE, **filters, captcha=guess, captcha_key=k)
        st, txt = req(f"{API}/daily-price-arrival/report", body)
        try:
            j = json.loads(txt)
        except Exception:
            j = {"_raw": txt[:300]}
        if j.get("status"):
            tk = (j.get("report_access") or {}).get("report_ticket")
            return st, j, tk
        det = j.get("detail") or j.get("_raw")
        print(f"    try {attempt}: HTTP {st} {det}")
        time.sleep(3)
    return st, j, None


def summarize(tag, st, j):
    if not j.get("status"):
        print(f"{tag}: HTTP {st}  FAIL  {j.get('detail') or j.get('_raw')}")
        return
    recs = (j.get("data") or {}).get("records") or []
    rows = recs[0]["data"] if recs else []
    pag = (recs[0].get("pagination") or [{}])[0] if recs else {}
    names = {}
    for r in rows:
        n = r.get("cmdt_name")
        names[n] = names.get(n, 0) + 1
    print(f"{tag}: HTTP {st}  OK  rows={len(rows)}  total_count={pag.get('total_count')}  "
          f"distinct_commodities={len(names)}")
    top = sorted(names.items(), key=lambda kv: -kv[1])[:12]
    print(f"     commodities: {top}")


TESTS = [
    ("S1 group=1 only (no commodity)", {"group": "1"}),
    ("S2 no group, no commodity", {}),
    ("S3 group=[100000] commodity=[100001] (all sentinels)", {"group": "[100000]", "commodity": "[100001]"}),
    ("S4 group=1 commodity as array [1,4,5]", {"group": "1", "commodity": "[1,4,5]"}),
    ("S5 group=1 commodity='1,4,5' (comma string)", {"group": "1", "commodity": "1,4,5"}),
]


def main():
    multi_ticket = None
    multi_filters = None
    for tag, f in TESTS:
        print(f"\n--- {tag}")
        st, j, tk = solved_report(f)
        summarize(tag, st, j)
        if j.get("status"):
            recs = (j.get("data") or {}).get("records") or []
            rows = recs[0]["data"] if recs else []
            if len({r.get("cmdt_name") for r in rows}) > 1 and multi_ticket is None:
                multi_ticket, multi_filters = tk, f
        time.sleep(3)

    if multi_ticket:
        print(f"\n--- MULTI works. Testing ticket-paging on it: {multi_filters}")
        for pg in ("1", "2"):
            body = dict(BASE, **multi_filters, report_ticket=multi_ticket, page=pg, limit="500")
            st, txt = req(f"{API}/daily-price-arrival/report", body)
            try:
                j = json.loads(txt)
            except Exception:
                j = {"_raw": txt[:300]}
            summarize(f"  ticket page={pg}", st, j)
            time.sleep(3)
    else:
        print("\nNo wider query returned multiple commodities -> 1 captcha per commodity is the floor.")


if __name__ == "__main__":
    main()
