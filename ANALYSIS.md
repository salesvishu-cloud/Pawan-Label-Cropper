# Reference PDF analysis — Pawan Flipkart Label Cropper Automatically

Measured from the two supplied files, **Original Lable.pdf** and **Crop Label.pdf**, with PyMuPDF (native PDF objects, no OCR). 1 pt = 1/72 inch.

## 1. Original PDF page dimensions

| Property | Value |
|---|---|
| Page size | **595 × 842 pt** = A4 (8.26 × 11.69 in, 210 × 297 mm) |
| Pages / rotation | 1 page, /Rotate 0, MediaBox = CropBox |
| Generator | wkhtmltopdf 0.12.6 (Qt 4.8.7) |
| Content | Real selectable text (302 words), 89 vector drawings, 5 embedded images (7 placements) |
| Label images | AWB barcode 210×30 px (used twice: vertical and bottom), Flipkart logo 145×33 px, 2-D code 139×139 px |
| Invoice images | invoice QR 120×120 px, signature JPEG 29×60 px |

Everything is native PDF content, so detection can use exact coordinates. OCR is not needed for this file.

## 2. Shipping-label bounding box

The label is a **closed rectangle drawn from four thin black rules (0.75 pt filled rectangles)**:

| Edge | Coordinate (pt) |
|---|---|
| Left | x = 190.5 |
| Right | x = 404.25 |
| Top | y = 28.5 |
| Bottom | y = 381.0 |

**Size: 213.75 × 352.5 pt = 2.97 × 4.90 in (75.4 × 124.4 mm).**

Inside the border, full-width rules split the label into bands at y = 51.75 (header), 246.0, 277.5, 334.5 and 366.0. A vertical rule at x = 262 separates the barcode column from the QR/address column.

**Crop Label.pdf** is the *same page* with its MediaBox moved to x 186.0–407.6, y 22.3–388.3. That gives a 221.6 × 366.0 pt page (3.08 × 5.08 in). It adds about 4 pt of margin and includes a sliver of the dashed cut line at the bottom. The whole tax invoice is still inside that file, hidden outside the page box.

Measuring the border inside Crop Label.pdf and aligning it with the original gives exactly **(190.5, 28.5, 404.25, 381.0)**. The test suite uses this as ground truth.

## 3. Invoice starting position

| Marker | Position |
|---|---|
| Dashed "cut here" line | y = 385.5 pt, x 36 → 554, 1.5 pt stroke, dash 4.5/4.5 |
| "Tax Invoice" heading | y = 392.1 pt (x = 51.8) |
| Gap from label border to cut line | 4.5 pt |

Everything from y ≈ 385 downward is invoice: order/invoice numbers, Sold By, Billing Address, Shipping ADDRESS, product table, totals, signature and QR.

## 4. Label aspect ratio

* Label: 213.75 / 352.5 = **0.606** (1 : 1.65), which is taller than 4 × 6.
* 4 × 6 page: 288 / 432 = **0.667** (1 : 1.5).

So height is the limiting side. With the default 4 pt page margin and 2 pt padding, the label scales by **1.19×**. It prints at **3.59 × 5.89 in**, centred, with about 0.2 in of white at each side. Nothing is stretched.

## 5. How the label can be detected automatically

The label stands out through several independent signals, and none of them is a fixed coordinate:

1. **Structure.** It is the *outermost closed rectangle* on the page that contains shipping keywords and no invoice keywords.
2. **Keywords with positions.** The label contains REG, the order ID `OD…`, PREPAID/COD, "Ordered through", AWB No., "Shipping/Customer address", HBD, CPD, Sold By, GSTIN, "SKU ID | Description", QTY, "Not for resale" and "Printed at". The invoice contains "Tax Invoice", "Invoice No/Date", "Billing Address", "Taxable", "Gross Amount", "TOTAL PRICE", "Authorized Signature" and "E. & O.E.".
3. **Separator.** A long dashed line and the "Tax Invoice" heading mark where the invoice begins.
4. **Codes.** A CODE128 AWB barcode (decodes to the AWB number) and a square 2-D matrix code sit inside the label. The invoice has its own QR code outside it.

