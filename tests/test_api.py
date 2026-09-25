"""End-to-end API test: upload -> detect -> process -> download (batch of files)."""
from __future__ import annotations

import io
import os
import sys
import time
import zipfile

import pymupdf as fitz

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from tests.make_variants import build  # noqa: E402
from tests.test_variants import ORIGINAL, ground_truth_label  # noqa: E402


def _wait(c, jid, states=("ready", "done", "error"), timeout=120):
    t = time.time()
    while time.time() - t < timeout:
        j = c.get(f"/api/jobs/{jid}").json()
        if j["state"] in states:
            return j
        time.sleep(0.2)
    raise TimeoutError(j)


def test_batch_end_to_end():
    v = build(open(ORIGINAL, "rb").read(), ground_truth_label())
    files = [
        ("files", ("Flipkart_A.pdf", open(ORIGINAL, "rb").read(), "application/pdf")),
        ("files", ("Flipkart_B_3pages.pdf", v["03_multipage_3"][0], "application/pdf")),
        ("files", ("Flipkart_C_2up.pdf", v["04_two_up_landscape"][0], "application/pdf")),
        ("files", ("Invoice_only.pdf", v["09_invoice_only"][0], "application/pdf")),
    ]
    with TestClient(app) as c:
        cfg = c.get("/api/config").json()
        assert cfg["page_size_pt"] == [288.0, 432.0]
        j = c.post("/api/jobs", files=files).json()
        jid = j["job_id"]
        j = _wait(c, jid)
        assert j["state"] == "ready", j
        t = j["totals"]
        assert t["pages"] == 1 + 3 + 1 + 1
        assert t["labels_detected"] == 1 + 3 + 2, t
        assert t["needs_review"] == 1          # the invoice-only page is flagged
        failed = [l for l in j["labels"] if l["status"] == "failed"]
        assert failed and failed[0]["file_name"] == "Invoice_only.pdf"

        # page image + label preview
        r = c.get(f"/api/jobs/{jid}/pages/0/0/image.png")
        assert r.status_code == 200 and r.content[:4] == b"\x89PNG"
        uid = j["labels"][0]["uid"]
        r = c.get(f"/api/jobs/{jid}/labels/{uid}/preview.png")
        assert r.status_code == 200 and r.content[:4] == b"\x89PNG"

        # manual crop fallback on the failed page (draw roughly anything)
        f_idx, pno = failed[0]["file_idx"], failed[0]["page"]
        j = c.post(f"/api/jobs/{jid}/pages/{f_idx}/{pno}/manual",
                   json={"rect": [40, 390, 560, 740]}).json()
        man = [l for l in j["labels"] if l["status"] == "manual"]
        assert len(man) == 1 and man[0]["included"]
        # ...and exclude it again
        j = c.post(f"/api/jobs/{jid}/labels/{man[0]['uid']}/include", json={"include": False}).json()

        # re-detect one page with forced OCR path
        j = c.post(f"/api/jobs/{jid}/pages/0/0/redetect", json={"mode": "text"}).json()
        assert any(l["file_idx"] == 0 and l["method"] == "text-anchor" for l in j["labels"])

        # process
        name = "Flipkart_4x6_Labels_TEST.pdf"
        j = c.post(f"/api/jobs/{jid}/process", json={"filename": name,
                                                     "settings": {"page_margin_pt": 4}}).json()
        j = _wait(c, jid)
        assert j["state"] == "done", j
        assert j["outputs"]["pages"] == 6

        r = c.get(f"/api/jobs/{jid}/download/pdf")
        assert r.status_code == 200
        assert name in r.headers.get("content-disposition", "")
        doc = fitz.open("pdf", r.content)
        assert doc.page_count == 6
        for pg in doc:
            assert (round(pg.rect.width, 3), round(pg.rect.height, 3)) == (288.0, 432.0)
        # order preserved: file A p1, file B p1..p3, file C (left, right)
        texts = [pg.get_text() for pg in doc]
        assert all("Not for resale" in t for t in texts)
        assert not any("Tax Invoice" in t for t in texts)

        r = c.get(f"/api/jobs/{jid}/download/zip")
        z = zipfile.ZipFile(io.BytesIO(r.content))
        assert len(z.namelist()) == 6
        r = c.get(f"/api/jobs/{jid}/download/label/{j['labels'][0]['uid']}")
        assert r.status_code == 200 and fitz.open("pdf", r.content).page_count == 1

        # temp files removed on delete
        from app.main import store
        d = store.get(jid).dir
        assert os.path.isdir(d)
        c.delete(f"/api/jobs/{jid}")
        assert not os.path.exists(d)
        assert c.get(f"/api/jobs/{jid}").status_code == 404


if __name__ == "__main__":
    test_batch_end_to_end()
    print("API end-to-end: PASS")
