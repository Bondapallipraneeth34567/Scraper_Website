"""CAPTCHA handling for the AGMARKNET direct-API path.

The AGMARKNET CAPTCHA is a plain ~6-character alphanumeric image (no
challenge-response, no behavioural check). This module reads those
characters so the pipeline can fetch data unattended. It does NOT defeat
any anti-bot system beyond reading the glyphs, and the pipeline still
honours the server's rate limit.

Solvers (CAPTCHA_SOLVER=...):

  manual            pause; an authorised human types it in operator_console.py
  easyocr           local easyocr over a preprocessed image
  llm               a multimodal LLM reads the image
  llm_assisted_ocr  easyocr first; if its read is low-confidence / wrong
                    length / cross-check fails, the LLM provides the clean
                    read. This is the recommended automated mode -- easyocr
                    carries the easy majority cheaply, the LLM rescues the
                    ambiguous-glyph cases (0/O, I/l/1, 5/S) easyocr fails on.

A wrong read is cheap: pipeline._process_commodity mints a fresh image and
retries up to _MAX_CAPTCHA_ATTEMPTS, so the solver only needs to be *mostly*
right, not perfect.
"""
from __future__ import annotations

import io
import json
import re
import secrets
import time
from abc import ABC, abstractmethod
from pathlib import Path

import httpx
from loguru import logger

from ..config import settings
from .client import CaptchaChallenge

_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
_KEEP = re.compile(r"[^A-Za-z0-9]")


class CaptchaAborted(Exception):
    """Human (or timeout) chose to skip this commodity for this cycle."""


class CaptchaUnreadable(Exception):
    """No solver could produce a plausible read -- caller should mint a new image."""


# ------------------------------------------------------------------ preprocess
def preprocess_captcha(png_bytes: bytes) -> bytes:
    """Grayscale -> upscale -> denoise -> Otsu threshold -> morphological
    clean. Returns a PNG. Falls back to a plain PIL greyscale+autocontrast
    if OpenCV isn't installed."""
    try:
        import cv2
        import numpy as np

        arr = np.frombuffer(png_bytes, np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_GRAYSCALE)
        if img is None:
            raise ValueError("cv2 could not decode the captcha image")
        img = cv2.resize(img, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
        img = cv2.bilateralFilter(img, 7, 55, 55)
        img = cv2.threshold(img, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2, 2))
        img = cv2.morphologyEx(img, cv2.MORPH_OPEN, kernel, iterations=1)
        img = cv2.morphologyEx(img, cv2.MORPH_CLOSE, kernel, iterations=1)
        ok, buf = cv2.imencode(".png", img)
        if ok:
            return buf.tobytes()
    except Exception as e:  # noqa: BLE001 -- preprocessing is best-effort
        logger.debug(f"captcha preprocess fell back to PIL: {e}")

    from PIL import Image, ImageOps

    im = Image.open(io.BytesIO(png_bytes)).convert("L")
    im = ImageOps.autocontrast(im.resize((im.width * 3, im.height * 3)))
    out = io.BytesIO()
    im.save(out, format="PNG")
    return out.getvalue()


def _clean(text: str) -> str:
    return _KEEP.sub("", text or "")


def _plausible(text: str) -> bool:
    n = settings.captcha_expected_len
    # allow +/-1 in case the true length ever varies; must be alnum only
    return bool(text) and (n - 1) <= len(text) <= (n + 1) and text.isalnum()


# --------------------------------------------------------------------- base
class CaptchaSolver(ABC):
    @abstractmethod
    def solve(self, challenge: CaptchaChallenge, context: dict) -> str:
        """Return the CAPTCHA text, or raise CaptchaAborted / CaptchaUnreadable."""


