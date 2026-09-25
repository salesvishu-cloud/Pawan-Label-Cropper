"""Image-analysis helpers (OpenCV + optional Tesseract OCR).

Used only as a fallback when the native PDF analysis cannot find the label,
e.g. for scanned / image-only PDFs, and for trimming blank margins.
Nothing produced here is ever put into the output PDF - the output always
re-uses the original PDF content.
"""
from __future__ import annotations

import functools
import shutil

import cv2
import numpy as np
import pymupdf as fitz

from .pdf_parser import Segment, TextLine


def render_gray(page: fitz.Page, dpi: float, clip: fitz.Rect | None = None):
    """Render to an 8-bit grayscale numpy array. Returns (img, px_per_pt, origin)."""
    clip = fitz.Rect(clip) if clip is not None else fitz.Rect(page.rect)
    zoom = dpi / 72.0
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=clip,
                          colorspace=fitz.csGRAY, alpha=False)
    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width)
    if pix.stride != pix.width:  # pragma: no cover - safety for odd strides
        img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.stride)[:, : pix.width]
    return img.copy(), zoom, fitz.Point(clip.x0, clip.y0)


# --------------------------------------------------------------------------- #
# Border / rule detection on a raster
# --------------------------------------------------------------------------- #
def raster_segments(page: fitz.Page, dpi: int = 150) -> list[Segment]:
    img, z, o = render_gray(page, dpi)
    bw = (img < 150).astype(np.uint8) * 255
    klen = max(25, int(dpi * 0.4))                     # rules longer than 0.4 inch
    max_thick = max(3, int(dpi * 0.04))
    segs: list[Segment] = []
    for orient, ksize in (("h", (klen, 1)), ("v", (1, klen))):
        k = cv2.getStructuringElement(cv2.MORPH_RECT, ksize)
        mask = cv2.morphologyEx(bw, cv2.MORPH_OPEN, k)
        n, _, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
        for i in range(1, n):
            x, y, w, h, _ = stats[i]
            if orient == "h" and h <= max_thick:
                cy = o.y + (y + h / 2) / z
                segs.append(Segment(o.x + x / z, cy, o.x + (x + w) / z, cy, "h", h / z))
            elif orient == "v" and w <= max_thick:
                cx = o.x + (x + w / 2) / z
                segs.append(Segment(cx, o.y + y / z, cx, o.y + (y + h) / z, "v", w / z))
    return segs


def ink_components(page: fitz.Page, dpi: int = 100, join_pt: float = 3.0) -> list[fitz.Rect]:
    """Bounding boxes of ink blobs (letters joined into words/blocks)."""
    img, z, o = render_gray(page, dpi)
    bw = (img < 170).astype(np.uint8) * 255
    k = max(1, int(join_pt * z))
    bw = cv2.dilate(bw, np.ones((k, k), np.uint8))
    n, _, stats, _ = cv2.connectedComponentsWithStats(bw, connectivity=8)
    page_area = page.rect.width * page.rect.height
    out = []
    for i in range(1, n):
        x, y, w, h, _ = stats[i]
        r = fitz.Rect(o.x + x / z, o.y + y / z, o.x + (x + w) / z, o.y + (y + h) / z)
        if r.width * r.height < 0.8 * page_area:
            out.append(r)
    return out


def ink_bbox(page: fitz.Page, rect: fitz.Rect, dpi: int = 110, thresh: int = 200,
             min_px: int = 2) -> fitz.Rect | None:
    """Tight bounding box of the visible ink inside rect (page coordinates)."""
    img, z, o = render_gray(page, dpi, rect)
    ink = img < thresh
    rows = np.where(ink.sum(axis=1) >= min_px)[0]
    cols = np.where(ink.sum(axis=0) >= min_px)[0]
    if rows.size == 0 or cols.size == 0:
        return None
    return fitz.Rect(o.x + cols[0] / z, o.y + rows[0] / z,
                     o.x + (cols[-1] + 1) / z, o.y + (rows[-1] + 1) / z)


# --------------------------------------------------------------------------- #
# OCR
# --------------------------------------------------------------------------- #
@functools.lru_cache(maxsize=1)
def ocr_available() -> bool:
    try:
        import pytesseract  # noqa: F401
    except Exception:
        return False
    if shutil.which("tesseract"):
        return True
    # Common Windows install location
    import os
    for p in (r"C:\Program Files\Tesseract-OCR\tesseract.exe",
              r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"):
        if os.path.exists(p):
            import pytesseract
            pytesseract.pytesseract.tesseract_cmd = p
            return True
    return False


def ocr_page(page: fitz.Page, dpi: int = 400, clip: fitz.Rect | None = None,
             psm: int = 6) -> tuple[list[TextLine], list[tuple]]:
    """OCR (part of) the page locally with Tesseract. Returns (lines, words) in PDF points."""
    if not ocr_available():
        return [], []
    import pytesseract
    img, z, o = render_gray(page, dpi, clip)
    try:
        data = pytesseract.image_to_data(img, config=f"--psm {psm}",
                                         output_type=pytesseract.Output.DICT)
    except Exception:
        return [], []
    lines: dict[tuple, list] = {}
    words: list[tuple] = []
    for i, txt in enumerate(data["text"]):
        txt = (txt or "").strip()
        try:
            conf = float(data["conf"][i])
        except (TypeError, ValueError):
            conf = -1
        if not txt or conf < 20:
            continue
        x, y, w, h = data["left"][i], data["top"][i], data["width"][i], data["height"][i]
        r = fitz.Rect(o.x + x / z, o.y + y / z, o.x + (x + w) / z, o.y + (y + h) / z)
        words.append((r.x0, r.y0, r.x1, r.y1, txt))
        key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        lines.setdefault(key, []).append((r, txt))
    return _lines_from(lines), words


def _lines_from(lines: dict) -> list[TextLine]:
    out = []
    for items in lines.values():
        rect = fitz.Rect(items[0][0])
        for r, _ in items[1:]:
            rect |= r
        out.append(TextLine(rect, " ".join(t for _, t in items)))
    return out


def _merge_word_rows(words: list[tuple]) -> list[TextLine]:
    ws = sorted(words, key=lambda w: ((w[1] + w[3]) / 2, w[0]))
    rows: list[list[tuple]] = []
    for w in ws:
        cy, h = (w[1] + w[3]) / 2, (w[3] - w[1])
        if rows:
            last = rows[-1][-1]
            lcy = (last[1] + last[3]) / 2
            if abs(cy - lcy) < 0.6 * max(h, 1) and 0 <= w[0] - last[2] < 2.5 * max(h, 1):
                rows[-1].append(w)
                continue
        rows.append([w])
    out = []
    for row in rows:
        if len(row) < 2:
            continue
        r = fitz.Rect(row[0][:4])
        for w in row[1:]:
            r |= fitz.Rect(w[:4])
        out.append(TextLine(r, " ".join(w[4] for w in row)))
    return out
