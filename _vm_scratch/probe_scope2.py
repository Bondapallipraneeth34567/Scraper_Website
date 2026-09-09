"""Probe: raw response bodies for 'wide' /daily-price-arrival/report
queries (all-sentinels, multi-commodity). Previous run showed these
return HTTP 200 but with a shape the summarizer didn't recognize -- so
here we just solve ONE captcha per form and dump the raw body.

Gentle on the rate limiter: <=12 captcha tries per form at 5s apart,
12s between forms.

Run on the VM:
  cd /home/azureuser/scraper_csv && source venv/bin/activate
  python3 _vm_scratch/probe_scope2.py
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


def req(url, body=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data,
                               method="POST" if body is not None else "GET", headers=BASEH)
    try:
        with urllib.request.urlopen(r, timeout=45, context=CTX) as x:
            return x.status, x.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def one_captcha():
    for attempt in range(1, 13):
        st, txt = req(f"{API}/captcha/generator", {})
        try:
            cap = json.loads(txt)
            k = cap["captcha_key"]
        except Exception:
            print(f"    gen HTTP {st}: {txt[:120]}")
            time.sleep(5); continue
        guess = "".join(READER.readtext(
            s._preprocess_captcha_image(base64.b64decode(cap["captcha_image"])),
            detail=0, allowlist=s._CAPTCHA_ALPHABET))
        return k, guess, attempt
    return None, None, None


def run(tag, filters):
    print(f"\n================ {tag}")
    k, guess, att = one_captcha()
    if not k:
        print("   no captcha obtained"); return
    body = dict(BASE, **filters, captcha=guess, captcha_key=k)
    print(f"   captcha try#{att} OCR={guess!r}   body filters: {filters}")
    st, txt = req(f"{API}/daily-price-arrival/report", body)
    print(f"   HTTP {st}")
    print("   " + txt[:1800].replace("\n", "\n   "))
    # if JSON with records, count distinct commodities
    try:
        j = json.loads(txt)
        recs = (j.get("data") or {}).get("records") or []
        rows = recs[0]["data"] if recs else []
        names = sorted({r.get("cmdt_name") for r in rows})
        pag = (recs[0].get("pagination") or [{}])[0] if recs else {}
        print(f"\n   >>> parsed: status={j.get('status')} rows={len(rows)} "
              f"distinct_commodities={len(names)} total_count={pag.get('total_count')}")
        if names:
            print(f"   >>> commodity names: {names[:25]}")
    except Exception as e:
        print(f"   (not parseable JSON: {e})")


def main():
    run("W1 all-sentinels: group=[100000] commodity=[100001]",
        {"group": "[100000]", "commodity": "[100001]"})
    time.sleep(12)
    run("W2 multi array: group=1 commodity=[1,4,5]",
        {"group": "1", "commodity": "[1,4,5]"})
    time.sleep(12)
    run("W3 group array + all commodity: group=[1] commodity=[100001]",
        {"group": "[1]", "commodity": "[100001]"})
    time.sleep(12)
    run("W4 no commodity key, group=[1]",
        {"group": "[1]"})


if __name__ == "__main__":
    main()
