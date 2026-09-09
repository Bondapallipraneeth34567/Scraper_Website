"""TrainedCaptchaSolver -- loads captcha_cnn.pt (CRNN+CTC) and reads the image.

Plugs into agmarknet.captcha.build_solver():
  CAPTCHA_SOLVER=trained            -> this alone; raises CaptchaUnreadable
                                      on a low-confidence / wrong-length read
                                      so the pipeline mints a fresh image.
  CAPTCHA_SOLVER=trained_ensemble   -> this, with easyocr as a tie-breaker /
                                      fallback when the model is unsure.
"""
from __future__ import annotations

from pathlib import Path

import torch
from loguru import logger

from ..agmarknet.captcha import (
    CaptchaChallenge, CaptchaSolver, CaptchaUnreadable, _clean, _plausible,
    preprocess_captcha,
)
from ..config import settings
from . import CAPTCHA_LEN
from .dataset import to_tensor
from .model import SegCaptcha, decode

_CKPT = Path(__file__).parent / "captcha_cnn.pt"


def _load_model():
    if not _CKPT.exists():
        raise RuntimeError(
            f"{_CKPT} is missing -- run `python -m mandi_pipeline.captcha_model.harvest` "
            f"then `python -m mandi_pipeline.captcha_model.train`."
        )
    ck = torch.load(_CKPT, map_location="cpu")
    m = SegCaptcha(n_classes=ck["n_classes"])
    m.load_state_dict(ck["state_dict"])
    m.eval()
    return m, ck


class TrainedCaptchaSolver(CaptchaSolver):
    def __init__(self, *, gate: bool = True) -> None:
        self._model, ck = _load_model()
        self._gate = gate
        self.min_conf = settings.captcha_trained_min_conf
        logger.info(f"trained CAPTCHA model loaded (val string acc {ck.get('val_string_acc', '?')}, "
                    f"n_train {ck.get('n_train', '?')}, min_conf {self.min_conf})")

    @torch.no_grad()
    def predict(self, png: bytes) -> tuple[str, float]:
        logits = self._model(to_tensor(png).unsqueeze(0))   # (1, 6, 62)
        strings, conf = decode(logits)
        return _clean(strings[0]), float(conf[0])

    def solve(self, challenge: CaptchaChallenge, context: dict) -> str:
        text, conf = self.predict(challenge.image_png)
        if _plausible(text) and (not self._gate or conf >= self.min_conf):
            logger.info(f"trained model read CAPTCHA -> {text!r} (conf {conf:.2f})")
            return text
        raise CaptchaUnreadable(f"trained model unsure: {text!r} (conf {conf:.2f}, "
                                f"len {len(text)})")


class TrainedEnsembleSolver(CaptchaSolver):
    """Trained model first; if it's not confident, ask easyocr and take that
    read when the two agree or easyocr is itself confident."""

    def __init__(self) -> None:
        self._trained = TrainedCaptchaSolver(gate=False)
        from ..agmarknet.captcha import EasyOcrSolver
        self._ocr = EasyOcrSolver()

    def solve(self, challenge: CaptchaChallenge, context: dict) -> str:
        text, conf = self._trained.predict(challenge.image_png)
        if _plausible(text) and conf >= settings.captcha_trained_min_conf:
            logger.info(f"captcha via trained model -> {text!r} (conf {conf:.2f})")
            return text
        try:
            ocr_text, ocr_conf = self._ocr._read(preprocess_captcha(challenge.image_png))
        except Exception:
            ocr_text, ocr_conf = "", 0.0
        if _plausible(text) and text.lower() == ocr_text.lower():
            logger.info(f"captcha: trained+easyocr agree -> {text!r}")
            return text
        if _plausible(ocr_text) and ocr_conf >= 0.6:
            logger.info(f"captcha: trained unsure ({conf:.2f}) -> easyocr {ocr_text!r}")
            return ocr_text
        if _plausible(text):
            return text
        raise CaptchaUnreadable(f"trained {text!r}/{conf:.2f} + easyocr {ocr_text!r} both weak")
