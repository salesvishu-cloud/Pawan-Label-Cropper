"""Automatic Flipkart shipping-label detection.

Strategy (first one that succeeds wins, each result is scored afterwards):

1. VECTOR BORDER   - rebuild rectangles from the PDF's own ruling lines and
                     pick the outermost rectangle that contains shipping-label
                     keywords and no tax-invoice keywords.
2. TEXT ANCHOR     - no closed border: grow a region outward from strong label
                     keywords through neighbouring text/images/graphics, never
                     crossing the invoice start (dashed cut line / "Tax Invoice").
3. RASTER + OCR    - scanned / image-only pages: detect ruling lines with
                     OpenCV and read keywords with local Tesseract OCR, then
                     apply 1 and 2 on that data.

No fixed coordinates are used anywhere; every threshold is relative to the
page size (``PageData.unit``).
"""
from __future__ import annotations

import bisect
from dataclasses import dataclass, field

import pymupdf as fitz

from . import raster
from .anchors import (Anchor, anchors_in, distinct, find_anchors,
                      has_duplicate_unique)
from .pdf_parser import PageData, Segment, TextLine, parse_page


@dataclass
class Detection:
    rect: fitz.Rect                 # label boundary, page coordinates
    method: str                     # vector-border | text-anchor | raster-border | raster-anchor | manual
    border: bool                    # a closed rectangular border was found
    anchors: list[Anchor] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    pdata: PageData | None = None
    invoice_anchors: list[Anchor] = field(default_factory=list)
    all_label_anchors: list[Anchor] = field(default_factory=list)
    line_thickness: float = 1.0


# --------------------------------------------------------------------------- #
# Segment helpers
# --------------------------------------------------------------------------- #
def merge_segments(segs: list[Segment], orient: str, pos_tol: float, gap: float) -> list[Segment]:
    items = sorted((s for s in segs if s.orient == orient and not s.dashed),
                   key=lambda s: (s.pos, s.x0 if orient == "h" else s.y0))
    clusters: list[list[Segment]] = []
    for s in items:
        if clusters and abs(s.pos - clusters[-1][0].pos) <= pos_tol:
            clusters[-1].append(s)
        else:
            clusters.append([s])
    out: list[Segment] = []
    for cl in clusters:
        pos = sum(s.pos for s in cl) / len(cl)
        spans = sorted(((s.x0, s.x1) if orient == "h" else (s.y0, s.y1), s.thickness, s.dark)
                       for s in cl)
        cur_a, cur_b = spans[0][0]
        thick, dark = spans[0][1], spans[0][2]
        for (a, b), t, dk in spans[1:]:
            if a <= cur_b + gap:
                cur_b = max(cur_b, b)
                thick, dark = max(thick, t), dark or dk
            else:
                out.append(_mk(orient, pos, cur_a, cur_b, thick, dark))
                cur_a, cur_b, thick, dark = a, b, t, dk
        out.append(_mk(orient, pos, cur_a, cur_b, thick, dark))
    return out


def _mk(orient, pos, a, b, thick, dark) -> Segment:
    if orient == "h":
        return Segment(a, pos, b, pos, "h", thick, False, dark)
    return Segment(pos, a, pos, b, "v", thick, False, dark)


class _Index:
    """Lookup of segments by position for fast coverage queries."""

    def __init__(self, segs: list[Segment]):
        self.segs = sorted(segs, key=lambda s: s.pos)
        self.keys = [s.pos for s in self.segs]

    def near(self, pos: float, tol: float) -> list[Segment]:
        i = bisect.bisect_left(self.keys, pos - tol)
        j = bisect.bisect_right(self.keys, pos + tol)
        return self.segs[i:j]

    def coverage(self, pos: float, a: float, b: float, tol: float,
                 max_gap: float | None = None) -> float:
        """Fraction of [a, b] covered by segments near pos. Returns 0 when any
        uncovered gap is longer than max_gap (two separate borders in a row
        are not one border)."""
        if b <= a:
            return 0.0
        spans = []
        for s in self.near(pos, tol):
            lo, hi = (s.x0, s.x1) if s.orient == "h" else (s.y0, s.y1)
            lo, hi = max(lo, a), min(hi, b)
            if hi > lo:
                spans.append((lo, hi))
        spans.sort()
        total, cur_a, cur_b = 0.0, None, None
        biggest_gap, last_end = 0.0, a
        for lo, hi in spans:
            if cur_b is None or lo > cur_b:
                if cur_b is not None:
                    total += cur_b - cur_a
                biggest_gap = max(biggest_gap, lo - last_end)
                cur_a, cur_b = lo, hi
            else:
                cur_b = max(cur_b, hi)
            last_end = max(last_end, cur_b)
        if cur_b is not None:
            total += cur_b - cur_a
        biggest_gap = max(biggest_gap, b - last_end)
        if max_gap is not None and biggest_gap > max_gap:
            return 0.0
        return total / (b - a)


