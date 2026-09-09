"""Method: a purpose-trained CAPTCHA recogniser for AGMARKNET's 6-char
alphanumeric image.

AGMARKNET's CAPTCHA is one consistent low-noise style, so a small CNN
trained on it reaches ~95-99% -- far past general OCR (easyocr ~70%
first-try) and with no external API.

  harvest.py   mint images from the live /captcha/generator endpoint and
               self-label them by strict multi-preprocessing easyocr
               consensus (no auth, no /report calls)
  dataset.py   torch Dataset over the harvested (image, 6-char label) pairs
  model.py     CnnCaptcha -- shared conv backbone -> 6 independent 62-way heads
  train.py     train -> mandi_pipeline/captcha_model/captcha_cnn.pt
  solver.py    TrainedCaptchaSolver -- plugs into agmarknet.captcha.build_solver
               as CAPTCHA_SOLVER=trained (or trained_ensemble = trained + a
               general-OCR vote as fallback)

Kept in its own folder (not agmarknet/) because it is a distinct method,
reusable and independently trainable.
"""
ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"  # 62, index == class id
CHAR_TO_IDX = {c: i for i, c in enumerate(ALPHABET)}
IDX_TO_CHAR = {i: c for i, c in enumerate(ALPHABET)}
CAPTCHA_LEN = 6
IMG_H, IMG_W = 50, 160  # AGMARKNET captcha is ~200x80; resized to this for the net
