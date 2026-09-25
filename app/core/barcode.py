"""Barcode / QR detection and preservation checks.

The output never re-draws or re-encodes a barcode: the original PDF content
(vector graphics and embedded images at their native resolution) is placed on
the 4x6 page.  This module only *reads* codes:

* as detection anchors (a label has an AWB barcode and a 2-D code), and
* to verify that every barcode found on the source label is still machine
  readable on the finished 4x6 page at thermal-printer resolution (203 dpi).
"""
from __future__ import annotations

import functools

import cv2
import numpy as np
import pymupdf as fitz

from .raster import render_gray


@functools.lru_cache(maxsize=1)
def _pyzbar():
    try:
        from pyzbar import pyzbar  # type: ignore
        pyzbar.decode(np.zeros((10, 10), np.uint8))
        return pyzbar
    except Exception:
        return None


def decoder_name() -> str:
    return "pyzbar" if _pyzbar() else "opencv"


def _decode(img: np.ndarray) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    zb = _pyzbar()
    if zb is not None:
        for r in zb.decode(img):
            found.append((r.type, r.data.decode("utf-8", "replace")))
    else:
        try:
            bd = cv2.barcode.BarcodeDetector()
            ok, infos, types, _ = bd.detectAndDecodeWithType(img)
            if ok:
                found += [(t, i) for i, t in zip(infos, types) if i]
        except Exception:
            pass
        try:
            data, _, _ = cv2.QRCodeDetector().detectAndDecode(img)
            if data:
                found.append(("QRCODE", data))
        except Exception:
            pass
    # unique, keep order
    seen, out = set(), []
    for t, d in found:
        if (t, d) not in seen:
            seen.add((t, d))
            out.append((t, d))
    return out


def _matrix_codes(img: np.ndarray, px_per_inch: float) -> int:
    """Count square 2-D code blobs (QR / DataMatrix-like) structurally."""
    bw = (img < 128).astype(np.uint8)
    k = max(3, int(px_per_inch * 0.04))
    blob = cv2.morphologyEx(bw * 255, cv2.MORPH_CLOSE, np.ones((k, k), np.uint8))
    # RETR_LIST: the code usually sits inside the label border's contour
    contours, _ = cv2.findContours(blob, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    hits: list[tuple[int, int, int, int]] = []
    rects = sorted((cv2.boundingRect(c) for c in contours), key=lambda r: -r[2] * r[3])
    for x, y, w, h in rects:
        if w < 0.4 * px_per_inch or h < 0.4 * px_per_inch:
            continue
        if not 0.8 <= w / h <= 1.25:
            continue
        sub = bw[y:y + h, x:x + w]
        fill = sub.mean()
        if not 0.25 <= fill <= 0.75:
            continue
        tr_x = np.abs(np.diff(sub.astype(np.int8), axis=1)).sum(axis=1).mean() / w
        tr_y = np.abs(np.diff(sub.astype(np.int8), axis=0)).sum(axis=0).mean() / h
        if tr_x > 0.02 and tr_y > 0.02:          # modules in both directions
            cx, cy = x + w / 2, y + h / 2
            if not any(a <= cx <= a + aw and b <= cy <= b + ah for a, b, aw, ah in hits):
                hits.append((x, y, w, h))
    return len(hits)


def _linear_like(img: np.ndarray, px_per_inch: float) -> int:
    """Count 1-D barcode-like blobs (many parallel bars) when decoding fails."""
    bw = (img < 128).astype(np.uint8) * 255
    n = 0
    for axis_kernel in ((int(px_per_inch * 0.08) or 3, 1), (1, int(px_per_inch * 0.08) or 3)):
        blob = cv2.morphologyEx(bw, cv2.MORPH_CLOSE, np.ones(axis_kernel[::-1], np.uint8))
        contours, _ = cv2.findContours(blob, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        for c in contours:
            x, y, w, h = cv2.boundingRect(c)
            long_, short = max(w, h), min(w, h)
            if long_ < 0.8 * px_per_inch or short < 0.12 * px_per_inch or long_ / short < 2.5:
                continue
            sub = bw[y:y + h, x:x + w] > 0
            along = 1 if w >= h else 0
            tr = np.abs(np.diff(sub.astype(np.int8), axis=along)).sum(axis=along).mean()
            if tr / long_ * px_per_inch > 20:     # > 20 bar edges per inch
                n += 1
    return n


def scan_region(page: fitz.Page, rect: fitz.Rect, target_px: int = 1100) -> dict:
    """Find barcodes and 2-D codes inside rect of a source page."""
    dpi = max(150.0, min(400.0, 72.0 * target_px / max(rect.width, 1)))
    img, z, _ = render_gray(page, dpi, rect)
    ppi = dpi  # px per (source) inch
    codes = _decode(img)
    linear = [(t, d) for t, d in codes if t not in ("QRCODE",)]
    qr = [(t, d) for t, d in codes if t == "QRCODE"]
    matrix = _matrix_codes(img, ppi)
    return {
        "barcodes": [{"type": t, "data": d} for t, d in linear],
        "qr": [{"type": t, "data": d} for t, d in qr],
        "matrix_codes": matrix,
        "barcode_like": _linear_like(img, ppi) if not linear else len(linear),
        "decoder": decoder_name(),
    }


def verify_output_page(page: fitz.Page, expected: list[str], dpi: int = 203) -> dict:
    """Decode the finished 4x6 page at thermal-printer resolution and compare.

    level: ok        - every source barcode reads at printer resolution (203 dpi)
           marginal  - reads at 300 dpi only (source barcode was already a
                       low-resolution image, e.g. a phone photo / PNG upload)
           fail      - not readable
    """
    img, _, _ = render_gray(page, dpi)
    got = {d for _, d in _decode(img)}
    if not expected:
        return {"checked": False, "readable": sorted(got), "missing": [], "level": "n/a"}
    missing = [e for e in expected if e not in got]
    level = "ok"
    if missing:
        img2, _, _ = render_gray(page, max(300, dpi))
        got |= {d for _, d in _decode(img2)}
        missing2 = [e for e in expected if e not in got]
        level = "marginal" if not missing2 else "fail"
    return {"checked": True, "readable": sorted(got), "missing": missing,
            "ok": level == "ok", "level": level, "dpi": dpi}
