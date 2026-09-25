"""FastAPI web app - Pawan Flipkart Label Cropper Automatically.

Run:  python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
Then open http://127.0.0.1:8000
"""
from __future__ import annotations

import datetime as dt
import json
import os
import threading
import time
from contextlib import asynccontextmanager

import pymupdf as fitz
from fastapi import Body, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from .config import (APP_NAME, APP_SHORT, APP_VERSION, DEFAULT_SETTINGS, OUT_HEIGHT_PT,
                     OUT_WIDTH_PT)
from .core import pipeline, preview
from .core.barcode import decoder_name
from .core.pdf_parser import IMAGE_EXTS, open_document
from .core.raster import ocr_available
from .jobs import FileEntry, Job, JobStore

STATIC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static")
store = JobStore()


def _janitor():
    while True:
        time.sleep(60)
        try:
            store.cleanup(DEFAULT_SETTINGS.job_ttl_minutes)
        except Exception:
            pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    threading.Thread(target=_janitor, daemon=True).start()
    yield
    store.close_all()


app = FastAPI(title=APP_NAME, version=APP_VERSION, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC), name="static")

# Optional password lock for online deployment: set PLC_ACCESS_PASSWORD
# (and optionally PLC_ACCESS_USER, default "pawan"). Local use needs nothing.
_ACCESS_PASSWORD = os.environ.get("PLC_ACCESS_PASSWORD", "")
_ACCESS_USER = os.environ.get("PLC_ACCESS_USER", "pawan")


@app.middleware("http")
async def _password_lock(request, call_next):
    if not _ACCESS_PASSWORD or request.url.path == "/healthz":
        return await call_next(request)
    import base64
    import secrets
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("basic "):
        try:
            user, _, pw = base64.b64decode(auth[6:]).decode("utf-8").partition(":")
            if secrets.compare_digest(user, _ACCESS_USER) and secrets.compare_digest(pw, _ACCESS_PASSWORD):
                return await call_next(request)
        except Exception:
            pass
    return Response("Login required", status_code=401,
                    headers={"WWW-Authenticate": 'Basic realm="Label Cropper"'})


@app.get("/healthz")
def healthz():
    return {"ok": True}


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _job(jid: str) -> Job:
    job = store.get(jid)
    if not job:
        raise HTTPException(404, "Session expired - please upload the PDF again")
    return job


def _label(job: Job, uid: str):
    for l in job.labels:
        if l.uid == uid:
            return l
    raise HTTPException(404, "Label not found")


def _summary(job: Job) -> dict:
    s = job.settings
    labels = sorted(job.labels, key=lambda l: l.sort_key)
    files = []
    for f in job.files:
        fl = [l for l in labels if l.file_idx == f.idx]
        files.append({
            "idx": f.idx, "name": f.name, "pages": f.pages, "error": f.error,
            "labels_detected": sum(1 for l in fl if l.status != "failed"),
            "needs_review": sum(1 for l in fl if l.status in ("review", "failed") and l.approved is None),
        })
    items = []
    for n, l in enumerate(labels, 1):
        d = l.to_dict()
        d["seq"] = n
        d["included"] = pipeline.is_included(l, s)
        items.append(d)
    outputs = {}
    if job.outputs:
        outputs = {
            "pages": job.outputs.get("pages", 0),
            "filename": job.outputs.get("filename"),
            "has_pdf": bool(job.outputs.get("combined")),
            "page_size_ok": job.outputs.get("page_size_ok"),
        }
    return {
        "job_id": job.id, "state": job.state, "progress": job.progress, "error": job.error,
        "settings": s.to_dict(), "files": files, "labels": items, "outputs": outputs,
        "totals": {
            "files": len(job.files),
            "pages": sum(f.pages for f in job.files),
            "labels_detected": sum(1 for l in labels if l.status != "failed"),
            "confident": sum(1 for l in labels if l.status in ("detected", "manual")),
            "needs_review": sum(1 for l in labels if l.status in ("review", "failed") and l.approved is None),
            "included": sum(1 for l in labels if pipeline.is_included(l, s)),
        },
    }


def _analyse_job(job: Job, force: str | None = None):
    try:
        total = sum(f.pages for f in job.files if not f.error)
        job.progress = {"done": 0, "total": total, "phase": "Detecting labels"}
        done = 0
        results = []
        for f in job.files:
            if f.error:
                continue
            doc = job.doc(f.idx)
            for pno in range(doc.page_count):
                with job.lock:
                    results += pipeline.analyse_page(doc, pno, f.idx, f.name, job.settings, force)
                done += 1
                job.progress = {"done": done, "total": total, "phase": "Detecting labels"}
        job.labels = results
        job.state = "ready"
    except Exception as e:  # pragma: no cover
        job.state, job.error = "error", f"Analysis failed: {e}"


