"""Probe: can a SUSTAINED run work? Does the rate limiter block a long
sequential job, what's the real OCR hit-rate, and time per commodity?

Pulls the real commodity list from /filters, takes the first N, and for
each: solve captcha (retry up to MAX_CAP tries), POST /report with
limit=100000, record rows/time/attempts/rate-limit hits. Spaces calls
GAP seconds apart. Writes NOTHING to any DB.

Run on the VM:
  cd /home/azureuser/scraper_csv && source venv/bin/activate
  python3 _vm_scratch/probe_burst.py            # default N=40
  N=80 GAP=3 python3 _vm_scratch/probe_burst.py
"""
import base64
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, "/home/azureuser/scraper_csv")
import agmarknet_scraper as s

N = int(os.environ.get("N", "40"))
GAP = float(os.environ.get("GAP", "4"))
MAX_CAP = 8

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE
API = "https://api.agmarknet.gov.in/v1"
BASEH = {
    "Content-Type": "application/json", "Origin": "https://agmarknet.gov.in",
    "Referer": "https://agmarknet.gov.in/", "User-Agent": s._USER_AGENT,
    "Accept": "application/json, text/plain, */*",
}
BASE = {
    "from_date": "2026-09-01", "to_date": "2026-09-01", "data_type": "100006",
    "state": "[100000]", "district": "[100001]", "market": "[100002]",
    "grade": "[100003]", "variety": "[100007]", "page": "1", "limit": "100000",
}
READER = s._new_ocr_reader()


def req(url, body=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data,
                               method="POST" if body is not None else "GET", headers=BASEH)
    try:
        with urllib.request.urlopen(r, timeout=45, context=CTX) as x:
            return x.status, x.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def scrape_one(gid, cid):
    """Returns dict: ok, attempts, ratelimit_hits, rows, total, secs."""
    t0 = time.monotonic()
    rl = 0
    for attempt in range(1, MAX_CAP + 1):
        st, txt = req(f"{API}/captcha/generator", {})
        try:
            cap = json.loads(txt); k = cap["captcha_key"]
        except Exception:
            rl += 1; time.sleep(4); continue
        guess = "".join(READER.readtext(
            s._preprocess_captcha_image(base64.b64decode(cap["captcha_image"])),
            detail=0, allowlist=s._CAPTCHA_ALPHABET))
        body = dict(BASE, group=str(gid), commodity=str(cid), captcha=guess, captcha_key=k)
        st, txt = req(f"{API}/daily-price-arrival/report", body)
        try:
            j = json.loads(txt)
        except Exception:
            j = {}
        if j.get("status"):
            recs = (j.get("data") or {}).get("records") or []
            rows = recs[0]["data"] if recs else []
            pag = (recs[0].get("pagination") or [{}])[0] if recs else {}
            return dict(ok=True, attempts=attempt, rl=rl, rows=len(rows),
                        total=pag.get("total_count"), secs=time.monotonic() - t0)
        det = (j.get("detail") or j.get("message") or "")
        if "Too many" in det:
            rl += 1; time.sleep(5)
        else:
            time.sleep(1.5)
    return dict(ok=False, attempts=MAX_CAP, rl=rl, rows=0, total=None, secs=time.monotonic() - t0)


def main():
    st, txt = req(f"{API}/daily-price-arrival/filters")
    cmdts = json.loads(txt)["data"]["cmdt_data"]
    sample = cmdts[:N]
    print(f"burst: {len(sample)} commodities, gap={GAP}s, max {MAX_CAP} captcha tries each\n")

    ok = fail = tot_attempts = tot_rl = with_data = 0
    t_start = time.monotonic()
    for i, c in enumerate(sample, 1):
        r = scrape_one(c["cmdt_group_id"], c["cmdt_id"])
        ok += r["ok"]; fail += (not r["ok"])
        tot_attempts += r["attempts"]; tot_rl += r["rl"]
        with_data += 1 if (r["ok"] and r["rows"]) else 0
        print(f"{i:3}/{len(sample)} {c['cmdt_name'][:28]:28} "
              f"{'OK ' if r['ok'] else 'FAIL'} cap_tries={r['attempts']} rl={r['rl']} "
              f"rows={r['rows']:>5} total={r['total']} {r['secs']:.1f}s")
        time.sleep(GAP)
    elapsed = time.monotonic() - t_start

    print(f"\n==== SUMMARY ====")
    print(f"ok={ok}  fail={fail}  had_rows={with_data}")
    print(f"total captcha attempts={tot_attempts}  avg per commodity={tot_attempts/len(sample):.2f}")
    print(f"rate-limit hits={tot_rl}")
    print(f"elapsed={elapsed:.0f}s  avg per commodity={elapsed/len(sample):.1f}s")
    print(f"extrapolate 605 commodities: {elapsed/len(sample)*605/60:.0f} min "
          f"({elapsed/len(sample)*605/3600:.1f} h)")


if __name__ == "__main__":
    main()
