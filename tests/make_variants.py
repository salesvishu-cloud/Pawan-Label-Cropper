"""Builds test PDFs from the reference Flipkart PDF, each with the expected
label rectangle(s) in the new page's coordinates (ground truth).

The original reference label border is measured, not hard-coded, from
"Crop Label.pdf" vs "Original Lable.pdf" by the test itself; here we only
transform whatever label rect we are given.
"""
from __future__ import annotations

import io

import pymupdf as fitz


def _map(rect: fitz.Rect, src_rect: fitz.Rect, target: fitz.Rect) -> fitz.Rect:
    """Where rect (in a source page of size src_rect) lands when the page is
    shown into target with keep_proportion."""
    s = min(target.width / src_rect.width, target.height / src_rect.height)
    ox = target.x0 + (target.width - src_rect.width * s) / 2
    oy = target.y0 + (target.height - src_rect.height * s) / 2
    return fitz.Rect(ox + (rect.x0 - src_rect.x0) * s, oy + (rect.y0 - src_rect.y0) * s,
                     ox + (rect.x1 - src_rect.x0) * s, oy + (rect.y1 - src_rect.y0) * s)


def build(src_bytes: bytes, label: fitz.Rect) -> dict[str, tuple[bytes, list[list[fitz.Rect]]]]:
    """Returns {name: (pdf_bytes, [expected rects per page])}"""
    src = fitz.open("pdf", src_bytes)
    sp = src[0].rect
    out: dict[str, tuple[bytes, list]] = {}

    # 1. reference unchanged
    out["01_reference"] = (src_bytes, [[label]])

    # 2. US Letter page, content scaled 85% and shifted
    d = fitz.open(); p = d.new_page(width=612, height=792)
    t = fitz.Rect(50, 70, 50 + sp.width * 0.85, 70 + sp.height * 0.85)
    p.show_pdf_page(t, src, 0)
    out["02_letter_shifted_scaled"] = (d.tobytes(), [[_map(label, sp, t)]])

    # 3. multi-page: reference, shifted-right, small in bottom-right corner
    d = fitz.open(); exp = []
    d.insert_pdf(src); exp.append([label])
    p = d.new_page(width=sp.width, height=sp.height)
    t = fitz.Rect(120, 30, 120 + sp.width * 0.78, 30 + sp.height * 0.78)
    p.show_pdf_page(t, src, 0); exp.append([_map(label, sp, t)])
    p = d.new_page(width=sp.width, height=sp.height)
    t = fitz.Rect(sp.width * 0.4, sp.height * 0.4, sp.width - 10, sp.height - 10)
    p.show_pdf_page(t, src, 0); exp.append([_map(label, sp, t)])
    out["03_multipage_3"] = (d.tobytes(), exp)

    # 4. two full label+invoice pages side by side on landscape A4
    d = fitz.open(); p = d.new_page(width=842, height=595)
    t1, t2 = fitz.Rect(0, 0, 421, 595), fitz.Rect(421, 0, 842, 595)
    p.show_pdf_page(t1, src, 0); p.show_pdf_page(t2, src, 0)
    out["04_two_up_landscape"] = (d.tobytes(), [[_map(label, sp, t1), _map(label, sp, t2)]])

    # 5. four labels only (no invoice) in a 2x2 grid on A4
    d = fitz.open(); p = d.new_page(width=595, height=842)
    exp = []
    clip = fitz.Rect(label.x0 - 2, label.y0 - 2, label.x1 + 2, label.y1 + 2)
    for r in range(2):
        for c in range(2):
            t = fitz.Rect(20 + c * 287, 20 + r * 410, 20 + c * 287 + 270, 20 + r * 410 + 400)
            p.show_pdf_page(t, src, 0, clip=clip)
            exp.append(_map(label, clip, t))
    out["05_four_up_labels_only"] = (d.tobytes(), [exp])

    # 6. page stored sideways with /Rotate 90 (viewer shows it upright)
    d = fitz.open(); p = d.new_page(width=sp.height, height=sp.width)
    p.show_pdf_page(p.rect, src, 0, rotate=90)
    p.set_rotation(90)
    # expected: after un-rotation the page looks like the original
    out["06_rotated_page"] = (d.tobytes(), [[label]])

    # 6b. content printed upside down (label must come out upright)
    d = fitz.open(); p = d.new_page(width=sp.width, height=sp.height)
    p.show_pdf_page(p.rect, src, 0, rotate=180)
    flipped = fitz.Rect(sp.width - label.x1, sp.height - label.y1,
                        sp.width - label.x0, sp.height - label.y0)
    out["06b_upside_down_content"] = (d.tobytes(), [[flipped]])

    # 7. scanned: image-only page at 200 dpi (no text layer, no vectors)
    pix = src[0].get_pixmap(dpi=200, colorspace=fitz.csGRAY)
    d = fitz.open(); p = d.new_page(width=sp.width, height=sp.height)
    p.insert_image(p.rect, stream=pix.tobytes("png"))
    out["07_scanned_image_only"] = (d.tobytes(), [[label]])

    # 8. borderless: remove only the four outer border rules of the label
    d = fitz.open("pdf", src_bytes); p = d[0]
    borders = []
    for dr in p.get_drawings():
        r = fitz.Rect(dr["rect"])
        is_border = ((r.height < 2 and abs(r.width - label.width) < 2) and
                     (abs(r.y0 - label.y0) < 1.5 or abs(r.y1 - label.y1) < 1.5)) or \
                    ((r.width < 2 and abs(r.height - label.height) < 2) and
                     (abs(r.x0 - label.x0) < 1.5 or abs(r.x1 - label.x1) < 1.5))
        if is_border:
            borders.append(r)
    for r in borders:   # one at a time: MuPDF only removes fully covered paths
        p.add_redact_annot(r + (-1, -1, 1, 1), fill=False, cross_out=False)
        p.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE,
                           graphics=fitz.PDF_REDACT_LINE_ART_REMOVE_IF_COVERED,
                           text=fitz.PDF_REDACT_TEXT_NONE)
    # ground truth = what is still visible of the label once the border is gone
    vis = None
    for kind, bb in p.get_bboxlog():
        r = fitz.Rect(bb)
        if label.contains(r) and not r.is_empty:
            vis = r if vis is None else vis | r
    out["08_borderless_label"] = (d.tobytes(), [[vis]])

    # 9. invoice only (label removed) -> must report "not found"
    d = fitz.open("pdf", src_bytes); p = d[0]
    p.add_redact_annot(fitz.Rect(label.x0 - 3, label.y0 - 3, label.x1 + 3, label.y1 + 3))
    p.apply_redactions(images=fitz.PDF_REDACT_IMAGE_REMOVE,
                       graphics=fitz.PDF_REDACT_LINE_ART_REMOVE_IF_TOUCHED)
    out["09_invoice_only"] = (d.tobytes(), [[]])

    # 10. CropBox not at origin
    d = fitz.open("pdf", src_bytes); p = d[0]
    p.set_cropbox(fitz.Rect(25, 12, 580, 830))
    shifted = fitz.Rect(label.x0 - 25, label.y0 - 12, label.x1 - 25, label.y1 - 12)
    out["10_cropbox_offset"] = (d.tobytes(), [[shifted]])

    # 11. label image uploaded as PNG (converted to a PDF page internally)
    png = src[0].get_pixmap(dpi=220).tobytes("png")
    scale = 842.0 / sp.height
    out["11_png_upload"] = (png, [[fitz.Rect(label.x0 * scale, label.y0 * scale,
                                             label.x1 * scale, label.y1 * scale)]])
    return out
