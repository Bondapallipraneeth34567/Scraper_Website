"""Probe: is the /report captcha case-insensitive, and how much does
that help OCR?

For each fresh captcha it submits the OCR guess THREE ways against the
same captcha_key:
  1. as-is         (what OCR read)
  2. lower-cased
  3. upper-cased
and records which of them the server accepts. If lower/upper succeed
where as-is fails, the captcha is case-insensitive and we can normalise
OCR output -> far fewer rejections.

NOTE: a captcha_key is single-use once accepted, so we submit the three
forms only until one succeeds; if as-is already succeeds we still learn
nothing about case, so we track "as-is failed but lower/upper worked".

Run on the VM:
  cd /home/azureuser/scraper_csv && source venv/bin/activate
  python3 _vm_scratch/probe_caseinsensitive.py
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
    "Content-Type": "application/json", "Origin": "https://agmarknet.gov.in",
    "Referer": "https://agmarknet.gov.in/", "User-Agent": s._USER_AGENT,
    "Accept": "application/json, text/plain, */*",
}
BASE = {
    "from_date": "2026-09-01", "to_date": "2026-09-01", "data_type": "100006",
    "group": "1", "commodity": "1", "state": "[100000]", "district": "[100001]",
    "market": "[100002]", "grade": "[100003]", "variety": "[100007]",
    "page": "1", "limit": "10",
}
READER = s._new_ocr_reader()
ROUNDS = 12


def req(url, body=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data,
                               method="POST" if body is not None else "GET", headers=BASEH)
    try:
        with urllib.request.urlopen(r, timeout=45, context=CTX) as x:
            return x.status, x.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def submit(key, value):
    st, txt = req(f"{API}/daily-price-arrival/report", dict(BASE, captcha=value, captcha_key=key))
    try:
        j = json.loads(txt)
    except Exception:
        j = {}
    return j.get("status") is True, (j.get("detail") or j.get("message") or "")[:60]


def main():
    asis_ok = lower_rescue = upper_rescue = both_fail = gen_fail = 0
    for rnd in range(1, ROUNDS + 1):
        st, txt = req(f"{API}/captcha/generator", {})
        try:
            cap = json.loads(txt); key = cap["captcha_key"]
        except Exception:
            gen_fail += 1
            print(f"{rnd:2}: captcha gen failed: {txt[:80]}")
            time.sleep(6); continue
        guess = "".join(READER.readtext(
            s._preprocess_captcha_image(base64.b64decode(cap["captcha_image"])),
            detail=0, allowlist=s._CAPTCHA_ALPHABET))

        ok1, d1 = submit(key, guess)
        if ok1:
            asis_ok += 1
            print(f"{rnd:2}: OCR={guess!r:12} as-is OK  (no case info)")
            time.sleep(5); continue

        ok2, d2 = submit(key, guess.lower())
        ok3 = False
        if not ok2:
            ok3, d3 = submit(key, guess.upper())

        if ok2:
            lower_rescue += 1
            print(f"{rnd:2}: OCR={guess!r:12} as-is FAIL -> lower {guess.lower()!r} OK  ** case-insensitive **")
        elif ok3:
            upper_rescue += 1
            print(f"{rnd:2}: OCR={guess!r:12} as-is FAIL -> UPPER {guess.upper()!r} OK  ** case-insensitive **")
        else:
            both_fail += 1
            print(f"{rnd:2}: OCR={guess!r:12} all 3 FAIL ({d1})")
        time.sleep(5)

    print("\n==== SUMMARY ====")
    print(f"rounds={ROUNDS}  gen_fail={gen_fail}")
    print(f"as-is accepted            : {asis_ok}")
    print(f"rescued by lower-casing   : {lower_rescue}")
    print(f"rescued by upper-casing   : {upper_rescue}")
    print(f"still failed all 3 forms  : {both_fail}")
    if lower_rescue or upper_rescue:
        print("\n=> captcha is CASE-INSENSITIVE. Normalising OCR case will cut rejections a lot.")
    elif asis_ok and not (lower_rescue or upper_rescue):
        print("\n=> inconclusive/likely CASE-SENSITIVE (no lower/upper rescue seen).")


if __name__ == "__main__":
    main()
