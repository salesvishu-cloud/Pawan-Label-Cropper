"""High-level pipeline: analyse pages -> label results -> 4x6 PDF outputs.
UI-independent; used by the FastAPI app, the CLI and the tests.
"""
from __future__ import annotations

import os
import re
import uuid
import zipfile
from dataclasses import asdict, dataclass, field

import pymupdf as fitz

from ..config import APP_NAME, OUT_HEIGHT_PT, OUT_WIDTH_PT, Settings
from .anchors import find_anchors
from .barcode import scan_region, verify_output_page
from .confidence import score_detection
from .crop_engine import content_cut_by, crop_rect_for, isolated_source
from .label_detector import Detection, detect_labels
from .pdf_parser import parse_page
from .renderer_4x6 import place_label

_OD_RE = re.compile(r"\bOD\d{10,}\b")


@dataclass
class LabelResult:
    uid: str
    file_idx: int
    file_name: str
    page: int                       # 0-based page index
    index_on_page: int
    status: str                     # detected | review | failed | manual
    confidence: float
    method: str
    rect: list | None               # detected label boundary (pt)
    crop: list | None               # final crop rectangle (pt)
    border: bool = False
    line_thickness: float = 0.0
    unit: float = 1.0
    breakdown: dict = field(default_factory=dict)
    reasons: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    codes: dict = field(default_factory=dict)
    order_id: str = ""
    orientation: int = 0            # CCW turn that makes the text upright
    approved: bool | None = None    # user override: True include / False exclude
    output: dict = field(default_factory=dict)

    @property
    def sort_key(self):
        return (self.file_idx, self.page, self.index_on_page)

    def to_dict(self) -> dict:
        return asdict(self)


def _status(conf: float, settings: Settings) -> str:
    return "detected" if conf >= settings.confidence_threshold else "review"


def is_included(lbl: LabelResult, settings: Settings) -> bool:
    if lbl.status == "failed" or lbl.crop is None:
        return False
    if lbl.approved is not None:
        return lbl.approved
    if lbl.status in ("detected", "manual"):
        return True
    return settings.include_low_confidence


def _score(page: fitz.Page, det: Detection, crop: fitz.Rect, others: list[fitz.Rect],
           native, orientation: int = 0) -> tuple[dict, dict]:
    codes = scan_region(page, crop)
    cut = content_cut_by(page, native.words, crop, native.unit)
    sc = score_detection(det, crop, codes, cut, others, page.rect, orientation)
    return sc, codes


def detect_orientation(lines, crop: fitz.Rect) -> int:
    """Dominant text direction inside the crop -> rotation that makes it upright."""
    votes = {0: 0, 90: 0, 180: 0, 270: 0}
    for ln in lines:
        c = fitz.Point((ln.rect.x0 + ln.rect.x1) / 2, (ln.rect.y0 + ln.rect.y1) / 2)
        if not crop.contains(c):
            continue
        dx, dy = ln.dir
        if abs(dx) >= abs(dy):
            k = 0 if dx > 0 else 180
        else:
            k = 90 if dy > 0 else 270
        votes[k] += len(ln.text)
    total = sum(votes.values())
    if not total:
        return 0
    best = max(votes, key=votes.get)
    return best if votes[best] >= 0.6 * total else 0


def _order_id(det: Detection) -> str:
    for a in det.anchors:
        m = _OD_RE.search(a.text)
        if m:
            return m.group(0)
    return ""


def analyse_page(doc: fitz.Document, pno: int, file_idx: int, file_name: str,
                 settings: Settings, force: str | None = None) -> list[LabelResult]:
    page = doc[pno]
    dets, native = detect_labels(page, settings.enable_ocr, settings.ocr_dpi, force)
    if not dets:
        return [LabelResult(uuid.uuid4().hex[:12], file_idx, file_name, pno, 0, "failed", 0.0,
                            "none", None, None,
                            reasons=["No shipping label could be detected on this page"])]
    crops = [crop_rect_for(page, d.rect, d.border, native.unit, d.line_thickness,
                           settings.label_padding_pt) for d in dets]
    out = []
    for i, (d, crop) in enumerate(zip(dets, crops)):
        others = [c for j, c in enumerate(crops) if j != i]
        orient = detect_orientation(native.lines, crop)
        sc, codes = _score(page, d, crop, others, native, orient)
        out.append(LabelResult(
            uid=uuid.uuid4().hex[:12], file_idx=file_idx, file_name=file_name, page=pno,
            index_on_page=i, status=_status(sc["score"], settings), confidence=sc["score"],
            method=d.method, rect=[round(v, 2) for v in d.rect], crop=[round(v, 2) for v in crop],
            border=d.border, line_thickness=d.line_thickness, unit=native.unit,
            breakdown=sc, reasons=sc["reasons"], notes=d.notes, codes=codes,
            order_id=_order_id(d), orientation=orient))
    return out