def find_boxes(segments: list[Segment], unit: float) -> list[tuple[fitz.Rect, float]]:
    """All closed axis-aligned rectangles formed by ruling lines."""
    tol = 3.0 * unit
    gap = 8.0 * unit          # largest break allowed inside one border line
    min_side = 50.0 * unit
    H = [s for s in merge_segments(segments, "h", 1.2 * unit, 3 * unit) if s.length >= min_side]
    V = [s for s in merge_segments(segments, "v", 1.2 * unit, 3 * unit) if s.length >= min_side]
    hidx, vidx = _Index(H), _Index(V)
    boxes: list[tuple[fitz.Rect, float]] = []

    Hs = sorted(H, key=lambda s: s.pos)
    for i, t in enumerate(Hs):
        for b in Hs[i + 1:]:
            if b.pos - t.pos < min_side:
                continue
            if abs(t.x0 - b.x0) > tol or abs(t.x1 - b.x1) > tol:
                continue
            xl, xr = (t.x0 + b.x0) / 2, (t.x1 + b.x1) / 2
            if (vidx.coverage(xl, t.pos, b.pos, tol, gap) >= 0.9 and
                    vidx.coverage(xr, t.pos, b.pos, tol, gap) >= 0.9):
                boxes.append((fitz.Rect(xl, t.pos, xr, b.pos), max(t.thickness, b.thickness)))

    Vs = sorted(V, key=lambda s: s.pos)
    for i, l in enumerate(Vs):
        for r in Vs[i + 1:]:
            if r.pos - l.pos < min_side:
                continue
            if abs(l.y0 - r.y0) > tol or abs(l.y1 - r.y1) > tol:
                continue
            yt, yb = (l.y0 + r.y0) / 2, (l.y1 + r.y1) / 2
            if (hidx.coverage(yt, l.pos, r.pos, tol, gap) >= 0.9 and
                    hidx.coverage(yb, l.pos, r.pos, tol, gap) >= 0.9):
                boxes.append((fitz.Rect(l.pos, yt, r.pos, yb), max(l.thickness, r.thickness)))

    # de-duplicate
    uniq: list[tuple[fitz.Rect, float]] = []
    for r, t in sorted(boxes, key=lambda x: -x[0].get_area()):
        if not any(_same(r, u, tol) for u, _ in uniq):
            uniq.append((r, t))
    return uniq


def _same(a: fitz.Rect, b: fitz.Rect, tol: float) -> bool:
    return (abs(a.x0 - b.x0) <= tol and abs(a.y0 - b.y0) <= tol and
            abs(a.x1 - b.x1) <= tol and abs(a.y1 - b.y1) <= tol)


def _contains(outer: fitz.Rect, inner: fitz.Rect, tol: float) -> bool:
    return (inner.x0 >= outer.x0 - tol and inner.y0 >= outer.y0 - tol and
            inner.x1 <= outer.x1 + tol and inner.y1 <= outer.y1 + tol)


def _is_label_like(la: list[Anchor], ia: list[Anchor]) -> bool:
    strong = {a.name for a in la if a.strong}
    return len(strong) >= 2 and len(distinct(la)) >= 3 and not ia and not has_duplicate_unique(la)


# --------------------------------------------------------------------------- #
# 1) Vector border
# --------------------------------------------------------------------------- #
def select_label_boxes(boxes, label_anchors, invoice_anchors, unit) -> list[tuple[fitz.Rect, float, list[Anchor]]]:
    cands = []
    for r, thick in boxes:
        la = anchors_in(r, label_anchors)
        ia = anchors_in(r, invoice_anchors)
        if _is_label_like(la, ia):
            cands.append((r, thick, la))
    cands.sort(key=lambda c: -c[0].get_area())
    chosen: list[tuple[fitz.Rect, float, list[Anchor]]] = []
    for r, thick, la in cands:
        if any(_contains(c[0], r, 2 * unit) or c[0].intersects(r) for c in chosen):
            continue
        chosen.append((r, thick, la))
    return chosen


# --------------------------------------------------------------------------- #
# 2) Text-anchor region growing
# --------------------------------------------------------------------------- #
@dataclass
class _Barrier:
    y: float
    x0: float
    x1: float


def _barriers(pd: PageData, invoice_anchors: list[Anchor]) -> list[_Barrier]:
    bars: list[_Barrier] = []
    pw = pd.rect.width
    for s in pd.segments:  # dashed "cut here" lines spanning much of the page
        if s.orient == "h" and s.dashed and s.length >= 0.3 * pw:
            bars.append(_Barrier(s.pos, s.x0, s.x1))
    for a in invoice_anchors:
        ext = 0.25 * pw
        bars.append(_Barrier(a.rect.y0 - 0.5, a.rect.x0 - ext, a.rect.x1 + ext))
    return bars