## 6. Recommended (implemented) detection algorithm

Each page is processed independently. The first strategy that succeeds wins, and every result is then scored.

1. **Vector border (primary).**
   * Read all drawing paths, respecting clip paths so hidden content is ignored.
   * Turn thin filled rectangles and stroked lines into horizontal and vertical segments, then merge collinear pieces.
   * Rebuild every closed rectangle: a pair of horizontals with matching ends, plus verticals covering at least 90% of each side with no break over 8 pt.
   * A rectangle counts as a label only if it holds at least 2 strong label keywords and at least 3 distinct ones, has **no** invoice keyword, and has no duplicated once-per-label keyword (that would mean two labels).
   * Keep the **outermost** qualifying rectangles. That gives one per label, so 2-up and 4-up sheets work.
2. **Keyword region (no border).** Grow a region outward from strong keywords through neighbouring text, images and graphics within 8 pt. It never crosses the dashed cut line or the start of the invoice.
3. **Image analysis + OCR (scans/photos).**
   * OpenCV morphology finds ruling lines, and the same rectangle logic runs on them.
   * Local Tesseract (PSM 6 at 400 dpi) reads keywords, only inside the candidate boxes to keep it fast.
   * This path only runs when the page has no usable text or carries a large image.
4. **Clean-up.**
   * Trim blank margins to the actual ink (the crop only ever shrinks).
   * Add padding (defined in output points).
   * Correct orientation from the label's dominant text direction, so upside-down or sideways pages come out upright.
5. **Confidence (0–100).**

   | Signal | Points |
   |---|---|
   | Keywords found | 30 |
   | Rectangular border | 20 |
   | Barcode decoded | 15 |
   | QR / 2-D code | 10 |
   | Structure (header at top, footer at bottom, AWB/address inside) | 15 |
   | Invoice detected separately and excluded | 10 |

   Penalties: invoice text inside the crop −40; crop edge cuts words or visible ink −20; label text just outside the crop −15; implausibly small −20. Below the threshold (default 80%) the page is flagged *"Label detection confidence is low for Page X"* and held back until reviewed. Pages with no label are flagged too.

The reference scores **100%** by the vector-border method. The detected box matches the ground truth at IoU 0.998; the only difference is the half-width of the border line.

## 7. How barcode / QR quality is preserved

* **No screenshots.** The label is placed with `show_pdf_page`, so text and rules stay vector. The barcode, logo and 2-D code are the *original embedded images at their native resolution*, byte-for-byte. Nothing is re-rendered, re-compressed, sharpened or regenerated.
* **Uniform scaling only.** No stretching, so bar-width ratios are unchanged.
* **Invoice removed for real.** With "Remove hidden invoice data" on, content outside the crop is deleted with redactions, not just hidden. The label area is then re-rendered and compared pixel-for-pixel with the original. If anything differs, the untouched copy is used instead.
* **Verified after output.** Each finished 4 × 6 page is decoded at **203 dpi** (typical thermal-printer resolution) and must return the same AWB number. Results show as "Barcode ✓". "⚠ marginal" means it reads only at 300 dpi, which only happens when the source barcode was already a low-resolution image, such as a phone photo.
* Raster work (OCR, previews) is used for *detection and display only* and never goes into the output PDF.

## 8. How the label becomes exactly 4 × 6 inches

1. Create a new PDF page with MediaBox **288 × 432 pt**. That is a real 4 × 6 in page, not an image that happens to look like one.
2. If the label is landscape (after orientation correction), turn it 90° so it fills the portrait page.
3. `scale = min((288 − 2·margin) / w, (432 − 2·margin) / h)`: one factor for both axes.
4. Centre the scaled label and draw the original page region into that rectangle with `show_pdf_page(clip=crop)`.
5. Write one page per label, in original order (file → page → reading order on the page). The output is one combined PDF, plus optional individual PDFs in a ZIP.

The result for Original Lable.pdf is one 288 × 432 pt page. It contains the whole label from REG down to "Printed at 1511 hrs, 24/09/26", with no dashed line, no invoice text and no invoice images inside the file.
