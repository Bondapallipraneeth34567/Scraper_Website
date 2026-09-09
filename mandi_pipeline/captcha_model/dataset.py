"""Dataset + the single image transform shared by training and inference.

Harvested files are RAW captcha PNGs named `<label>__<hex>.png`; the label
is the 6 chars before `__`. The transform must be byte-identical between
train.py and solver.py or the model sees a different distribution at
inference -- so both import `to_tensor` from here.
"""
from __future__ import annotations

import io
import random
from pathlib import Path

import numpy as np
import torch
from PIL import Image
from torch.utils.data import Dataset

from . import CAPTCHA_LEN, CHAR_TO_IDX, IMG_H, IMG_W

_DATA = Path(__file__).parent / "data"


def to_tensor(png_bytes: bytes) -> torch.Tensor:
    """PNG bytes -> float tensor (1, IMG_H, IMG_W), grayscale, [0,1],
    mean/std normalised. No thresholding -- the CNN learns from greyscale."""
    im = Image.open(io.BytesIO(png_bytes)).convert("L").resize((IMG_W, IMG_H), Image.BILINEAR)
    a = np.asarray(im, dtype=np.float32) / 255.0
    a = (a - 0.5) / 0.5
    return torch.from_numpy(a).unsqueeze(0)


def label_to_idx(label: str) -> torch.Tensor:
    return torch.tensor([CHAR_TO_IDX[c] for c in label], dtype=torch.long)


def _augment(t: torch.Tensor) -> torch.Tensor:
    """Light train-time augmentation: small shift + gaussian noise. Kept
    mild -- the real captchas vary little."""
    _, h, w = t.shape
    dx, dy = random.randint(-3, 3), random.randint(-2, 2)
    t = torch.roll(t, shifts=(dy, dx), dims=(1, 2))
    if random.random() < 0.5:
        t = t + torch.randn_like(t) * random.uniform(0.02, 0.08)
    return t.clamp(-1, 1)


class CaptchaDataset(Dataset):
    def __init__(self, files: list[tuple[str, str]], train: bool):
        self.files = files          # (filename, label)
        self.train = train

    def __len__(self) -> int:
        return len(self.files)

    def __getitem__(self, i: int):
        fn, label = self.files[i]
        t = to_tensor((_DATA / fn).read_bytes())
        if self.train:
            t = _augment(t)
        return t, label_to_idx(label)


def load_manifest() -> list[tuple[str, str]]:
    import json
    mf = _DATA / "manifest.jsonl"
    out = []
    for line in mf.open():
        d = json.loads(line)
        if len(d["label"]) == CAPTCHA_LEN and all(c in CHAR_TO_IDX for c in d["label"]) \
                and (_DATA / d["file"]).exists():
            out.append((d["file"], d["label"]))
    return out