def _blocked(bars: list[_Barrier], y_from: float, e: fitz.Rect) -> bool:
    ey = (e.y0 + e.y1) / 2
    lo, hi = min(y_from, ey), max(y_from, ey)
    for b in bars:
        if b.x1 < e.x0 or b.x0 > e.x1:
            continue
        if lo < b.y < hi or e.y0 < b.y < e.y1:
            return True
    return False


def _gap(r: fitz.Rect, e: fitz.Rect) -> float:
    dx = max(0.0, e.x0 - r.x1, r.x0 - e.x1)
    dy = max(0.0, e.y0 - r.y1, r.y0 - e.y1)
    return max(dx, dy)


def grow_region(seed: fitz.Rect, elements: list[fitz.Rect], bars: list[_Barrier],
                gap: float) -> fitz.Rect:
    region = fitz.Rect(seed)
    y_from = (seed.y0 + seed.y1) / 2
    used = [False] * len(elements)
    changed = True
    while changed:
        changed = False
        for i, e in enumerate(elements):
            if used[i] or _gap(region, e) > gap:
                continue
            if _blocked(bars, y_from, e):
                used[i] = True
                continue
            region |= e
            used[i] = True
            changed = True
    return region


def _elements(pd: PageData, invoice_anchors: list[Anchor]) -> list[fitz.Rect]:
    inv_rects = [a.rect for a in invoice_anchors]
    page_area = pd.rect.width * pd.rect.height
    els: list[fitz.Rect] = []
    for ln in pd.lines:
        if any(ln.rect.intersects(r) and abs(ln.rect.y0 - r.y0) < 1 for r in inv_rects):
            continue
        els.append(ln.rect)
    for im in pd.images:
        if im.rect.get_area() < 0.5 * page_area:
            els.append(im.rect)
    for r in pd.drawing_rects:
        if r.width < 0.9 * pd.rect.width and r.get_area() < 0.5 * page_area:
            els.append(r)
    for s in pd.segments:
        if not s.dashed:
            els.append(s.rect)
    return [e for e in els if not e.is_empty or e.width > 0 or e.height > 0]


def text_anchor_regions(pd: PageData, label_anchors, invoice_anchors) -> list[tuple[fitz.Rect, list[Anchor]]]:
    unit = pd.unit
    els = _elements(pd, invoice_anchors)
    bars = _barriers(pd, invoice_anchors)
    seeds = [a for a in label_anchors if a.strong]
    regions: list[fitz.Rect] = []
    for s in seeds:
        c = fitz.Point((s.rect.x0 + s.rect.x1) / 2, (s.rect.y0 + s.rect.y1) / 2)
        if any(r.contains(c) for r in regions):
            continue
        regions.append(grow_region(s.rect, els, bars, 8.0 * unit))
    # merge overlapping regions
    merged = True
    while merged:
        merged = False
        for i in range(len(regions)):
            for j in range(i + 1, len(regions)):
                if regions[i].intersects(regions[j]):
                    regions[i] |= regions[j]
                    del regions[j]
                    merged = True
                    break
            if merged:
                break
    out = []
    for r in regions:
        la = anchors_in(r, label_anchors)
        ia = anchors_in(r, invoice_anchors)
        if _is_label_like(la, ia) and r.width > 60 * unit and r.height > 60 * unit:
            out.append((r, la))
    return out


def _refine_with_orphans(pd, chosen, label_anchors, invoice_anchors):
    """If strong label keywords sit just outside a detected border (the border
    only enclosed part of the label), grow the box to take them in."""
    unit = pd.unit
    inside = set()
    for r, _, la in chosen:
        inside.update(id(a) for a in la)
    orphans = [a for a in label_anchors if a.strong and id(a) not in inside]
    if not orphans:
        return chosen, []
    els = _elements(pd, invoice_anchors)
    bars = _barriers(pd, invoice_anchors)
    out, notes = [], []
    for r, thick, la in chosen:
        near = [a for a in orphans if _gap(r, a.rect) <= 25 * unit
                and not _blocked(bars, (r.y0 + r.y1) / 2, a.rect)]
        if near:
            grown = grow_region(r, els, bars, 8.0 * unit)
            la2 = anchors_in(grown, label_anchors)
            ia2 = anchors_in(grown, invoice_anchors)
            if _is_label_like(la2, ia2) and len(distinct(la2)) > len(distinct(la)):
                notes.append("border enclosed only part of the label; extended to nearby label content")
                out.append((grown, thick, la2))
                continue
        out.append((r, thick, la))
    return out, notes


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def _reading_order(dets: list[Detection]) -> list[Detection]:
    rows: list[list[Detection]] = []
    for d in sorted(dets, key=lambda d: d.rect.y0):
        for row in rows:
            ref = row[0].rect
            ov = min(ref.y1, d.rect.y1) - max(ref.y0, d.rect.y0)
            if ov > 0.5 * min(ref.height, d.rect.height):
                row.append(d)
                break
        else:
            rows.append([d])
    out = []
    for row in rows:
        out += sorted(row, key=lambda d: d.rect.x0)
    return out


