"""Harvest + self-label AGMARKNET CAPTCHAs for training.

  python -m mandi_pipeline.captcha_model.harvest --target 3000

Loop: POST /v1/captcha/generator {}  (no auth) -> PNG.
Read it 3 ways with easyocr (raw / Otsu / adaptive-threshold). Keep the
image ONLY if all three give the identical 6-char alnum string with min
confidence >= --min-conf. That strict agreement makes the label reliable
without any /report call or human.

Saved: data/<label>__<8hex>.png  +  a line in data/manifest.jsonl
Resumable -- counts existing manifest lines and stops at --target.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import ssl
import time
import urllib.request
from pathlib import Path

_HERE = Path(__file__).parent
_DATA = _HERE / "data"
_MANIFEST = _DATA / "manifest.jsonl"
_LOCK = _HERE.parent / "state" / "pipeline.lock"   # the production sweep's flock


def _sweep_active() -> bool:
    """True while a production sweep holds the single-instance lock -- the
    harvest yields to it so it doesn't add load to the same rate-limited
    CAPTCHA endpoint mid-sweep."""
    if not _LOCK.exists():
        return False
    try:
        import fcntl
    except ImportError:
        return False   # Windows dev box: no gating
    fh = open(_LOCK, "r")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(fh, fcntl.LOCK_UN)
        return False
    except BlockingIOError:
        return True
    finally:
        fh.close()
_API = "https://api.agmarknet.gov.in/v1/captcha/generator"
_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
       "Chrome/124.0.0.0 Safari/537.36")
_ALNUM = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE


def _mint() -> bytes | None:
    import base64
    req = urllib.request.Request(_API, data=b"{}", method="POST", headers={
        "Content-Type": "application/json", "Accept": "application/json",
        "User-Agent": _UA, "Origin": "https://agmarknet.gov.in",
        "Referer": "https://agmarknet.gov.in/",
    })
    try:
        with urllib.request.urlopen(req, timeout=30, context=_CTX) as r:
            j = json.loads(r.read().decode() or "{}")
    except Exception as e:
        print(f"  mint failed: {e}")
        return None
    b64 = j.get("captcha_image") or j.get("image") or ""
    if b64.startswith("data:"):
        b64 = b64.split(",", 1)[1]
    try:
        return base64.b64decode(b64)
    except Exception:
        return None


def _variants(png: bytes):
    import cv2
    import numpy as np
    arr = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_GRAYSCALE)
    if arr is None:
        return []
    up = cv2.resize(arr, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
    den = cv2.bilateralFilter(up, 7, 55, 55)
    otsu = cv2.threshold(den, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]
    adap = cv2.adaptiveThreshold(den, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                 cv2.THRESH_BINARY, 21, 8)
    return [den, otsu, adap]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="captcha_model.harvest")
    ap.add_argument("--target", type=int, default=3000, help="labelled images to accumulate")
    ap.add_argument("--min-conf", type=float, default=0.90)
    ap.add_argument("--gap", type=float, default=2.0, help="seconds between mints")
    ap.add_argument("--max-mints", type=int, default=0, help="hard stop on total mints (0 = none)")
    ap.add_argument("--yield-to-sweep", action="store_true",
                    help="pause whenever a production sweep holds the pipeline lock (use on the VM)")
    args = ap.parse_args(argv)

    _DATA.mkdir(parents=True, exist_ok=True)
    have = 0
    if _MANIFEST.exists():
        have = sum(1 for _ in _MANIFEST.open())
    print(f"have {have} labelled, target {args.target}")
    if have >= args.target:
        print("target already met")
        return 0

    import easyocr
    reader = easyocr.Reader(["en"], gpu=False, verbose=False)

    def read(img):
        res = reader.readtext(img, detail=1, allowlist=_ALNUM, paragraph=False)
        if not res:
            return "", 0.0
        txt = "".join(r[1] for r in res)
        txt = "".join(c for c in txt if c.isalnum())
        conf = min((float(r[2]) for r in res), default=0.0)
        return txt, conf

    mints = kept = 0
    t0 = time.monotonic()
    mf = _MANIFEST.open("a")
    try:
        while have + kept < args.target:
            if args.max_mints and mints >= args.max_mints:
                print(f"hit --max-mints {args.max_mints}")
                break
            if args.yield_to_sweep and _sweep_active():
                print("  production sweep active -> pausing harvest 120s")
                time.sleep(120)
                continue
            png = _mint()
            mints += 1
            if png:
                reads = [read(v) for v in _variants(png)]
                strs = [s for s, c in reads if len(s) == 6 and c >= args.min_conf]
                if len(strs) == 3 and strs[0] == strs[1] == strs[2]:
                    label = strs[0]
                    h = hashlib.sha1(png).hexdigest()[:8]
                    (_DATA / f"{label}__{h}.png").write_bytes(png)
                    mf.write(json.dumps({"file": f"{label}__{h}.png", "label": label}) + "\n")
                    mf.flush()
                    kept += 1
            if (mints % 25) == 0:
                rate = kept / max(mints, 1)
                el = time.monotonic() - t0
                print(f"  mints={mints} kept={kept} ({rate:.0%})  "
                      f"total_labelled={have + kept}  {el/60:.1f}min")
            time.sleep(args.gap)
    finally:
        mf.close()
    print(f"done: +{kept} labelled this run ({have + kept} total) from {mints} mints")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
