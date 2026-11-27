"""On-disk project store. Everything is plain files so you can inspect, diff and version it.

<home>/projects/<id>/
    project.json            title, chat history, current spec, iteration and slice summaries
    spec.md                 the current spec, human readable
    iterations/v001/
        model.py            the CAD script (edit it and press Rebuild to iterate by hand)
        build/              parts/*.stl|step, assembly/*.stl, assembly.step, manifest.json, build.log
        report.json         checks; renders/ (iso/front/right/top/sheet.png, scene.blend); review.json
        iteration.json      summary: backend, spec digest, attempts, verdicts, cost
    slices/s001/            <name>.3mf (project), <name>.gcode.3mf (sliced), slice.json, plate_1.png
"""

from __future__ import annotations

import json
import re
import threading
import time
import uuid
from pathlib import Path

from printwright.config import home
from printwright.modes import DEFAULT_MODE, Mode
from printwright.spec import Spec, default_spec

_LOCKS: dict[str, threading.Lock] = {}
SAFE_ID = re.compile(r"^[a-z0-9][a-z0-9-]{2,80}$")


def projects_root() -> Path:
    p = home() / "projects"
    p.mkdir(parents=True, exist_ok=True)
    return p


class Project:
    def __init__(self, root: Path, data: dict):
        self.root = root
        self.data = data

    # ------------------------------------------------------------------ lifecycle
    @classmethod
    def create(cls, title: str, mode: Mode = DEFAULT_MODE) -> Project:
        slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:40] or "project"
        pid = f"{slug}-{uuid.uuid4().hex[:6]}"
        root = projects_root() / pid
        root.mkdir(parents=True)
        now = time.time()
        data = {
            "id": pid,
            "title": title,
            "created": now,
            "updated": now,
            "chat": [],
            "spec": default_spec(title, mode).model_dump(),
            "iterations": [],
            "slices": [],
            "backend": None,
        }
        p = cls(root, data)
        p.save()
        return p

    @classmethod
    def load(cls, pid: str) -> Project:
        if not SAFE_ID.match(pid):
            raise FileNotFoundError(pid)
        root = projects_root() / pid
        f = root / "project.json"
        if not f.exists():
            raise FileNotFoundError(pid)
        return cls(root, json.loads(f.read_text(encoding="utf-8")))

    @classmethod
    def list(cls) -> list[dict]:
        out = []
        for f in projects_root().glob("*/project.json"):
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            spec = d.get("spec", {})
            out.append(
                {
                    "id": d["id"],
                    "title": d.get("title"),
                    "updated": d.get("updated"),
                    "mode": spec.get("mode", DEFAULT_MODE),
                    "status": spec.get("status"),
                    "iterations": len(d.get("iterations", [])),
                }
            )
        return sorted(out, key=lambda x: x["updated"] or 0, reverse=True)

    @property
    def id(self) -> str:
        return self.data["id"]

    @property
    def lock(self) -> threading.Lock:
        return _LOCKS.setdefault(self.id, threading.Lock())

    def save(self) -> None:
        self.data["updated"] = time.time()
        tmp = self.root / "project.json.tmp"
        tmp.write_text(json.dumps(self.data, indent=2), encoding="utf-8")
        tmp.replace(self.root / "project.json")
        (self.root / "spec.md").write_text(self.spec.to_markdown(), encoding="utf-8")

    # ------------------------------------------------------------------ spec & chat
    @property
    def spec(self) -> Spec:
        return Spec.model_validate(self.data["spec"])

    @spec.setter
    def spec(self, s: Spec) -> None:
        self.data["spec"] = s.model_dump()
        self.data["title"] = s.title or self.data["title"]

    def add_chat(self, role: str, content: str, **extra) -> None:
        self.data["chat"].append({"role": role, "content": content, "ts": time.time(), **extra})

    # ------------------------------------------------------------------ iterations
    def iteration_dir(self, n: int) -> Path:
        return self.root / "iterations" / f"v{n:03d}"

    def new_iteration(self) -> tuple[int, Path]:
        n = len(self.data["iterations"]) + 1
        d = self.iteration_dir(n)
        d.mkdir(parents=True, exist_ok=True)
        return n, d

    def latest_iteration(self) -> int | None:
        return len(self.data["iterations"]) or None

    def iteration(self, n: int) -> dict:
        f = self.iteration_dir(n) / "iteration.json"
        return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}

    def record_iteration(self, n: int, summary: dict) -> None:
        (self.iteration_dir(n) / "iteration.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        brief = {
            k: summary.get(k)
            for k in (
                "n",
                "backend",
                "ok",
                "ready_to_slice",
                "counts",
                "verdict",
                "spec_digest",
                "created",
                "source",
                "cost_usd",
            )
        }
        its = self.data["iterations"]
        if len(its) >= n:
            its[n - 1] = brief
        else:
            its.append(brief)
        self.save()

    # ------------------------------------------------------------------ slices
    def new_slice_dir(self) -> tuple[int, Path]:
        n = len(self.data["slices"]) + 1
        d = self.root / "slices" / f"s{n:03d}"
        d.mkdir(parents=True, exist_ok=True)
        return n, d

    def resolve(self, rel: str) -> Path:
        """A path inside the project, or FileNotFoundError (no escaping via ..)."""
        p = (self.root / rel).resolve()
        if self.root.resolve() not in p.parents and p != self.root.resolve():
            raise FileNotFoundError(rel)
        return p