# --------------------------------------------------------------------- manual
class ManualSolver(CaptchaSolver):
    def __init__(self) -> None:
        self.dir: Path = settings.captcha_state_dir
        self.dir.mkdir(parents=True, exist_ok=True)
        self.timeout = settings.captcha_wait_timeout_sec
        self.poll_sec = 3.0

    def solve(self, challenge: CaptchaChallenge, context: dict) -> str:
        cid = time.strftime("%Y%m%dT%H%M%S") + "-" + secrets.token_hex(3)
        (self.dir / f"{cid}.png").write_bytes(challenge.image_png)
        (self.dir / f"{cid}.json").write_text(json.dumps({
            "id": cid, "status": "pending", "captcha_key": challenge.key,
            "expires_at": challenge.expires_at,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "context": context,
        }, indent=2), encoding="utf-8")
        self._notify(cid, context)
        logger.warning(
            f"⏸  CAPTCHA required for {context.get('commodity_name')!r}. Waiting "
            f"up to {self.timeout:.0f}s. Console: http://{settings.operator_console_host}:"
            f"{settings.operator_console_port}/  id: {cid}"
        )
        deadline = time.monotonic() + self.timeout
        answer_f = self.dir / f"{cid}.answer"
        skip_f = self.dir / f"{cid}.skip"
        reject_f = self.dir / f"{cid}.reject"
        try:
            while time.monotonic() < deadline:
                if answer_f.exists() and answer_f.read_text(encoding="utf-8").strip():
                    ans = answer_f.read_text(encoding="utf-8").strip()
                    logger.info(f"▶  operator solved CAPTCHA {cid} -> {ans!r}")
                    self._mark(self.dir / f"{cid}.json", "solved", answer=ans)
                    return ans
                if skip_f.exists():
                    self._mark(self.dir / f"{cid}.json", "skipped")
                    raise CaptchaAborted(f"operator skipped {cid}")
                if reject_f.exists():
                    self._mark(self.dir / f"{cid}.json", "rejected")
                    raise CaptchaUnreadable(f"operator rejected image {cid}")
                time.sleep(self.poll_sec)
            self._mark(self.dir / f"{cid}.json", "timeout")
            raise CaptchaAborted(f"no operator answer for {cid} within {self.timeout:.0f}s")
        finally:
            for f in (answer_f, skip_f, reject_f):
                f.unlink(missing_ok=True)

    @staticmethod
    def _mark(meta: Path, status: str, **extra) -> None:
        try:
            d = json.loads(meta.read_text(encoding="utf-8"))
        except Exception:
            d = {}
        d.update(status=status, resolved_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **extra)
        meta.write_text(json.dumps(d, indent=2), encoding="utf-8")

    @staticmethod
    def _notify(cid: str, context: dict) -> None:
        if not settings.alert_webhook_url:
            return
        try:
            httpx.post(settings.alert_webhook_url, json={
                "event": "captcha_required", "id": cid,
                "commodity": context.get("commodity_name"),
                "console": f"http://{settings.operator_console_host}:{settings.operator_console_port}/",
            }, timeout=10)
        except Exception as e:
            logger.debug(f"captcha notify webhook failed: {e}")


# --------------------------------------------------------------------- easyocr
class EasyOcrSolver(CaptchaSolver):
    def __init__(self) -> None:
        try:
            import easyocr
        except ImportError as e:
            raise RuntimeError("CAPTCHA_SOLVER=easyocr but easyocr is not installed "
                               "(`pip install easyocr opencv-python-headless`).") from e
        self._reader = easyocr.Reader(["en"], gpu=False, verbose=False)

    def _read(self, png: bytes) -> tuple[str, float]:
        import numpy as np
        from PIL import Image

        img = np.array(Image.open(io.BytesIO(png)).convert("L"))
        results = self._reader.readtext(img, detail=1, allowlist=_ALPHABET,
                                        paragraph=False)
        if not results:
            return "", 0.0
        text = _clean("".join(r[1] for r in results))
        conf = min((float(r[2]) for r in results), default=0.0)
        return text, conf

    def solve(self, challenge: CaptchaChallenge, context: dict) -> str:
        best, best_conf = "", 0.0
        for i in range(max(1, settings.captcha_local_attempts)):
            png = preprocess_captcha(challenge.image_png) if i == 0 else challenge.image_png
            text, conf = self._read(png)
            _debug_save(challenge, text, "easyocr", context, accepted=_plausible(text))
            if _plausible(text) and conf >= best_conf:
                best, best_conf = text, conf
            if _plausible(text) and conf >= 0.6:
                break
        if not _plausible(best):
            raise CaptchaUnreadable(f"easyocr: no plausible read (best={best!r} conf={best_conf:.2f})")
        logger.info(f"easyocr read CAPTCHA -> {best!r} (conf {best_conf:.2f})")
        return best


# --------------------------------------------------------------------- llm
class LlmVisionSolver(CaptchaSolver):
    _PROMPT = (
        "This image is a website CAPTCHA: {n} characters, letters A-Z/a-z and digits 0-9, "
        "no spaces. Reply with ONLY those {n} characters, nothing else. If a character is "
        "genuinely ambiguous, give your single best guess."
    )

    def __init__(self) -> None:
        try:
            import anthropic  # noqa: F401
        except ImportError as e:
            raise RuntimeError("CAPTCHA_SOLVER=llm needs `pip install anthropic` and ANTHROPIC_API_KEY.") from e
        if not settings.anthropic_api_key:
            raise RuntimeError("CAPTCHA_SOLVER=llm but ANTHROPIC_API_KEY is not set.")
        import anthropic

        self._client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        self._model = settings.captcha_llm_model

    def read(self, png_bytes: bytes) -> str:
        import base64

        msg = self._client.messages.create(
            model=self._model,
            max_tokens=64,
            # A glyph read needs no reasoning. Adaptive thinking (Sonnet 5's
            # default) would eat the whole token budget and truncate the
            # answer to an empty string.
            thinking={"type": "disabled"},
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {
                        "type": "base64", "media_type": "image/png",
                        "data": base64.b64encode(png_bytes).decode(),
                    }},
                    {"type": "text", "text": self._PROMPT.format(n=settings.captcha_expected_len)},
                ],
            }],
        )
        if getattr(msg, "stop_reason", None) == "refusal":
            cat = getattr(getattr(msg, "stop_details", None), "category", None)
            raise CaptchaUnreadable(f"LLM declined the CAPTCHA read (category={cat}) -- "
                                    f"not retrying the LLM for this image")
        raw = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
        return _clean(raw)

    def solve(self, challenge: CaptchaChallenge, context: dict) -> str:
        text = self.read(challenge.image_png)
        _debug_save(challenge, text, "llm", context, accepted=_plausible(text))
        if not _plausible(text):
            # one retry on the preprocessed image
            text = self.read(preprocess_captcha(challenge.image_png))
            _debug_save(challenge, text, "llm-prep", context, accepted=_plausible(text))
        if not _plausible(text):
            raise CaptchaUnreadable(f"llm: implausible read {text!r}")
        logger.info(f"llm read CAPTCHA -> {text!r}")
        return text


