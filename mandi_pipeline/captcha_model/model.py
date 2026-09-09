"""SegCaptcha -- a CNN that keeps the WIDTH axis, then pools it into
exactly CAPTCHA_LEN (6) columns, one per character position, and puts a
62-way head on each column.

This suits a fixed-length captcha better than CTC: alignment is explicit
(column k <-> character k), there is no blank class to collapse onto, and
it trains cleanly on a small set. The earlier 6-head design failed only
because it flattened an over-pooled feature blob; here AdaptiveAvgPool2d
((1, 6)) does the position segmentation directly.

forward(x: (B,1,H,W)) -> logits (B, 6, 62)
"""
from __future__ import annotations

import torch
import torch.nn as nn

from . import ALPHABET, CAPTCHA_LEN

N_CLASSES = len(ALPHABET)   # 62, no blank


def _cb(cin, cout, pool):
    layers = [nn.Conv2d(cin, cout, 3, padding=1), nn.BatchNorm2d(cout), nn.ReLU(inplace=True),
              nn.Conv2d(cout, cout, 3, padding=1), nn.BatchNorm2d(cout), nn.ReLU(inplace=True)]
    if pool:
        layers.append(nn.MaxPool2d(pool))
    return nn.Sequential(*layers)


class SegCaptcha(nn.Module):
    def __init__(self, n_classes: int = N_CLASSES):
        super().__init__()
        self.cnn = nn.Sequential(
            _cb(1, 64, (2, 2)),      # H/2   W/2
            _cb(64, 128, (2, 2)),    # H/4   W/4
            _cb(128, 256, (2, 2)),   # H/8   W/8
            _cb(256, 256, (2, 1)),   # H/16  W/8
        )
        # -> (B, 256, ~3, W/8); pool to (B, 256, 1, 6): one column per char
        self.seg = nn.AdaptiveAvgPool2d((1, CAPTCHA_LEN))
        self.head = nn.Sequential(
            nn.Dropout(0.3),
            nn.Linear(256, 256), nn.ReLU(inplace=True), nn.Dropout(0.3),
            nn.Linear(256, n_classes),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        f = self.seg(self.cnn(x))            # (B, 256, 1, 6)
        f = f.squeeze(2).permute(0, 2, 1)   # (B, 6, 256)
        return self.head(f)                 # (B, 6, 62)


def decode(logits: torch.Tensor) -> tuple[list[str], torch.Tensor]:
    """logits (B,6,62) -> (strings, per-sample min-position confidence)."""
    from . import IDX_TO_CHAR
    p = logits.softmax(-1)
    conf, idx = p.max(-1)                    # (B,6)
    strings = ["".join(IDX_TO_CHAR[int(i)] for i in row) for row in idx]
    return strings, conf.min(dim=1).values
