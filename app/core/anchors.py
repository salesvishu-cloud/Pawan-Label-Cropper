"""Known Flipkart shipping-label and tax-invoice keywords (anchors)."""
from __future__ import annotations

import re
from dataclasses import dataclass

import pymupdf as fitz

from .pdf_parser import TextLine

# Keywords that belong to the SHIPPING LABEL.
#   strong = never appears in the tax invoice, safe to seed a label region
#   unique = appears once per label (a region containing two => two labels)
LABEL_ANCHORS: dict[str, dict] = {
    "awb":             {"re": r"\bAWB\b",                              "strong": True,  "unique": False},
    "ship_address":    {"re": r"shipping\s*/\s*customer\s*address",     "strong": True,  "unique": True},
    "sku_id":          {"re": r"\bSKU\s*ID\b",                          "strong": True,  "unique": False},
    "hbd":             {"re": r"\bHBD\b",                               "strong": True,  "unique": True},
    "cpd":             {"re": r"\bCPD\b",                               "strong": True,  "unique": True},
    "not_for_resale":  {"re": r"not\s*for\s*re-?\s*sale",                  "strong": True,  "unique": True},
    "printed_at":      {"re": r"printed\s*at\b",                        "strong": True,  "unique": True},
    "reg":             {"re": r"^\s*REG\b",                             "strong": True,  "unique": False},
    "payment":         {"re": r"\b(PREPAID|COD|PRE-PAID)\b",            "strong": False, "unique": False},
    "order_id":        {"re": r"\b[O0]\s?D\s?\d{10,}\b",                         "strong": False, "unique": False},
    "qty":             {"re": r"\bQTY\b",                               "strong": False, "unique": False},
    "ordered_through": {"re": r"ordered\s*through",                     "strong": False, "unique": False},
    "sold_by":         {"re": r"sold\s*by",                             "strong": False, "unique": False},
    "gstin":           {"re": r"\bGSTIN\b",                             "strong": False, "unique": False},
}

# Keywords that prove we are looking at the TAX INVOICE (must be excluded)
INVOICE_ANCHORS: dict[str, str] = {
    "tax_invoice":      r"tax\s*invoice",
    "invoice_no":       r"invoice\s*(no|number|date)\b",
    "billing_address":  r"billing\s*address",
    "authorized_sign":  r"authori[sz]ed\s*signat",
    "total_price":      r"total\s*price",
    "taxable_value":    r"taxable",
    "gross_amount":     r"gross\s*amount",
    "eoe":              r"\bE\.?\s*&\s*O\.?\s*E\b",
    "conditions_apply": r"conditions\s*apply",
    "seller_reg_addr":  r"seller\s*registered",
    "all_values_inr":   r"values\s*are\s*in\s*INR",
}

# Anchors counted for the "keywords found" part of the confidence score
CORE_KEYWORDS = ["awb", "ship_address", "sku_id", "hbd", "cpd", "not_for_resale",
                 "printed_at", "payment", "order_id", "qty", "ordered_through", "reg"]

_LABEL_RE = {k: re.compile(v["re"], re.I) for k, v in LABEL_ANCHORS.items()}
_LABEL_RE["reg"] = re.compile(LABEL_ANCHORS["reg"]["re"])          # case-sensitive
_LABEL_RE["payment"] = re.compile(LABEL_ANCHORS["payment"]["re"])  # case-sensitive
_INVOICE_RE = {k: re.compile(v, re.I) for k, v in INVOICE_ANCHORS.items()}


@dataclass
class Anchor:
    name: str
    rect: fitz.Rect
    text: str
    kind: str   # 'label' | 'invoice'

    @property
    def strong(self) -> bool:
        return self.kind == "label" and LABEL_ANCHORS[self.name]["strong"]

    @property
    def unique(self) -> bool:
        return self.kind == "label" and LABEL_ANCHORS[self.name]["unique"]


def find_anchors(lines: list[TextLine]) -> tuple[list[Anchor], list[Anchor]]:
    label, invoice = [], []
    for ln in lines:
        for name, rx in _LABEL_RE.items():
            if rx.search(ln.text):
                label.append(Anchor(name, ln.rect, ln.text, "label"))
        for name, rx in _INVOICE_RE.items():
            if rx.search(ln.text):
                invoice.append(Anchor(name, ln.rect, ln.text, "invoice"))
    return label, invoice


def anchors_in(rect: fitz.Rect, anchors: list[Anchor], tol: float = 1.0) -> list[Anchor]:
    """Anchors whose centre lies inside rect."""
    r = fitz.Rect(rect.x0 - tol, rect.y0 - tol, rect.x1 + tol, rect.y1 + tol)
    out = []
    for a in anchors:
        c = fitz.Point((a.rect.x0 + a.rect.x1) / 2, (a.rect.y0 + a.rect.y1) / 2)
        if r.contains(c):
            out.append(a)
    return out


def distinct(anchors: list[Anchor]) -> set[str]:
    return {a.name for a in anchors}


def has_duplicate_unique(anchors: list[Anchor]) -> bool:
    """True when anchors contain two copies of a once-per-label keyword."""
    seen: dict[str, list[fitz.Rect]] = {}
    for a in anchors:
        if not a.unique:
            continue
        for r in seen.get(a.name, []):
            # same line reported twice is not a duplicate
            if abs(r.y0 - a.rect.y0) > 2 or abs(r.x0 - a.rect.x0) > 2:
                return True
        seen.setdefault(a.name, []).append(a.rect)
    return False
