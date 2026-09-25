# Pawan Flipkart Label Cropper Automatically

Upload Flipkart label PDFs → click **Process Labels** → get a print-ready **4 × 6 inch** shipping-label PDF.

The app detects the shipping label on every page automatically, drops the tax invoice, and places the *original* label content (vector text and the original barcode/QR images) on exact 288 × 432 pt pages. You never have to pick coordinates.

See **ANALYSIS.md** for the measurements of your reference PDFs and the full detection design.

---

## Quick start

### Windows
1. Install **Python 3.10+** from python.org and tick *"Add python.exe to PATH"*.
2. Unzip this folder and double-click **`run_windows.bat`**. The first run installs packages, which takes about a minute.
3. The browser opens at **http://127.0.0.1:8000**.

### macOS / Linux
```bash
./run.sh            # or: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
                    #     .venv/bin/python -m uvicorn app.main:app --port 8000
```

### Optional: OCR for scanned or photographed labels
Normal Flipkart PDFs **do not need OCR**. It is only used when a page is an image, such as a scan or phone photo.
* Windows: install Tesseract from https://github.com/UB-Mannheim/tesseract/wiki (default folder is detected automatically).
* macOS: `brew install tesseract` · Ubuntu: `sudo apt install tesseract-ocr`

The header shows **"OCR fallback: on / not installed"**.

### GitHub + live website
See **GITHUB_AUR_LIVE_GUIDE.md**: `upload_to_github.bat` pushes the code (customer PDFs are excluded by `.gitignore`), and `render.yaml` + `Dockerfile` deploy it on Render.com. Set `PLC_ACCESS_PASSWORD` to lock the online version.

---

## Using it

1. **Upload Flipkart PDF** or drag & drop. Multiple files are fine.
2. The table shows **File · Pages · Labels Detected · Needs Review**. Each label card shows **Shipping Label Detection %**, a preview, and a barcode check.
3. If a page scores below the threshold, you'll see **"Label detection confidence is low for Page X"** with a **Review Page** button. From there you can:
   * approve or exclude the label,
   * re-detect with another method (keyword region / image analysis + OCR), or
   * drag a **manual crop** as a fallback.
4. **Process Labels** → **Processed: N/N** → **Download 4×6 PDF**, **Download Individual Labels (ZIP)**, or **Print**.
5. Output settings (collapsible):

   | Setting | Default |
   |---|---|
   | File name | `Flipkart_4x6_Labels_YYYY-MM-DD.pdf` |
   | Confidence threshold | 80% |
   | Page margin | 4 pt |
   | Label padding | 2 pt |
   | Rotate landscape labels | on |
   | Remove hidden invoice data | on |
   | Include low-confidence labels | off |

**Printing:** in the print dialog choose your thermal printer, paper **4 × 6 in (100 × 150 mm)**, scale **100% / Actual size**. Do not use "fit to page".

**Order:** output pages follow the upload order: file 1 page 1, page 2, …, then file 2. Several labels on one page come out top-to-bottom, left-to-right.

### Command line (optional)
```bash
python cli.py Flipkart_Orders.pdf other.pdf -o Flipkart_4x6_Labels.pdf --zip
```

---

## Privacy
* Everything runs **locally** on 127.0.0.1. PDFs are never sent to any third-party service, and OCR (Tesseract) runs locally too.
* Uploads live in a temporary folder only while you review them. They are deleted when you press **Clear / New batch**, after 30 idle minutes, and when the app stops.
* With *Remove hidden invoice data* on, the output PDF contains only the label. The invoice text and images are deleted, not just hidden.

---

## Project layout (modules)

| Requested module | File |
|---|---|
| /upload, /download, /preview API | `app/main.py` (FastAPI routes), `app/jobs.py` (temp storage + auto-delete) |
| /pdf-parser | `app/core/pdf_parser.py`: text lines, words, vector rules (clip-aware), images, rotation normalising |
| /label-detector | `app/core/label_detector.py`: vector border → keyword region → raster/OCR; `app/core/anchors.py`: keywords |
| confidence | `app/core/confidence.py` |
| /crop-engine | `app/core/crop_engine.py`: ink trim, padding, cut-through check, verified invoice removal |
| /4x6-renderer | `app/core/renderer_4x6.py`: exact 288 × 432 pt, uniform scale, centring, rotation |
| /barcode-preservation | `app/core/barcode.py`: barcode/QR detection and 203 dpi output verification |
| /pdf-generator | `app/core/pipeline.py`: `generate_outputs` (combined PDF, individual PDFs, ZIP) |
| /preview | `app/core/preview.py`: thumbnails and page overlays |
| image analysis / OCR | `app/core/raster.py`: OpenCV rules, ink components, Tesseract |
| UI | `static/index.html`, `static/app.js`, `static/style.css` |

Detection logic is independent of the UI. `app/core` can be used from the CLI, tests or other code.

## Configuration
Every setting in `app/config.py` can be overridden with an environment variable prefixed `PLC_`. For example:

```bash
PLC_CONFIDENCE_THRESHOLD=85
PLC_PAGE_MARGIN_PT=0
PLC_JOB_TTL_MINUTES=10
```

## Tests
```bash
python -m pytest -q tests          # or run the files directly for a readable report
python tests/test_variants.py
```

`tests/test_variants.py` takes the ground truth from your `samples/Crop_Label.pdf` and generates 12 scenarios from `samples/Original_Lable.pdf`. All 12 pass:

| # | Scenario | Result |
|---|---|---|
| 1 | Reference file | 100% |
| 2 | US-Letter page, shifted and scaled | 100% |
| 3 | 3-page file with different positions and scales | 100% |
| 4 | Two label+invoice pages side-by-side on one sheet | 100% |
| 5 | 4 labels per A4 sheet | 97% |
| 6 | Page stored with /Rotate | 100% |
| 6b | Upside-down content (output comes out upright) | 100% |
| 7 | Scanned image-only page (OCR) | 100% |
| 8 | Label with its outer border removed | 88% |
| 9 | Invoice-only page | correctly reported "not found" |
| 10 | CropBox offset | 100% |
| 11 | PNG image upload | 100%; barcode marginal at 203 dpi, reads at 300 dpi |

`tests/test_api.py` runs a full batch through the web API: upload, detect, manual crop, re-detect, process, then download the PDF, ZIP and a single label, and checks that temp files are deleted.

## Limitations
* Detection has been validated on your sample format and the generated variants above. If Flipkart changes the label layout, keywords can be added in `app/core/anchors.py`.
* Orientation correction uses the PDF text layer. For image-only uploads that are sideways, rotate the scan first or use a manual crop.
* A low-confidence page is never auto-included unless you approve it or enable "Include low-confidence labels".
