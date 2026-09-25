"""Places a cropped label on an exact 4 x 6 inch (288 x 432 pt) PDF page.

The label is embedded as original PDF content (vector text, vector rules and
the original barcode/QR images) via ``show_pdf_page`` - it is NOT turned into
a screenshot.  It is scaled uniformly (never stretched), rotated only when the
label is landscape, and centred.
"""
from __future__ import annotations

import pymupdf as fitz

from ..config import OUT_HEIGHT_PT, OUT_WIDTH_PT


def fit_rect(w: float, h: float, margin: float) -> tuple[fitz.Rect, float]:
    avail_w = OUT_WIDTH_PT - 2 * margin
    avail_h = OUT_HEIGHT_PT - 2 * margin
    s = min(avail_w / w, avail_h / h)
    tw, th = w * s, h * s
    cx, cy = OUT_WIDTH_PT / 2, OUT_HEIGHT_PT / 2
    return fitz.Rect(cx - tw / 2, cy - th / 2, cx + tw / 2, cy + th / 2), s


def place_label(out: fitz.Document, src: fitz.Document, pno: int, clip: fitz.Rect,
                margin_pt: float = 4.0, auto_rotate: bool = True, base_rotate: int = 0) -> dict:
    """base_rotate: counter-clockwise turn (0/90/180/270) that makes the label's
    text upright (from orientation detection)."""
    page = out.new_page(width=OUT_WIDTH_PT, height=OUT_HEIGHT_PT)
    rotate = base_rotate % 360
    w, h = (clip.height, clip.width) if rotate in (90, 270) else (clip.width, clip.height)
    if auto_rotate and w > h * 1.05:        # landscape label -> turn to portrait
        rotate = (rotate + 90) % 360
        w, h = h, w
    target, scale = fit_rect(w, h, max(0.0, margin_pt))
    page.show_pdf_page(target, src, pno, clip=clip, rotate=rotate, keep_proportion=True)
    return {
        "scale": round(scale, 4),
        "rotated": rotate,
        "target": [round(v, 2) for v in target],
        "printed_size_in": [round(target.width / 72, 2), round(target.height / 72, 2)],
    }
