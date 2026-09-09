"""Probe: does a POLITE sustained run avoid the rate-limit wall?

Strategy (opposite of probe_burst.py):
  - ONE captcha attempt per commodity per pass (no aggressive inner retry)
  - GAP seconds between commodities
  - on "Too many requests": sleep COOLDOWN, then ONE more try for that item
  - up to PASSES sweeps over whatever still failed
  - client read-timeout is caught, not fatal

Writes NOTHING. Measures: pass-1 hit rate, recovery across passes,
throttle events, seconds/commodity, 605 extrapolation.

Run on the VM:
  cd /home/azureuser/scraper_csv && source venv/bin/activate
  python3 _vm_scratch/probe_polite.py                 # N=60 GAP=6 PASSES=3
  N=120 GAP=5 python3 _vm_scratch/probe_polite.py
"""
import base64
import json
import os
import socket
import ssl
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, "/home/azureuser/scraper_csv")
import agmarknet_scraper as s

N = int(os.environ.get("N", "60"))
GAP = float(os.environ.get("GAP", "6"))
PASSES = int(os.environ.get("PASSES", "3"))
COOLDOWN = float(os.environ.get("COOLDOWN", "30"))

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
throttles = 0
timeouts = 0


def req(url, body=None):
    global timeouts
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data,
                               method="POST" if body is not None else "GET", headers=BASEH)
    try:
        with urllib.request.urlopen(r, timeout=45, context=CTX) as x:
            return x.status, x.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()
    except (TimeoutError, socket.timeout, urllib.error.URLError) as e:
        timeouts += 1
        return 0, json.dumps({"detail": f"client error: {e}"})


def attempt(gid, cid):
    """One shot. Returns ('ok', rows, total) | ('throttled', ...) | ('badcaptcha', ...) | ('err', msg)."""
    global throttles
    st, txt = req(f"{API}/captcha/generator", {})
    try:
        cap = json.loads(txt); k = cap["captcha_key"]
    except Exception:
        if "Too many" in txt:
            throttles += 1; return ("throttled", 0, None)
        return ("err", txt[:80])
    guess = "".join(READER.readtext(
        s._preprocess_captcha_image(base64.b64decode(cap["captcha_image"])),
        detail=0, allowlist=s._CAPTCHA_ALPHABET))
    st, txt = req(f"{API}/daily-price-arrival/report",
                  dict(BASE, group=str(gid), commodity=str(cid), captcha=guess, captcha_key=k))
    try:
        j = json.loads(txt)
    except Exception:
        return ("err", txt[:80])
    if j.get("status"):
        recs = (j.get("data") or {}).get("records") or []
        rows = recs[0]["data"] if recs else []
        pag = (recs[0].get("pagination") or [{}])[0] if recs else {}
        return ("ok", len(rows), pag.get("total_count"))
    det = j.get("detail") or j.get("message") or ""
    if "Too many" in det:
        throttles += 1; return ("throttled", 0, None)
    return ("badcaptcha", 0, det[:50])


def main():
    st, txt = req(f"{API}/daily-price-arrival/filters")
    cmdts = json.loads(txt)["data"]["cmdt_data"][:N]
    print(f"polite: N={N} gap={GAP}s passes={PASSES} cooldown={COOLDOWN}s\n")

    done = {}
    todo = [(c["cmdt_group_id"], c["cmdt_id"], c["cmdt_name"]) for c in cmdts]
    t0 = time.monotonic()
    per_pass = []

    for p in range(1, PASSES + 1):
        if not todo:
            break
        got = 0
        nxt = []
        print(f"--- pass {p}: {len(todo)} to try")
        for gid, cid, nm in todo:
            res = attempt(gid, cid)
            tag = res[0]
            if tag == "ok":
                done[(gid, cid)] = res
                got += 1
            elif tag == "throttled":
                print(f"    throttled on {nm[:24]} -> cooldown {COOLDOWN}s")
                time.sleep(COOLDOWN)
                res2 = attempt(gid, cid)
                if res2[0] == "ok":
                    done[(gid, cid)] = res2; got += 1
                else:
                    nxt.append((gid, cid, nm))
            else:
                nxt.append((gid, cid, nm))
            time.sleep(GAP)
        per_pass.append((p, len(todo), got))
        print(f"--- pass {p}: +{got} ok  ({len(done)}/{N} total)")
        todo = nxt

    elapsed = time.monotonic() - t0
    with_rows = sum(1 for v in done.values() if v[1])
    print(f"\n==== SUMMARY ====")
    for p, tried, got in per_pass:
        print(f"  pass {p}: {got}/{tried} ok")
    print(f"final: {len(done)}/{N} solved  ({with_rows} had rows)  still-missing={N - len(done)}")
    print(f"throttle events={throttles}  client timeouts={timeouts}")
    print(f"elapsed={elapsed:.0f}s  per commodity={elapsed/N:.1f}s")
    print(f"extrapolate 605: {elapsed/N*605/60:.0f} min ({elapsed/N*605/3600:.1f} h)")
    if todo:
        print("still missing:", [t[2] for t in todo][:30])


if __name__ == "__main__":
    main()
