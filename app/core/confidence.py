"""Detection confidence score (0-100) with a human-readable breakdown."""
from __future__ import annotations

import pymupdf as fitz

from .anchors import CORE_KEYWORDS, anchors_in

WEIGHTS = {
    "keywords": 30,     # shipping-label keywords found inside the crop
    "border": 20,       # closed rectangular border detected
    "barcode": 15,      # 1-D barcode (AWB) found / decoded
    "qr": 10,           # 2-D code found
    "structure": 15,    # header at top, footer at bottom, AWB + address inside
    "invoice": 10,      # tax invoice detected separately and excluded
}


def _v(a, crop: fitz.Rect, orientation: int) -> tuple[float, float]:
    """Anchor's (top-distance, bottom-distance) as fractions of the label's
    height, measured in the label's upright frame."""
    x0, y0, x1, y1 = a.rect
    if orientation == 180:
        return (crop.y1 - y1) / crop.height, (y0 - crop.y0) / crop.height
    if orientation == 90:   # content turned clockwise: label top is on the right
        return (crop.x1 - x1) / crop.width, (x0 - crop.x0) / crop.width
    if orientation == 270:  # content turned counter-clockwise: label top on the left
        return (x0 - crop.x0) / crop.width, (crop.x1 - x1) / crop.width
    return (y0 - crop.y0) / crop.height, (crop.y1 - y1) / crop.height


def score_detection(det, crop: fitz.Rect, codes: dict, cut: list[str],
                    other_rects: list[fitz.Rect], page_rect: fitz.Rect,
                    orientation: int = 0) -> dict:
    unit = max(page_rect.width, page_rect.height) / 842.0
    parts: dict[str, float] = {}
    reasons: list[str] = []
    la_in = anchors_in(crop, det.all_label_anchors)
    names = {a.name for a in la_in}

    # 1. keywords
    found = [k for k in CORE_KEYWORDS if k in names]
    parts["keywords"] = WEIGHTS["keywords"] * min(1.0, len(found) / 8.0)

    # 2. border
    parts["border"] = WEIGHTS["border"] if det.border else (8 if det.method != "manual" else 0)

    # 3. barcode
    if codes.get("barcodes"):
        parts["barcode"] = WEIGHTS["barcode"]
    elif codes.get("barcode_like"):
        parts["barcode"] = 8
        reasons.append("Barcode found but could not be decoded")
    else:
        parts["barcode"] = 0
        reasons.append("No barcode found inside crop")

    # 4. QR / 2-D code
    if codes.get("qr") or codes.get("matrix_codes"):
        parts["qr"] = WEIGHTS["qr"]
    else:
        parts["qr"] = 0
        reasons.append("No QR / 2-D code found inside crop")

    # 5. structure
    s = 0.0
    top = [a for a in la_in if a.name in ("order_id", "reg", "payment")
           and _v(a, crop, orientation)[0] < 0.3]
    bottom = [a for a in la_in if a.name in ("not_for_resale", "printed_at")
              and _v(a, crop, orientation)[1] < 0.25]
    if top:
        s += 5
    else:
        reasons.append("Shipping header not found at top of crop")
    if bottom:
        s += 5
    else:
        reasons.append("'Not for resale' / printed footer not found at bottom of crop")
    if {"awb", "ship_address"} & names:
        s += 5
    parts["structure"] = s

    # 6. invoice separation
    inv_all = det.invoice_anchors
    inv_in = anchors_in(crop, inv_all)
    if inv_in:
        parts["invoice"] = 0
    elif inv_all:
        parts["invoice"] = WEIGHTS["invoice"]
    else:
        parts["invoice"] = 7

    total = sum(parts.values())

    # ---- penalties ---------------------------------------------------------
    penalties: dict[str, float] = {}
    if inv_in:
        penalties["invoice_inside"] = 40
        reasons.append("Tax-invoice text is inside the crop: " +
                       ", ".join(sorted({a.text[:30] for a in inv_in}))[:120])
    if cut:
        penalties["cut_through"] = 20
        reasons.append("Crop edge cuts through content: " + ", ".join(cut[:5]))
    orphans = [a for a in det.all_label_anchors
               if a.strong and not crop.intersects(a.rect)
               and not any(r.intersects(a.rect) for r in other_rects)
               and _gap(crop, a.rect) < 30 * unit]
    if orphans:
        penalties["content_outside"] = 15
        reasons.append("Label text found just outside the crop: " +
                       ", ".join(sorted({a.text[:25] for a in orphans}))[:120])
    if crop.get_area() < 0.015 * page_rect.get_area():
        penalties["too_small"] = 20
        reasons.append("Detected area is unusually small")
    total -= sum(penalties.values())
    total = max(0.0, min(100.0, total))

    return {
        "score": round(total, 1),
        "parts": {k: round(v, 1) for k, v in parts.items()},
        "max": WEIGHTS,
        "penalties": penalties,
        "keywords_found": found,
        "reasons": reasons,
    }


def _gap(r: fitz.Rect, e: fitz.Rect) -> float:
    dx = max(0.0, e.x0 - r.x1, r.x0 - e.x1)
    dy = max(0.0, e.y0 - r.y1, r.y0 - e.y1)
    return max(dx, dy)
