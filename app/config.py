"""Central configuration for Pawan Flipkart Label Cropper Automatically.

Every value can be overridden with an environment variable of the same name
prefixed with ``PLC_`` (e.g. ``PLC_CONFIDENCE_THRESHOLD=85``).
"""
from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, asdict, fields

APP_NAME = "Pawan Flipkart Label Cropper Automatically"
APP_SHORT = "FLIPKART LABEL CROPPER"
APP_VERSION = "1.0.0"

# Exact 4 x 6 inch output page in PDF points (1 inch = 72 pt)
OUT_WIDTH_PT = 288.0
OUT_HEIGHT_PT = 432.0

TEMP_ROOT = os.environ.get(
    "PLC_TEMP_ROOT", os.path.join(tempfile.gettempdir(), "pawan_label_cropper")
)


def _env(name: str, default, cast):
    raw = os.environ.get("PLC_" + name.upper())
    if raw is None:
        return default
    try:
        if cast is bool:
            return raw.strip().lower() in ("1", "true", "yes", "on")
        return cast(raw)
    except ValueError:
        return default


@dataclass
class Settings:
    # Labels scoring below this (0-100) are flagged "Review" and held back
    confidence_threshold: float = 80.0
    # White border kept around the label on the 4x6 page (pt). 0 = edge to edge.
    page_margin_pt: float = 4.0
    # Extra space kept around the detected label border before scaling (pt)
    label_padding_pt: float = 2.0
    # Rotate landscape labels by 90 degrees so they fill the portrait 4x6 page
    auto_rotate: bool = True
    # Physically remove the invoice / other page content that sits outside the
    # crop, so it is not hidden inside the output PDF (verified pixel-exact)
    strip_hidden_content: bool = True
    # Put labels below the confidence threshold into the output anyway
    include_low_confidence: bool = False
    # DPI used for OCR / raster fallback detection
    ocr_dpi: int = 400
    # DPI used to verify barcodes on the finished 4x6 page (typical thermal = 203)
    verify_dpi: int = 203
    # Allow Tesseract OCR fallback (only used when the page has no usable text)
    enable_ocr: bool = True
    # Temporary uploads are wiped after this many minutes of inactivity
    job_ttl_minutes: int = 30

    @classmethod
    def from_env(cls) -> "Settings":
        s = cls()
        for f in fields(cls):
            setattr(s, f.name, _env(f.name, getattr(s, f.name), type(getattr(s, f.name))))
        return s

    def merged(self, overrides: dict | None) -> "Settings":
        data = asdict(self)
        for k, v in (overrides or {}).items():
            if k in data and v is not None:
                try:
                    data[k] = type(data[k])(v) if not isinstance(data[k], bool) else bool(v)
                except (TypeError, ValueError):
                    pass
        return Settings(**data)

    def to_dict(self) -> dict:
        return asdict(self)


DEFAULT_SETTINGS = Settings.from_env()