# ------------------------------------------------------------- llm_assisted_ocr
class LlmAssistedOcrSolver(CaptchaSolver):
    """easyocr for the easy majority; LLM for the ambiguous rest.

    Order:
      1. easyocr on the preprocessed image.
      2. If easyocr is plausible AND high-confidence -> use it.
      3. Otherwise ask the LLM. If the LLM agrees with easyocr -> use it.
         If they differ -> trust the LLM (it handles 0/O, I/l/1, 5/S far
         better) but log the disagreement for tuning.
    """

    def __init__(self) -> None:
        self._ocr = EasyOcrSolver()
        self._llm = LlmVisionSolver()

    def solve(self, challenge: CaptchaChallenge, context: dict) -> str:
        prepped = preprocess_captcha(challenge.image_png)

        ocr_text, ocr_conf = "", 0.0
        try:
            ocr_text, ocr_conf = self._ocr._read(prepped)
        except Exception as e:
            logger.debug(f"llm_assisted_ocr: easyocr step failed: {e}")

        if _plausible(ocr_text) and ocr_conf >= 0.75:
            logger.info(f"captcha via easyocr (fast path) -> {ocr_text!r} (conf {ocr_conf:.2f})")
            _debug_save(challenge, ocr_text, "assisted-ocr-fast", context, accepted=True)
            return ocr_text

        try:
            llm_text = self._llm.read(prepped)
        except Exception as e:
            logger.warning(f"llm_assisted_ocr: LLM step failed ({e}); using easyocr result")
            if _plausible(ocr_text):
                return ocr_text
            raise CaptchaUnreadable(f"both steps failed (ocr={ocr_text!r})") from e

        if not _plausible(llm_text):
            llm_text = self._llm.read(challenge.image_png)  # retry on raw image

        if _plausible(ocr_text) and ocr_text.lower() == llm_text.lower():
            logger.info(f"captcha agreed by easyocr+llm -> {llm_text!r}")
            _debug_save(challenge, llm_text, "assisted-agree", context, accepted=True)
            return llm_text

        if _plausible(llm_text):
            if _plausible(ocr_text):
                logger.info(f"captcha: easyocr {ocr_text!r} vs llm {llm_text!r} -> trusting llm")
            _debug_save(challenge, llm_text, "assisted-llm", context, accepted=True)
            return llm_text

        if _plausible(ocr_text):
            return ocr_text
        raise CaptchaUnreadable(f"no plausible read (ocr={ocr_text!r} llm={llm_text!r})")


# --------------------------------------------------------------------- debug
def _debug_save(challenge: CaptchaChallenge, guess: str, tag: str, context: dict, accepted: bool) -> None:
    if not settings.captcha_debug:
        return
    try:
        d = settings.captcha_state_dir / "debug"
        d.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%dT%H%M%S") + "-" + secrets.token_hex(2)
        safe = _clean(guess) or "EMPTY"
        (d / f"{stamp}_{tag}_{safe}_{'ok' if accepted else 'x'}_raw.png").write_bytes(challenge.image_png)
        (d / f"{stamp}_{tag}_{safe}_{'ok' if accepted else 'x'}_prep.png").write_bytes(
            preprocess_captcha(challenge.image_png))
        with (d / "samples.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": stamp, "tag": tag, "guess": guess,
                                "accepted": accepted, "context": context}) + "\n")
    except Exception as e:
        logger.debug(f"captcha debug save failed: {e}")


# --------------------------------------------------------------------- factory
def build_solver() -> CaptchaSolver:
    kind = settings.captcha_solver
    if kind == "manual":
        return ManualSolver()
    if kind in ("easyocr", "ocr"):
        return EasyOcrSolver()
    if kind == "llm":
        return LlmVisionSolver()
    if kind == "llm_assisted_ocr":
        return LlmAssistedOcrSolver()
    if kind == "trained":
        from ..captcha_model.solver import TrainedCaptchaSolver
        return TrainedCaptchaSolver()
    if kind == "trained_ensemble":
        from ..captcha_model.solver import TrainedEnsembleSolver
        return TrainedEnsembleSolver()
    raise ValueError(f"unknown CAPTCHA_SOLVER={kind!r} "
                     "(manual | easyocr | llm | llm_assisted_ocr | trained | trained_ensemble)")
