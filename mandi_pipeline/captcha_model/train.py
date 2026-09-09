"""Train SegCaptcha (CNN -> 6 per-position heads) on the harvested data.

  python -m mandi_pipeline.captcha_model.train --epochs 60

Reports per-character and full-string val accuracy each epoch; saves the
best full-string checkpoint to captcha_model/captcha_cnn.pt.
"""
from __future__ import annotations

import argparse
import random
import time
from pathlib import Path

import torch
from torch.utils.data import DataLoader

from . import ALPHABET, CAPTCHA_LEN, IMG_H, IMG_W
from .dataset import CaptchaDataset, load_manifest
from .model import N_CLASSES, SegCaptcha

_CKPT = Path(__file__).parent / "captcha_cnn.pt"


@torch.no_grad()
def evaluate(model, loader, device) -> tuple[float, float]:
    model.eval()
    char_ok = char_tot = str_ok = str_tot = 0
    for x, y in loader:
        pred = model(x.to(device)).argmax(-1).cpu()   # (B,6)
        char_ok += (pred == y).sum().item()
        char_tot += y.numel()
        str_ok += (pred == y).all(dim=1).sum().item()
        str_tot += y.size(0)
    return char_ok / max(char_tot, 1), str_ok / max(str_tot, 1)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="captcha_model.train")
    ap.add_argument("--epochs", type=int, default=60)
    ap.add_argument("--batch", type=int, default=64)
    ap.add_argument("--lr", type=float, default=1e-3)
    ap.add_argument("--val-frac", type=float, default=0.12)
    ap.add_argument("--seed", type=int, default=1234)
    args = ap.parse_args(argv)

    rows = load_manifest()
    if len(rows) < 200:
        print(f"only {len(rows)} labelled images -- harvest more (aim >= 1500).")
        return 1
    random.Random(args.seed).shuffle(rows)
    n_val = max(60, int(len(rows) * args.val_frac))
    val_rows, train_rows = rows[:n_val], rows[n_val:]
    print(f"{len(rows)} images -> train {len(train_rows)} / val {len(val_rows)}  (Seg 6-head)", flush=True)

    device = "cuda" if torch.cuda.is_available() else "cpu"
    tr = DataLoader(CaptchaDataset(train_rows, train=True), batch_size=args.batch,
                    shuffle=True, drop_last=True)
    va = DataLoader(CaptchaDataset(val_rows, train=False), batch_size=128)

    model = SegCaptcha(n_classes=N_CLASSES).to(device)
    opt = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=1e-4)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max=args.epochs)
    lossf = torch.nn.CrossEntropyLoss()

    best = 0.0
    for ep in range(1, args.epochs + 1):
        model.train()
        t0 = time.monotonic()
        tot = 0.0
        for x, y in tr:
            x, y = x.to(device), y.to(device)
            logits = model(x)                                    # (B,6,62)
            loss = sum(lossf(logits[:, i, :], y[:, i]) for i in range(CAPTCHA_LEN))
            opt.zero_grad(); loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 5.0)
            opt.step()
            tot += loss.item()
        sched.step()
        ca, sa = evaluate(model, va, device)
        print(f"ep {ep:2d}/{args.epochs}  loss {tot/len(tr):.3f}  "
              f"val char {ca:.3f}  val string {sa:.3f}  ({time.monotonic()-t0:.0f}s)", flush=True)
        if sa >= best:
            best = sa
            torch.save({
                "arch": "seg6", "state_dict": model.state_dict(),
                "alphabet": ALPHABET, "n_classes": N_CLASSES,
                "img_h": IMG_H, "img_w": IMG_W, "captcha_len": CAPTCHA_LEN,
                "val_string_acc": sa, "val_char_acc": ca, "n_train": len(train_rows),
            }, _CKPT)

    print(f"\nbest val string accuracy: {best:.3f}  -> {_CKPT}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
