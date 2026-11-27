"""Minimal in-process job runner for the local server (one user, a few long tasks)."""

from __future__ import annotations

import threading
import time
import traceback
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

_POOL = ThreadPoolExecutor(max_workers=3, thread_name_prefix="pw-job")
_JOBS: dict[str, dict] = {}
_LOCK = threading.Lock()


def submit(
    kind: str,
    project_id: str | None,
    fn: Callable[[Callable[[str], None]], dict | None],
    project_lock: threading.Lock | None = None,
) -> dict:
    jid = uuid.uuid4().hex[:12]
    job = {
        "id": jid,
        "kind": kind,
        "project": project_id,
        "status": "queued",
        "log": [],
        "result": None,
        "error": None,
        "created": time.time(),
        "finished": None,
    }
    with _LOCK:
        _JOBS[jid] = job

    def log(line: str) -> None:
        job["log"].append(f"{time.strftime('%H:%M:%S')} {line}")

    def run() -> None:
        job["status"] = "running"
        lock = project_lock or threading.Lock()
        with lock:  # one job per project at a time
            try:
                job["result"] = fn(log)
                job["status"] = "done"
            except Exception as e:
                job["error"] = str(e)
                job["status"] = "error"
                log("ERROR " + str(e).splitlines()[0] if str(e) else type(e).__name__)
                job["traceback"] = traceback.format_exc()[-4000:]
            finally:
                job["finished"] = time.time()

    _POOL.submit(run)
    return job


def get(jid: str) -> dict | None:
    return _JOBS.get(jid)


def active(project_id: str) -> list[dict]:
    return [j for j in _JOBS.values() if j["project"] == project_id and j["status"] in ("queued", "running")]
