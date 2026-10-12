"""Minimal background-job store for long operations (tunnel test/create/delete).

A job runs in a worker thread, appends timestamped log lines, and finishes
with a result dict or an error. The frontend polls GET /jobs/{id} for live
progress (transcript) while running its own elapsed timer.
"""
from __future__ import annotations

import threading
import time
import uuid

MAX_JOBS = 50
MAX_LINES = 3000


class Job:
    def __init__(self, kind: str, title: str):
        now = time.time()
        self.id = uuid.uuid4().hex[:12]
        self.kind = kind
        self.title = title
        self.status = "queued"  # queued | running | done | error
        self.lines: list[dict] = []
        self.result: dict = {}
        self.error = ""
        self.started_at = now
        self.ended_at: float | None = None

    def log(self, text: str) -> None:
        if not text:
            return
        now = time.time()
        for line in str(text).splitlines() or [""]:
            self.lines.append({"t": now, "line": line})
        if len(self.lines) > MAX_LINES:
            del self.lines[: len(self.lines) - MAX_LINES]

    def to_dict(self, since: int = 0) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "title": self.title,
            "status": self.status,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "elapsed_s": int((self.ended_at or time.time()) - self.started_at),
            "lines": self.lines[since:],
            "next": len(self.lines),
            "result": self.result,
            "error": self.error,
        }


class JobStore:
    def __init__(self):
        self._lock = threading.Lock()
        self._jobs: dict[str, Job] = {}
        self._order: list[str] = []

    def create(self, kind: str, title: str) -> Job:
        job = Job(kind, title)
        with self._lock:
            self._jobs[job.id] = job
            self._order.append(job.id)
            while len(self._order) > MAX_JOBS:
                old = self._order.pop(0)
                self._jobs.pop(old, None)
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def list(self) -> list[dict]:
        with self._lock:
            jobs = [self._jobs[j] for j in self._order if j in self._jobs]
        return [
            {
                "id": j.id, "kind": j.kind, "title": j.title, "status": j.status,
                "started_at": j.started_at, "ended_at": j.ended_at,
            }
            for j in reversed(jobs)
        ]

    def run(self, job: Job, fn) -> Job:
        def _wrap():
            job.status = "running"
            try:
                job.result = fn(job) or {}
                job.status = "done"
            except Exception as exc:  # noqa: BLE001 - surfaced to the UI
                job.error = str(exc)
                job.status = "error"
            finally:
                job.ended_at = time.time()

        t = threading.Thread(target=_wrap, daemon=True)
        t.start()
        return job


store = JobStore()
