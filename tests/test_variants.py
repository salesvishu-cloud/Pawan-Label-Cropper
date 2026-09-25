"""Ground-truth tests.

Run:  python -m pytest -q tests      (or)   python tests/test_variants.py

Ground truth comes from the user's own files: "Crop Label.pdf" is the original
page with its page box moved onto the label, so aligning the two files gives the
exact label border in original-page coordinates.
"""
from __future__ import annotations

import os
import re
import sys
import time

import pymupdf as fitz

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.config import Settings  # noqa: E402
from app.core.anchors import INVOICE_ANCHORS  # noqa: E402
from app.core.pdf_parser import open_document  # noqa: E402
from app.core.pipeline import analyse_page, generate_outputs  # noqa: E402
from tests.make_variants import build  # noqa: E402

REF_DIR = os.environ.get("PLC_REF_DIR", os.path.join(ROOT, "samples"))
ORIGINAL = os.path.join(REF_DIR, "Original_Lable.pdf")
CROPPED = os.path.join(REF_DIR, "Crop_Label.pdf")
OUT = os.environ.get("PLC_TEST_OUT", os.path.join(ROOT, "tests", "_out"))
INV_RE = re.compile("|".join(INVOICE_ANCHORS.values()), re.I)


def ground_truth_label() -> fitz.Rect:
    """Label border in Original_Lable.pdf coordinates, measured from Crop_Label.pdf."""
    o, c = fitz.open(ORIGINAL)[0], fitz.open(CROPPED)[0]
    ro = o.search_for("Not for resale")[0]
    rc = c.search_for("Not for resale")[0]
    dx, dy = ro.x0 - rc.x0, ro.y0 - rc.y0
    border = None
    for dr in c.get_drawings():
        r = fitz.Rect(dr["rect"])
        if dr.get("fill") == (0.0, 0.0, 0.0) and c.rect.contains(r):
            border = r if border is None else border | r
    return fitz.Rect(border.x0 + dx, border.y0 + dy, border.x1 + dx, border.y1 + dy)


def iou(a: fitz.Rect, b: fitz.Rect) -> float:
    inter = fitz.Rect(a) & b
    if inter.is_empty:
        return 0.0
    ia = inter.get_area()
    return ia / (a.get_area() + b.get_area() - ia)


def run_all(verbose: bool = True) -> list[dict]:
    label = ground_truth_label()
    variants = build(open(ORIGINAL, "rb").read(), label)
    settings = Settings()
    rows = []
    for name, (data, expected) in variants.items():
        fname = name + (".png" if name.endswith("png_upload") else ".pdf")
        t0 = time.time()
        doc = open_document(data, fname)
        labels = []
        for pno in range(doc.page_count):
            labels += analyse_page(doc, pno, 0, fname, settings)
        dt = time.time() - t0
        problems = []
        ious, confs = [], []
        for pno, exp in enumerate(expected):
            got = [l for l in labels if l.page == pno and l.status != "failed"]
            if len(got) != len(exp):
                problems.append(f"page {pno + 1}: expected {len(exp)} label(s), got {len(got)}")
                continue
            page = doc[pno]
            unit = max(page.rect.width, page.rect.height) / 842
            for e, g in zip(sorted(exp, key=lambda r: (round(r.y0 / 50), r.x0)), got):
                det, crop = fitz.Rect(g.rect), fitz.Rect(g.crop)
                ious.append(iou(det, e))
                confs.append(g.confidence)
                tol = 1.5 * unit
                if not (crop.x0 <= e.x0 + tol and crop.y0 <= e.y0 + tol and
                        crop.x1 >= e.x1 - tol and crop.y1 >= e.y1 - tol):
                    problems.append(f"page {pno + 1}: crop {list(map(round, crop))} cuts label {list(map(round, e))}")
                words = [w for w in page.get_text("words") if crop.contains(fitz.Rect(w[:4]))]
                text = " ".join(w[4] for w in words)
                if INV_RE.search(text):
                    problems.append(f"page {pno + 1}: invoice text inside crop")
                if g.status != "detected":
                    problems.append(f"page {pno + 1}: status {g.status} ({g.confidence}%) {g.reasons}")
        # build outputs
        n_expected = sum(len(e) for e in expected)
        res = generate_outputs(labels, {0: doc}, settings, os.path.join(OUT, name), name)
        if res["pages"] != n_expected:
            problems.append(f"output pages {res['pages']} != {n_expected}")
        if res["pages"] and not res["page_size_ok"]:
            problems.append("output page size is not 288x432 pt")
        bc = [l.output.get("barcode_check", {}) for l in labels if l.output]
        bc_ok = all(b.get("level") in ("ok", "marginal") for b in bc if b.get("checked"))
        if not bc_ok:
            problems.append("barcode not readable on 4x6 output")
        marginal = any(b.get("level") == "marginal" for b in bc)
        methods = sorted({l.method for l in labels}) + (["barcode:marginal@203dpi"] if marginal else [])
        row = {"name": name, "pages": doc.page_count, "expected": n_expected,
               "detected": sum(1 for l in labels if l.status != "failed"),
               "min_iou": round(min(ious), 4) if ious else None,
               "min_conf": min(confs) if confs else None, "methods": methods,
               "barcode_ok": bc_ok, "seconds": round(dt, 2), "problems": problems,
               "failed_flagged": sum(1 for l in labels if l.status == "failed")}
        rows.append(row)
        if verbose:
            flag = "PASS" if not problems else "FAIL"
            print(f"{flag} {name:26s} pages={row['pages']} exp={n_expected} det={row['detected']} "
                  f"IoU={row['min_iou']} conf={row['min_conf']} {methods} {row['seconds']}s")
            for p in problems:
                print("     -", p)
        doc.close()
    return rows


def test_ground_truth_matches_reference():
    gt = ground_truth_label()
    doc = fitz.open(ORIGINAL)
    labels = analyse_page(doc, 0, 0, "Original_Lable.pdf", Settings())
    assert len(labels) == 1 and labels[0].status == "detected"
    assert iou(fitz.Rect(labels[0].rect), gt) > 0.99


def test_all_variants():
    rows = run_all(verbose=False)
    bad = {r["name"]: r["problems"] for r in rows if r["problems"]}
    assert not bad, bad


if __name__ == "__main__":
    print("Ground-truth label rect:", ground_truth_label())
    rows = run_all()
    print(f"\n{sum(1 for r in rows if not r['problems'])}/{len(rows)} scenarios passed")