def _process_job(job: Job, filename: str):
    try:
        def prog(n, t):
            job.progress = {"done": n, "total": t, "phase": "Building 4x6 PDF"}
        with job.lock:
            docs = job.docs()
            job.outputs = pipeline.generate_outputs(job.labels, docs, job.settings,
                                                    os.path.join(job.dir, "out"), filename, prog)
        job.state = "done"
    except Exception as e:  # pragma: no cover
        job.state, job.error = "error", f"Processing failed: {e}"


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #
@app.get("/", response_class=HTMLResponse)
def index():
    with open(os.path.join(STATIC, "index.html"), encoding="utf-8") as fh:
        return fh.read()


@app.get("/api/config")
def config():
    return {
        "app_name": APP_NAME, "short": APP_SHORT, "version": APP_VERSION,
        "defaults": DEFAULT_SETTINGS.to_dict(),
        "default_filename": f"Flipkart_4x6_Labels_{dt.date.today().isoformat()}.pdf",
        "ocr_available": ocr_available(), "barcode_decoder": decoder_name(),
        "page_size_pt": [OUT_WIDTH_PT, OUT_HEIGHT_PT],
    }


@app.post("/api/jobs")
async def create_job(files: list[UploadFile] = File(...), settings: str | None = Form(None)):
    overrides = json.loads(settings) if settings else {}
    job = store.create(DEFAULT_SETTINGS.merged(overrides))
    for i, up in enumerate(files):
        name = os.path.basename(up.filename or f"file_{i + 1}.pdf")
        data = await up.read()
        path = os.path.join(job.dir, f"src_{i:03d}{os.path.splitext(name)[1].lower() or '.pdf'}")
        with open(path, "wb") as fh:
            fh.write(data)
        entry = FileEntry(i, name, path)
        ext = os.path.splitext(name.lower())[1]
        if ext not in IMAGE_EXTS and ext != ".pdf":
            entry.error = "Unsupported file type (upload PDF files)"
        else:
            try:
                with open_document(data, name) as d:
                    entry.pages = d.page_count
            except Exception as e:
                entry.error = f"Could not open: {e}"
        job.files.append(entry)
    job.state = "analyzing"
    threading.Thread(target=_analyse_job, args=(job,), daemon=True).start()
    return _summary(job)


@app.get("/api/jobs/{jid}")
def get_job(jid: str):
    return _summary(_job(jid))


@app.post("/api/jobs/{jid}/settings")
def update_settings(jid: str, body: dict = Body(...)):
    job = _job(jid)
    with job.lock:
        old = job.settings
        job.settings = old.merged(body)
        if job.settings.label_padding_pt != old.label_padding_pt:
            for l in job.labels:
                pipeline.refresh_crop(job.doc(l.file_idx), l, job.settings)
        for l in job.labels:
            pipeline.restatus(l, job.settings)
        job.invalidate_outputs()
    return _summary(job)


@app.post("/api/jobs/{jid}/process")
def process(jid: str, body: dict = Body(default={})):
    job = _job(jid)
    if job.state in ("analyzing", "processing"):
        raise HTTPException(409, "Please wait - still working")
    with job.lock:
        settings = body.get("settings") or {}
        if settings:
            old = job.settings
            job.settings = old.merged(settings)
            if job.settings.label_padding_pt != old.label_padding_pt:
                for l in job.labels:
                    pipeline.refresh_crop(job.doc(l.file_idx), l, job.settings)
            for l in job.labels:
                pipeline.restatus(l, job.settings)
        job.invalidate_outputs()
        job.state = "processing"
    fname = body.get("filename") or f"Flipkart_4x6_Labels_{dt.date.today().isoformat()}.pdf"
    threading.Thread(target=_process_job, args=(job, fname), daemon=True).start()
    return _summary(job)


@app.post("/api/jobs/{jid}/labels/{uid}/include")
def include(jid: str, uid: str, body: dict = Body(...)):
    job = _job(jid)
    with job.lock:
        lbl = _label(job, uid)
        v = body.get("include")
        lbl.approved = None if v is None else bool(v)
        job.invalidate_outputs()
    return _summary(job)


@app.post("/api/jobs/{jid}/pages/{fidx}/{pno}/redetect")
def redetect(jid: str, fidx: int, pno: int, body: dict = Body(default={})):
    job = _job(jid)
    mode = body.get("mode") or None
    if mode not in (None, "vector", "text", "raster"):
        raise HTTPException(400, "bad mode")
    with job.lock:
        doc = job.doc(fidx)
        f = job.files[fidx]
        new = pipeline.analyse_page(doc, pno, fidx, f.name, job.settings, mode)
        job.labels = [l for l in job.labels if not (l.file_idx == fidx and l.page == pno)] + new
        job.invalidate_outputs()
    return _summary(job)


