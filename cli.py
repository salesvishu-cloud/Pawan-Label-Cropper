"""Command-line batch mode (no browser needed).

    python cli.py Flipkart_Orders.pdf more.pdf -o Flipkart_4x6_Labels.pdf
    python cli.py *.pdf --zip --include-low

Low-confidence / not-found pages are listed and (by default) left out.
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import os
import shutil
import sys
import tempfile

from app.config import DEFAULT_SETTINGS
from app.core.pdf_parser import open_document
from app.core.pipeline import analyse_page, generate_outputs, is_included


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Pawan Flipkart Label Cropper Automatically (CLI)")
    ap.add_argument("inputs", nargs="+", help="Flipkart PDF files (wildcards allowed)")
    ap.add_argument("-o", "--output", default=f"Flipkart_4x6_Labels_{dt.date.today().isoformat()}.pdf")
    ap.add_argument("--threshold", type=float, default=DEFAULT_SETTINGS.confidence_threshold)
    ap.add_argument("--margin", type=float, default=DEFAULT_SETTINGS.page_margin_pt)
    ap.add_argument("--padding", type=float, default=DEFAULT_SETTINGS.label_padding_pt)
    ap.add_argument("--include-low", action="store_true", help="include low-confidence labels")
    ap.add_argument("--no-rotate", action="store_true")
    ap.add_argument("--keep-hidden", action="store_true", help="do not strip content outside the crop")
    ap.add_argument("--zip", action="store_true", help="also write a ZIP of individual label PDFs")
    a = ap.parse_args(argv)

    files = []
    for pat in a.inputs:
        files += sorted(glob.glob(pat)) or [pat]
    settings = DEFAULT_SETTINGS.merged({
        "confidence_threshold": a.threshold, "page_margin_pt": a.margin,
        "label_padding_pt": a.padding, "include_low_confidence": a.include_low,
        "auto_rotate": not a.no_rotate, "strip_hidden_content": not a.keep_hidden})

    docs, labels = {}, []
    for i, f in enumerate(files):
        with open(f, "rb") as fh:
            doc = open_document(fh.read(), f)
        docs[i] = doc
        for p in range(doc.page_count):
            labels += analyse_page(doc, p, i, os.path.basename(f), settings)
        n = sum(1 for l in labels if l.file_idx == i and l.status != "failed")
        print(f"{os.path.basename(f)}: {doc.page_count} page(s), {n} label(s) detected")

    for l in labels:
        if l.status == "failed":
            print(f"  ! {l.file_name} page {l.page + 1}: no shipping label detected")
        elif l.status == "review":
            print(f"  ! {l.file_name} page {l.page + 1}: label detection confidence is low "
                  f"({l.confidence:.0f}%) - {'; '.join(l.reasons[:2])}")
        else:
            print(f"    {l.file_name} page {l.page + 1}: Shipping Label Detection {l.confidence:.0f}% "
                  f"({l.method})")

    tmp = tempfile.mkdtemp(prefix="plc_cli_")
    try:
        res = generate_outputs(labels, docs, settings, tmp, os.path.basename(a.output))
        if not res["pages"]:
            print("No labels to write.")
            return 2
        shutil.copy(res["combined"], a.output)
        print(f"\nProcessed: {res['pages']}/{sum(1 for l in labels if l.status != 'failed')} "
              f"-> {a.output} (4 x 6 in pages)")
        if a.zip:
            z = os.path.splitext(a.output)[0] + "_individual.zip"
            shutil.copy(res["zip"], z)
            print(f"Individual labels: {z}")
        held = [l for l in labels if l.status == "review" and not is_included(l, settings)]
        if held:
            print(f"{len(held)} low-confidence label(s) held back - use the web app to review, "
                  f"or rerun with --include-low")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        for d in docs.values():
            d.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