def manual_label(doc: fitz.Document, pno: int, rect: fitz.Rect, file_idx: int, file_name: str,
                 index_on_page: int, settings: Settings) -> LabelResult:
    """User-drawn crop (fallback). Used exactly as drawn; still scored for info."""
    page = doc[pno]
    rect = fitz.Rect(rect) & page.rect
    native = parse_page(page)
    la, ia = find_anchors(native.lines)
    det = Detection(rect, "manual", False, [a for a in la if rect.intersects(a.rect)],
                    [], native, ia, la, 0.0)
    orient = detect_orientation(native.lines, rect)
    sc, codes = _score(page, det, rect, [], native, orient)
    return LabelResult(uuid.uuid4().hex[:12], file_idx, file_name, pno, index_on_page, "manual",
                       sc["score"], "manual", [round(v, 2) for v in rect],
                       [round(v, 2) for v in rect], unit=native.unit, breakdown=sc,
                       reasons=sc["reasons"], codes=codes, order_id=_order_id(det),
                       orientation=orient, approved=True)


def refresh_crop(doc: fitz.Document, lbl: LabelResult, settings: Settings) -> None:
    """Re-derive the crop after a padding change (manual crops stay as drawn)."""
    if lbl.rect is None or lbl.method == "manual":
        return
    page = doc[lbl.page]
    crop = crop_rect_for(page, fitz.Rect(lbl.rect), lbl.border, lbl.unit, lbl.line_thickness,
                         settings.label_padding_pt)
    lbl.crop = [round(v, 2) for v in crop]


def restatus(lbl: LabelResult, settings: Settings) -> None:
    if lbl.status in ("detected", "review"):
        lbl.status = _status(lbl.confidence, settings)


# --------------------------------------------------------------------------- #
# Output generation
# --------------------------------------------------------------------------- #
def _safe(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", s).strip("_") or "label"


def generate_outputs(labels: list[LabelResult], docs: dict[int, fitz.Document],
                     settings: Settings, out_dir: str, filename: str,
                     progress=None) -> dict:
    os.makedirs(out_dir, exist_ok=True)
    chosen = [l for l in sorted(labels, key=lambda l: l.sort_key) if is_included(l, settings)]
    out = fitz.open()
    keep: list[fitz.Document] = []
    for n, lbl in enumerate(chosen, 1):
        src = docs[lbl.file_idx]
        crop = fitz.Rect(lbl.crop)
        iso, stripped = isolated_source(src, lbl.page, crop, settings.strip_hidden_content)
        keep.append(iso)
        info = place_label(out, iso, 0, crop, settings.page_margin_pt, settings.auto_rotate,
                           lbl.orientation)
        expected = [b["data"] for b in lbl.codes.get("barcodes", [])]
        info["barcode_check"] = verify_output_page(out[-1], expected, settings.verify_dpi)
        info["hidden_content_removed"] = stripped
        info["output_page"] = n
        lbl.output = info
        if progress:
            progress(n, len(chosen))
    for l in labels:
        if l not in chosen:
            l.output = {}

    if not filename.lower().endswith(".pdf"):
        filename += ".pdf"
    filename = _safe(filename[:-4]) + ".pdf"
    combined = os.path.join(out_dir, filename)
    out.set_metadata({"title": "4x6 Shipping Labels", "creator": APP_NAME, "producer": APP_NAME})
    if out.page_count:
        out.save(combined, garbage=3, deflate=True)

    # individual PDFs + ZIP
    ind_dir = os.path.join(out_dir, "labels")
    os.makedirs(ind_dir, exist_ok=True)
    zpath = os.path.join(out_dir, filename[:-4] + "_individual.zip")
    individual = {}
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as zf:
        for i, lbl in enumerate(chosen):
            one = fitz.open()
            one.insert_pdf(out, from_page=i, to_page=i)
            tag = lbl.order_id or f"{_safe(os.path.splitext(lbl.file_name)[0])}_p{lbl.page + 1}"
            if not lbl.order_id and lbl.index_on_page:
                tag += f"_{lbl.index_on_page + 1}"
            name = f"{i + 1:03d}_{tag}.pdf"
            p = os.path.join(ind_dir, name)
            one.save(p, garbage=3, deflate=True)
            one.close()
            zf.write(p, name)
            individual[lbl.uid] = p
    pages = out.page_count
    ok_size = all(abs(pg.rect.width - OUT_WIDTH_PT) < 0.01 and abs(pg.rect.height - OUT_HEIGHT_PT) < 0.01
                  for pg in out)
    out.close()
    for d in keep:
        d.close()
    return {"combined": combined if pages else None, "zip": zpath if pages else None,
            "individual": individual, "pages": pages, "filename": filename,
            "page_size_ok": ok_size}


def process_file(data: bytes, filename: str, settings: Settings) -> list[LabelResult]:
    """Convenience: analyse every page of one file (used by CLI/tests)."""
    from .pdf_parser import open_document
    doc = open_document(data, filename)
    res = []
    for pno in range(doc.page_count):
        res += analyse_page(doc, pno, 0, filename, settings)
    doc.close()
    return res