def _run_strategies(pd: PageData, raster_mode: bool, use_boxes: bool = True,
                    use_text: bool = True) -> list[Detection]:
    la, ia = find_anchors(pd.lines)
    dets: list[Detection] = []
    if use_boxes:
        boxes = find_boxes(pd.segments, pd.unit)
        chosen = select_label_boxes(boxes, la, ia, pd.unit)
        if chosen:
            chosen, notes = _refine_with_orphans(pd, chosen, la, ia)
            for r, thick, anchors in chosen:
                dets.append(Detection(r, "raster-border" if raster_mode else "vector-border", True,
                                      anchors, list(notes), pd, ia, la, thick))
            return dets
    if use_text:
        for r, anchors in text_anchor_regions(pd, la, ia):
            dets.append(Detection(r, "raster-anchor" if raster_mode else "text-anchor", False,
                                  anchors, [], pd, ia, la, 0.0))
    return dets


def build_raster_pagedata(page: fitz.Page, native: PageData, dpi: int, use_ocr: bool) -> PageData:
    """Page data from image analysis: ruling lines via OpenCV, text via local OCR.
    OCR runs only on the candidate boxes when borders are visible (much faster),
    otherwise on the whole page."""
    pd = PageData(index=native.index, rect=native.rect, source="ocr")
    pd.lines = list(native.lines)
    pd.words = list(native.words)
    pd.segments = raster.raster_segments(page, 150) + [s for s in native.segments if s.dashed]
    pd.drawing_rects = raster.ink_components(page, 100)
    if not use_ocr:
        return pd
    page_area = pd.rect.get_area()
    boxes = [r for r, _ in find_boxes(pd.segments, pd.unit) if r.get_area() >= 0.02 * page_area]
    regions: list[fitz.Rect] = []
    for r in sorted(boxes, key=lambda b: -b.get_area()):
        if not any(_contains(x, r, 2 * pd.unit) for x in regions):
            regions.append(r)
    covered = sum(r.get_area() for r in regions)
    if not regions or covered > 0.7 * page_area or len(regions) > 12:
        regions = [fitz.Rect(pd.rect)]
    for r in regions:
        pad = 3 * pd.unit
        clip = fitz.Rect(r.x0 - pad, r.y0 - pad, r.x1 + pad, r.y1 + pad) & pd.rect
        lines, words = raster.ocr_page(page, dpi, clip, psm=6)
        la, _ = find_anchors(lines)
        if len({a.name for a in la if a.strong}) < 3:      # second opinion
            l2, w2 = raster.ocr_page(page, dpi, clip, psm=3)
            lines += l2
            words += w2
        pd.lines += lines
        if native.text_word_count < 15:
            pd.words += words
    # Tax-invoice keywords outside the OCR'd boxes still matter: if we only read
    # boxes, look for invoice text in the rest of the page at low cost.
    if regions and regions[0] != pd.rect:
        lines, _ = raster.ocr_page(page, 200, None, psm=11)
        pd.lines += [l for l in lines if not any(r.intersects(l.rect) for r in regions)]
    return pd


def detect_labels(page: fitz.Page, enable_ocr: bool = True, ocr_dpi: int = 300,
                  force: str | None = None) -> tuple[list[Detection], PageData]:
    """Detect every shipping label on one page.

    force: None (auto) | 'vector' (borders only) | 'text' (keyword region only)
           | 'raster' (image analysis + OCR)
    """
    native = parse_page(page)
    dets: list[Detection] = []
    if force in (None, "vector", "text"):
        dets = _run_strategies(native, raster_mode=False,
                               use_boxes=(force != "text"), use_text=(force != "vector"))
    # Image analysis only makes sense when the label could be a picture: a
    # scanned/image-only page, or a page carrying a large embedded image.
    big_image = any(im.rect.get_area() >= 0.05 * native.rect.get_area() for im in native.images)
    raster_worth_it = force == "raster" or native.text_word_count < 15 or big_image
    if not dets and force in (None, "raster") and raster_worth_it:
        use_ocr = enable_ocr and raster.ocr_available()
        rpd = build_raster_pagedata(page, native, ocr_dpi, use_ocr)
        dets = _run_strategies(rpd, raster_mode=True)
        if not use_ocr:
            for d in dets:
                d.notes.append("OCR not available - raster detection used native text only")
    return _reading_order(dets), native
