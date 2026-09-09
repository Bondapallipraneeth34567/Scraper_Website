"""Dump the FULL response headers on both a normal and a throttled
response, to see if the server states its rate-limit policy
(Retry-After / X-RateLimit-* / RateLimit-*).

Fires 8 quick /captcha/generator calls with no delay to provoke a
throttle, printing every header of each response.

Run on the VM:
  cd /home/azureuser/scraper_csv && source venv/bin/activate
  python3 _vm_scratch/probe_ratehdr.py
"""
import json
import ssl
import time
import urllib.error
import urllib.request

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
H = {"Content-Type": "application/json", "Origin": "https://agmarknet.gov.in",
     "Referer": "https://agmarknet.gov.in/", "User-Agent": UA,
     "Accept": "application/json, text/plain, */*"}
URL = "https://api.agmarknet.gov.in/v1/captcha/generator"

for i in range(1, 9):
    req = urllib.request.Request(URL, data=b"{}", method="POST", headers=H)
    try:
        with urllib.request.urlopen(req, timeout=30, context=CTX) as r:
            status = r.status
            hdrs = dict(r.headers)
            body = r.read().decode()[:120]
    except urllib.error.HTTPError as e:
        status = e.code
        hdrs = dict(e.headers)
        body = e.read().decode()[:200]

    print(f"\n===== call {i}  HTTP {status}")
    for k, v in hdrs.items():
        print(f"   {k}: {v}")
    print(f"   BODY: {body}")
    time.sleep(0.3)
