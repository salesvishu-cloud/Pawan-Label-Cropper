"""PNG previews for the web UI (never used for the actual output PDF)."""
from __future__ import annotations

import cv2
import numpy as np
import pymupdf as fitz

from .renderer_4x6 import place_label

COLORS = {  # BGR
    "detected": (60, 160, 40),
    "manual": (200, 110, 30),
    "review": (0, 140, 255),
    "failed": (40, 40, 220),
}


def label_preview_png(src: fitz.Document, pno: int, crop: fitz.Rect, margin: float,
                      auto_rotate: bool, dpi: int = 110, orientation: int = 0) -> bytes:
    tmp = fitz.open()
    place_label(tmp, src, pno, crop, margin, auto_rotate, orientation)
    pix = tmp[0].get_pixmap(dpi=dpi, alpha=False)
    data = pix.tobytes("png")
    tmp.close()
    return data


def page_overlay_png(doc: fitz.Document, pno: int, labels: list, dpi: int = 100) -> bytes:
    page = doc[pno]
    pix = page.get_pixmap(dpi=dpi, alpha=False)
    img = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, pix.n)[:, :, :3]
    img = cv2.cvtColor(img, cv2.COLOR_RGB2BGR).copy()
    z = dpi / 72.0
    ox, oy = page.rect.x0, page.rect.y0
    for i, lbl in enumerate(labels):
        if not lbl.crop:
            continue
        x0, y0, x1, y1 = lbl.crop
        p0 = (int((x0 - ox) * z), int((y0 - oy) * z))
        p1 = (int((x1 - ox) * z), int((y1 - oy) * z))
        col = COLORS.get(lbl.status, (0, 0, 255))
        overlay = img.copy()
        cv2.rectangle(overlay, p0, p1, col, -1)
        img = cv2.addWeighted(overlay, 0.12, img, 0.88, 0)
        cv2.rectangle(img, p0, p1, col, 3)
        tag = f"#{i + 1} {lbl.confidence:.0f}%"
        cv2.putText(img, tag, (p0[0] + 6, max(p0[1] - 8, 18)), cv2.FONT_HERSHEY_SIMPLEX,
                    0.6, col, 2, cv2.LINE_AA)
    ok, buf = cv2.imencode(".png", img)
    return buf.tobytes()
