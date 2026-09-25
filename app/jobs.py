"""Temporary, in-memory job store.

Uploaded PDFs live only in a per-job temp folder while you review them, and
are deleted when you press "Clear", after the idle TTL, or when the server
stops.  Nothing is sent anywhere else.
"""
from __future__ import annotations

import os
import shutil
import threading
import time
import uuid
from dataclasses import dataclass, field

import pymupdf as fitz

from .config import TEMP_ROOT, Settings
from .core.pdf_parser import open_document


@dataclass
class FileEntry:
    idx: int
    name: str
    path: str
    pages: int = 0
    error: str = ""


@dataclass
class Job:
    id: str
    dir: str
    settings: Settings
    files: list[FileEntry] = field(default_factory=list)
    labels: list = field(default_factory=list)
    state: str = "created"            # analyzing | ready | processing | done | error
    progress: dict = field(default_factory=lambda: {"done": 0, "total": 0, "phase": ""})
    outputs: dict = field(default_factory=dict)
    error: str = ""
    touched: float = field(default_factory=time.time)
    lock: threading.RLock = field(default_factory=threading.RLock)
    _docs: dict = field(default_factory=dict)

    def doc(self, file_idx: int) -> fitz.Document:
        with self.lock:
            d = self._docs.get(file_idx)
            if d is None:
                f = self.files[file_idx]
                with open(f.path, "rb") as fh:
                    d = open_document(fh.read(), f.name)
                self._docs[file_idx] = d
            return d

    def docs(self) -> dict[int, fitz.Document]:
        return {f.idx: self.doc(f.idx) for f in self.files if not f.error}

    def invalidate_outputs(self) -> None:
        self.outputs = {}
        out_dir = os.path.join(self.dir, "out")
        shutil.rmtree(out_dir, ignore_errors=True)
        if self.state == "done":
            self.state = "ready"

    def close(self) -> None:
        for d in self._docs.values():
            try:
                d.close()
            except Exception:
                pass
        self._docs.clear()
        shutil.rmtree(self.dir, ignore_errors=True)


class JobStore:
    def __init__(self):
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        shutil.rmtree(TEMP_ROOT, ignore_errors=True)       # wipe leftovers
        os.makedirs(TEMP_ROOT, exist_ok=True)

    def create(self, settings: Settings) -> Job:
        jid = uuid.uuid4().hex
        d = os.path.join(TEMP_ROOT, jid)
        os.makedirs(d, exist_ok=True)
        job = Job(jid, d, settings)
        with self._lock:
            self._jobs[jid] = job
        return job

    def get(self, jid: str) -> Job | None:
        job = self._jobs.get(jid)
        if job:
            job.touched = time.time()
        return job

    def delete(self, jid: str) -> None:
        with self._lock:
            job = self._jobs.pop(jid, None)
        if job:
            with job.lock:
                job.close()

    def cleanup(self, ttl_minutes: int) -> None:
        now = time.time()
        for jid, job in list(self._jobs.items()):
            if job.state not in ("analyzing", "processing") and now - job.touched > ttl_minutes * 60:
                self.delete(jid)

    def close_all(self) -> None:
        for jid in list(self._jobs):
            self.delete(jid)
        shutil.rmtree(TEMP_ROOT, ignore_errors=True)
