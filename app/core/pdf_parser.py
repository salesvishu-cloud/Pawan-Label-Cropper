"""PDF parser: opens uploads and turns each page into plain geometry.

Everything the detector needs is extracted from the *native* PDF first:
text lines with coordinates, words, vector line segments (borders / table
rules), and image placements (barcodes, QR, logos).  Nothing here rasterises.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

import pymupdf as fitz

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}


# --------------------------------------------------------------------------- #
# Data classes
# --------------------------------------------------------------------------- #
@dataclass
class TextLine:
    rect: fitz.Rect
    text: str
    dir: tuple = (1.0, 0.0)      # writing direction (1,0) = normal left-to-right


@dataclass
class Segment:
    """An axis-aligned line segment. For 'h' segments y0 == y1 (centre line)."""
    x0: float
    y0: float
    x1: float
    y1: float
    orient: str            # 'h' or 'v'
    thickness: float = 0.75
    dashed: bool = False
    dark: bool = True

    @property
    def length(self) -> float:
        return (self.x1 - self.x0) if self.orient == "h" else (self.y1 - self.y0)

    @property
    def pos(self) -> float:
        return self.y0 if self.orient == "h" else self.x0

    @property
    def rect(self) -> fitz.Rect:
        t = max(self.thickness, 0.5) / 2
        if self.orient == "h":
            return fitz.Rect(self.x0, self.y0 - t, self.x1, self.y1 + t)
        return fitz.Rect(self.x0 - t, self.y0, self.x1 + t, self.y1)


@dataclass
class ImagePlacement:
    rect: fitz.Rect
    xref: int
    width: int
    height: int


@dataclass
class PageData:
    index: int
    rect: fitz.Rect
    lines: list[TextLine] = field(default_factory=list)
    words: list[tuple] = field(default_factory=list)          # (x0,y0,x1,y1,text)
    segments: list[Segment] = field(default_factory=list)
    images: list[ImagePlacement] = field(default_factory=list)
    drawing_rects: list[fitz.Rect] = field(default_factory=list)
    source: str = "native"                                     # native | ocr

    @property
    def unit(self) -> float:
        """Scale factor relative to an A4 page (842 pt long side)."""
        return max(self.rect.width, self.rect.height) / 842.0

    @property
    def text_word_count(self) -> int:
        return sum(1 for w in self.words if re.search(r"[A-Za-z0-9]", w[4]))


# --------------------------------------------------------------------------- #
# Opening documents
# --------------------------------------------------------------------------- #
def open_document(data: bytes, filename: str) -> fitz.Document:
    """Open a PDF (or a label image) and normalise page rotation."""
    ext = os.path.splitext(filename.lower())[1]
    if ext in IMAGE_EXTS:
        doc = _image_to_pdf(data, ext)
    else:
        doc = fitz.open(stream=data, filetype="pdf")
        if doc.needs_pass:
            raise ValueError("PDF is password protected")
        if not doc.is_pdf:
            raise ValueError("Not a PDF file")
    normalise_rotation(doc)
    return doc


def _image_to_pdf(data: bytes, ext: str) -> fitz.Document:
    """Embed an image unchanged on a page whose long side is A4 (842 pt)."""
    pix = fitz.Pixmap(data)
    scale = 842.0 / max(pix.width, pix.height)
    w, h = pix.width * scale, pix.height * scale
    doc = fitz.open()
    page = doc.new_page(width=w, height=h)
    page.insert_image(page.rect, stream=data)
    return fitz.open("pdf", doc.tobytes())


def normalise_rotation(doc: fitz.Document) -> None:
    """Bake /Rotate into the content so page coordinates == visual coordinates."""
    for page in doc:
        if page.rotation:
            page.remove_rotation()


# --------------------------------------------------------------------------- #
# Page parsing
# --------------------------------------------------------------------------- #
_DASH_RE = re.compile(r"\[\s*[0-9.]")


def _luminance(c) -> float:
    if not c:
        return 1.0
    if len(c) == 1:
        return c[0]
    if len(c) == 4:  # CMYK
        return 1 - min(1.0, c[3] + (c[0] + c[1] + c[2]) / 3)
    return 0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2]


def parse_page(page: fitz.Page) -> PageData:
    pd = PageData(index=page.number, rect=fitz.Rect(page.rect))

    # ---- text lines & words -------------------------------------------------
    d = page.get_text("dict")
    for b in d.get("blocks", []):
        if b.get("type") != 0:
            continue
        for ln in b.get("lines", []):
            text = "".join(s.get("text", "") for s in ln.get("spans", [])).strip()
            if text:
                pd.lines.append(TextLine(fitz.Rect(ln["bbox"]), text,
                                         tuple(round(v, 3) for v in ln.get("dir", (1, 0)))))
    pd.words = [(w[0], w[1], w[2], w[3], w[4]) for w in page.get_text("words")]

    # ---- images --------------------------------------------------------------
    for info in page.get_image_info(xrefs=True):
        r = fitz.Rect(info["bbox"])
        if r.is_empty or r.is_infinite:
            continue
        pd.images.append(ImagePlacement(r, info.get("xref", 0),
                                        info.get("width", 0), info.get("height", 0)))

    # ---- vector drawings -----------------------------------------------------
    # extended=True reports clip paths, so content that is clipped away (hidden,
    # e.g. an invoice masked by another tool) is ignored.
    page_area = pd.rect.width * pd.rect.height
    clips: list[tuple[int, fitz.Rect]] = []
    for dr in page.get_drawings(extended=True):
        lvl = dr.get("level", 0) or 0
        while clips and clips[-1][0] >= lvl:
            clips.pop()
        typ = dr.get("type") or ""
        if typ == "clip":
            sc = dr.get("scissor")
            if sc is not None:
                clips.append((lvl, fitz.Rect(sc)))
            continue
        if typ == "group" or "items" not in dr:
            continue
        scissor = fitz.Rect(pd.rect)
        for _, c in clips:
            scissor &= c
        if scissor.is_empty:
            continue
        dashed = bool(dr.get("dashes")) and bool(_DASH_RE.search(str(dr.get("dashes"))))
        stroke_lum = _luminance(dr.get("color"))
        fill_lum = _luminance(dr.get("fill"))
        width = dr.get("width") or 0.75
        drect = fitz.Rect(dr["rect"]) & scissor
        if drect.is_empty and (drect.width <= 0 and drect.height <= 0):
            continue

        visible = ("s" in typ and stroke_lum < 0.95) or ("f" in typ and fill_lum < 0.95)
        if visible and not dashed and drect.width * drect.height < 0.9 * page_area:
            pd.drawing_rects.append(drect)

        before = len(pd.segments)
        for it in dr.get("items", []):
            kind = it[0]
            if kind == "l":
                p1, p2 = it[1], it[2]
                if "s" not in typ or stroke_lum >= 0.95:
                    continue
                if abs(p1.y - p2.y) <= 1.0:
                    x0, x1 = sorted((p1.x, p2.x))
                    pd.segments.append(Segment(x0, (p1.y + p2.y) / 2, x1, (p1.y + p2.y) / 2, "h",
                                               width, dashed, stroke_lum < 0.6))
                elif abs(p1.x - p2.x) <= 1.0:
                    y0, y1 = sorted((p1.y, p2.y))
                    pd.segments.append(Segment((p1.x + p2.x) / 2, y0, (p1.x + p2.x) / 2, y1, "v",
                                               width, dashed, stroke_lum < 0.6))
            elif kind in ("re", "qu"):
                if kind == "qu":
                    q = it[1]
                    if not q.is_rectangular:
                        continue
                    r = fitz.Rect(q.rect)
                else:
                    r = fitz.Rect(it[1])
                r.normalize()
                _rect_to_segments(pd, r, typ, width, dashed, stroke_lum, fill_lum)
        # clip the new segments to the visible area
        kept = []
        for sg in pd.segments[before:]:
            c = _clip_segment(sg, scissor)
            if c is not None:
                kept.append(c)
        pd.segments[before:] = kept
    return pd


def _rect_to_segments(pd: PageData, r: fitz.Rect, typ: str, width: float,
                      dashed: bool, stroke_lum: float, fill_lum: float) -> None:
    filled_dark = "f" in typ and fill_lum < 0.95
    stroked = "s" in typ and stroke_lum < 0.95
    # Thin filled rectangles are how many generators (wkhtmltopdf!) draw rules
    if filled_dark and r.height <= 3.0 and r.width > 4 * max(r.height, 0.1):
        cy = (r.y0 + r.y1) / 2
        pd.segments.append(Segment(r.x0, cy, r.x1, cy, "h", r.height, dashed, fill_lum < 0.6))
        return
    if filled_dark and r.width <= 3.0 and r.height > 4 * max(r.width, 0.1):
        cx = (r.x0 + r.x1) / 2
        pd.segments.append(Segment(cx, r.y0, cx, r.y1, "v", r.width, dashed, fill_lum < 0.6))
        return
    if stroked and r.width > 3 and r.height > 3:
        dark = stroke_lum < 0.6
        pd.segments += [
            Segment(r.x0, r.y0, r.x1, r.y0, "h", width, dashed, dark),
            Segment(r.x0, r.y1, r.x1, r.y1, "h", width, dashed, dark),
            Segment(r.x0, r.y0, r.x0, r.y1, "v", width, dashed, dark),
            Segment(r.x1, r.y0, r.x1, r.y1, "v", width, dashed, dark),
        ]


def _clip_segment(sg: Segment, sc: fitz.Rect, tol: float = 0.6) -> Segment | None:
    if sg.orient == "h":
        if not (sc.y0 - tol <= sg.y0 <= sc.y1 + tol):
            return None
        x0, x1 = max(sg.x0, sc.x0), min(sg.x1, sc.x1)
        if x1 - x0 <= 0.5:
            return None
        return Segment(x0, sg.y0, x1, sg.y1, "h", sg.thickness, sg.dashed, sg.dark)
    if not (sc.x0 - tol <= sg.x0 <= sc.x1 + tol):
        return None
    y0, y1 = max(sg.y0, sc.y0), min(sg.y1, sc.y1)
    if y1 - y0 <= 0.5:
        return None
    return Segment(sg.x0, y0, sg.x1, y1, "v", sg.thickness, sg.dashed, sg.dark)


def page_count(data: bytes, filename: str) -> int:
    with open_document(data, filename) as doc:
        return doc.page_count