@app.post("/api/jobs/{jid}/pages/{fidx}/{pno}/manual")
def manual(jid: str, fidx: int, pno: int, body: dict = Body(...)):
    job = _job(jid)
    r = body.get("rect")
    if not r or len(r) != 4:
        raise HTTPException(400, "rect [x0,y0,x1,y1] in PDF points required")
    with job.lock:
        doc = job.doc(fidx)
        rect = fitz.Rect(*r)
        rect.normalize()
        if rect.width < 20 or rect.height < 20:
            raise HTTPException(400, "Crop area too small")
        replace = body.get("replace_uid")
        page_labels = [l for l in job.labels if l.file_idx == fidx and l.page == pno]
        if replace:
            old = _label(job, replace)
            idx = old.index_on_page
            job.labels.remove(old)
        else:
            # drop "failed" placeholders for this page; new label goes last on the page
            for l in page_labels:
                if l.status == "failed":
                    job.labels.remove(l)
            idx = max([l.index_on_page for l in page_labels if l.status != "failed"], default=-1) + 1
        lbl = pipeline.manual_label(doc, pno, rect, fidx, job.files[fidx].name, idx, job.settings)
        job.labels.append(lbl)
        job.invalidate_outputs()
    return _summary(job)


@app.get("/api/jobs/{jid}/pages/{fidx}/{pno}/info")
def page_info(jid: str, fidx: int, pno: int):
    job = _job(jid)
    doc = job.doc(fidx)
    if not 0 <= pno < doc.page_count:
        raise HTTPException(404, "page")
    r = doc[pno].rect
    labels = [l.to_dict() for l in sorted(job.labels, key=lambda l: l.sort_key)
              if l.file_idx == fidx and l.page == pno]
    return {"width": r.width, "height": r.height, "x0": r.x0, "y0": r.y0, "labels": labels,
            "file": job.files[fidx].name, "page": pno}


@app.get("/api/jobs/{jid}/pages/{fidx}/{pno}/image.png")
def page_image(jid: str, fidx: int, pno: int, dpi: int = 100):
    job = _job(jid)
    with job.lock:
        doc = job.doc(fidx)
        labels = sorted([l for l in job.labels if l.file_idx == fidx and l.page == pno],
                        key=lambda l: l.sort_key)
        png = preview.page_overlay_png(doc, pno, labels, max(50, min(dpi, 200)))
    return Response(png, media_type="image/png", headers={"Cache-Control": "no-store"})


@app.get("/api/jobs/{jid}/labels/{uid}/preview.png")
def label_preview(jid: str, uid: str, dpi: int = 110):
    job = _job(jid)
    with job.lock:
        lbl = _label(job, uid)
        if not lbl.crop:
            raise HTTPException(404, "No crop for this label")
        png = preview.label_preview_png(job.doc(lbl.file_idx), lbl.page, fitz.Rect(lbl.crop),
                                        job.settings.page_margin_pt, job.settings.auto_rotate,
                                        max(50, min(dpi, 220)), lbl.orientation)
    return Response(png, media_type="image/png", headers={"Cache-Control": "no-store"})


@app.get("/api/jobs/{jid}/download/pdf")
def download_pdf(jid: str, inline: int = 0):
    job = _job(jid)
    p = job.outputs.get("combined")
    if not p or not os.path.exists(p):
        raise HTTPException(404, "Process the labels first")
    if inline:
        return FileResponse(p, media_type="application/pdf",
                            headers={"Content-Disposition": f'inline; filename="{os.path.basename(p)}"'})
    return FileResponse(p, media_type="application/pdf", filename=os.path.basename(p))


@app.get("/api/jobs/{jid}/download/zip")
def download_zip(jid: str):
    job = _job(jid)
    p = job.outputs.get("zip")
    if not p or not os.path.exists(p):
        raise HTTPException(404, "Process the labels first")
    return FileResponse(p, media_type="application/zip", filename=os.path.basename(p))


@app.get("/api/jobs/{jid}/download/label/{uid}")
def download_label(jid: str, uid: str):
    job = _job(jid)
    p = job.outputs.get("individual", {}).get(uid)
    if not p or not os.path.exists(p):
        raise HTTPException(404, "Process the labels first")
    return FileResponse(p, media_type="application/pdf", filename=os.path.basename(p))


@app.delete("/api/jobs/{jid}")
def delete_job(jid: str):
    store.delete(jid)
    return JSONResponse({"deleted": True})
