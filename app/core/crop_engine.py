"""Crop engine: turns a detection into a final crop rectangle and prepares a
clean single-page source containing ONLY the label content.
"""
from __future__ import annotations

import numpy as np
import pymupdf as fitz

from .raster import ink_bbox, render_gray


def crop_rect_for(page: fitz.Page, rect: fitz.Rect, border: bool, unit: float,
                  line_thickness: float, padding_pt: float, trim: bool = True) -> fitz.Rect:
    """Final crop: detected boundary + half border width + padding, blank margins trimmed."""
    r = fitz.Rect(rect)
    half = max(line_thickness, 0.5) / 2 if border else 0.0
    r = fitz.Rect(r.x0 - half, r.y0 - half, r.x1 + half, r.y1 + half)
    if trim:
        # Clean unwanted white margins: shrink to the ink actually present.
        # Only ever shrinks, so no label ink can be cut off.
        ink = ink_bbox(page, r & page.rect)
        if ink is not None:
            r = fitz.Rect(max(r.x0, ink.x0), max(r.y0, ink.y0),
                          min(r.x1, ink.x1), min(r.y1, ink.y1))
    # padding is specified in OUTPUT points: convert with the scale the label
    # will get on the 4x6 page (portrait or rotated-landscape, whichever fits)
    w, h = max(r.width, 1.0), max(r.height, 1.0)
    scale = max(min(288.0 / w, 432.0 / h), min(288.0 / h, 432.0 / w))
    pad = padding_pt / scale
    r = fitz.Rect(r.x0 - pad, r.y0 - pad, r.x1 + pad, r.y1 + pad)
    return r & page.rect


def content_cut_by(page: fitz.Page, page_words, crop: fitz.Rect, unit: float,
                   tol: float = 0.6) -> list[str]:
    """What the crop edge would slice through:
    * words of the text layer that are only partly inside the crop, and
    * visible ink touching the crop edge from outside (catches barcodes, QR,
      images and rules, and ignores anything that is clipped/hidden)."""
    cut = []
    inner = fitz.Rect(crop.x0 - tol, crop.y0 - tol, crop.x1 + tol, crop.y1 + tol)
    for w in page_words:
        r = fitz.Rect(w[:4])
        if r.is_empty or not r.intersects(crop):
            continue
        if not inner.contains(r):
            inter = fitz.Rect(r) & crop
            if inter.get_area() > 0.15 * r.get_area():
                cut.append(w[4])
    band = 1.2 * unit
    off = 0.4 * unit
    sides = {
        "top": fitz.Rect(crop.x0, crop.y0 - off - band, crop.x1, crop.y0 - off),
        "bottom": fitz.Rect(crop.x0, crop.y1 + off, crop.x1, crop.y1 + off + band),
        "left": fitz.Rect(crop.x0 - off - band, crop.y0, crop.x0 - off, crop.y1),
        "right": fitz.Rect(crop.x1 + off, crop.y0, crop.x1 + off + band, crop.y1),
    }
    for name, strip in sides.items():
        strip &= page.rect
        if strip.is_empty or strip.width < 0.3 or strip.height < 0.3:
            continue
        img, _, _ = render_gray(page, 150, strip)
        ink = (img < 110)
        # ink must continue *into* the crop edge to count as a cut
        edge = ink[-1, :] if name == "top" else ink[0, :] if name == "bottom" else \
            ink[:, -1] if name == "left" else ink[:, 0]
        if edge.sum() >= 3 and ink.mean() > 0.004:
            cut.append(f"[graphics beyond {name} edge]")
    return cut


def isolated_source(src: fitz.Document, pno: int, crop: fitz.Rect,
                    strip_hidden: bool) -> tuple[fitz.Document, bool]:
    """One-page copy of the source page. When strip_hidden is on, everything
    outside the crop (tax invoice etc.) is physically removed with redactions,
    and the label area is verified pixel-for-pixel against the original.
    Returns (document, stripped?)."""
    tmp = fitz.open()
    tmp.insert_pdf(src, from_page=pno, to_page=pno)
    if not strip_hidden:
        return tmp, False
    page = tmp[0]
    pr = page.rect
    g = 0.25
    bands = [fitz.Rect(pr.x0, pr.y0, pr.x1, crop.y0 - g),
             fitz.Rect(pr.x0, crop.y1 + g, pr.x1, pr.y1),
             fitz.Rect(pr.x0, crop.y0 - g, crop.x0 - g, crop.y1 + g),
             fitz.Rect(crop.x1 + g, crop.y0 - g, pr.x1, crop.y1 + g)]
    bands = [b for b in bands if b.width > 0.5 and b.height > 0.5]
    if not bands:
        return tmp, False
    # Keep images that straddle the crop (e.g. a full-page scan) intact.
    straddle = any(fitz.Rect(i["bbox"]).intersects(crop) and not crop.contains(fitz.Rect(i["bbox"]))
                   for i in page.get_image_info())
    for b in bands:
        page.add_redact_annot(b, fill=False, cross_out=False)
    try:
        page.apply_redactions(
            images=fitz.PDF_REDACT_IMAGE_NONE if straddle else fitz.PDF_REDACT_IMAGE_REMOVE,
            graphics=fitz.PDF_REDACT_LINE_ART_REMOVE_IF_COVERED,
            text=fitz.PDF_REDACT_TEXT_REMOVE,
        )
    except Exception:
        tmp.close()
        tmp = fitz.open()
        tmp.insert_pdf(src, from_page=pno, to_page=pno)
        return tmp, False
    # Verify: the label area must render identically before and after.
    a, _, _ = render_gray(src[pno], 110, crop)
    b, _, _ = render_gray(tmp[0], 110, crop)
    if a.shape != b.shape or int(np.abs(a.astype(np.int16) - b.astype(np.int16)).max()) > 24:
        tmp.close()
        tmp = fitz.open()
        tmp.insert_pdf(src, from_page=pno, to_page=pno)
        return tmp, False
    return tmp, True
