"""Local web app (FastAPI). Binds to 127.0.0.1 only.

Browser-facing safety: requests must carry Host localhost/127.0.0.1 (blocks DNS rebinding) and every
state-changing request must send the header `X-Printwright: 1`, which a cross-site page cannot add without
a CORS preflight that this server never approves.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from printwright import __version__, apps, jobs
from printwright import pipeline as pl
from printwright.config import Settings, doctor
from printwright.modes import MODES, PROFILES
from printwright.project import Project
from printwright.spec import Spec

STATIC = Path(__file__).with_name("static")
ALLOWED_HOSTS = {"127.0.0.1", "localhost", "[::1]"}

app = FastAPI(title="printwright", version=__version__, docs_url="/api/docs", redoc_url=None)


@app.middleware("http")
async def guard(request: Request, call_next):
    host = (request.headers.get("host") or "").rsplit(":", 1)[0]
    if host not in ALLOWED_HOSTS:
        return JSONResponse({"detail": "forbidden host"}, status_code=403)
    if request.method not in ("GET", "HEAD", "OPTIONS") and request.headers.get("x-printwright") != "1":
        return JSONResponse({"detail": "missing X-Printwright header"}, status_code=403)
    return await call_next(request)


def _project(pid: str) -> Project:
    try:
        return Project.load(pid)
    except FileNotFoundError:
        raise HTTPException(404, "project not found") from None


def _job(kind: str, p: Project, fn) -> dict:
    if jobs.active(p.id):
        raise HTTPException(409, "a job is already running for this project")
    return jobs.submit(kind, p.id, fn, p.lock)


# --------------------------------------------------------------------------- meta


@app.get("/api/health")
def health() -> dict:
    s = Settings.load()
    return {
        "version": __version__,
        "doctor": doctor(s),
        "apps": apps.available(s),
        "modes": [PROFILES[m].to_dict() for m in MODES],
    }


@app.get("/api/settings")
def get_settings() -> dict:
    return Settings.load().model_dump()


@app.put("/api/settings")
def put_settings(body: dict) -> dict:
    s = Settings.load().model_copy(update={k: v for k, v in body.items() if k in Settings.model_fields})
    s = Settings.model_validate(s.model_dump())
    s.save()
    return s.model_dump()


@app.get("/api/profiles")
def profiles(printer: str = "Bambu Lab X2D", nozzle: float = 0.4) -> dict:
    ctx = pl.Context()
    try:
        from printwright.bambu.profiles import machine_for, printer_info

        m = machine_for(ctx.idx, printer, nozzle)
        return {
            "printers": ctx.idx.printer_models(),
            "machine": m,
            "printer": printer_info(ctx.idx, m).to_dict(),
            "processes": ctx.idx.compatible("process", m),
            "filaments": ctx.idx.filament_families(m),
            "sources": ctx.idx.roots,
        }
    except Exception as e:
        raise HTTPException(400, str(e)) from e


# --------------------------------------------------------------------------- projects


class NewProject(BaseModel):
    title: str
    mode: str = "balanced"


@app.get("/api/examples")
def examples() -> list[dict]:
    return pl.list_examples()


class ExampleBody(BaseModel):
    name: str


@app.post("/api/examples")
def from_example(body: ExampleBody) -> dict:
    holder: dict = {}

    def run(log):
        p = pl.create_from_example(body.name, log=log)
        holder["id"] = p.id
        return {"project": p.id}

    return jobs.submit("example", None, run)


@app.get("/api/projects")
def list_projects() -> list[dict]:
    return Project.list()


@app.post("/api/projects")
def create_project(body: NewProject) -> dict:
    if body.mode not in MODES:
        raise HTTPException(400, "unknown mode")
    return Project.create(body.title.strip() or "Untitled", body.mode).data  # type: ignore[arg-type]


@app.get("/api/projects/{pid}")
def get_project(pid: str) -> dict:
    p = _project(pid)
    spec = p.spec
    return {
        **p.data,
        "lint": spec.lint(),
        "can_freeze": spec.can_freeze(),
        "backend": pl.choose_backend(p, Settings.load()),
        "jobs": jobs.active(pid),
    }


@app.put("/api/projects/{pid}/spec")
def put_spec(pid: str, body: dict) -> dict:
    p = _project(pid)
    try:
        spec = Spec.model_validate(body)
    except Exception as e:
        raise HTTPException(422, str(e)) from e
    old = p.spec
    spec.status = (
        "draft" if spec.model_dump(exclude={"status"}) != old.model_dump(exclude={"status"}) else old.status
    )
    p.spec = spec
    p.save()
    return {"spec": spec.model_dump(), "lint": spec.lint(), "can_freeze": spec.can_freeze()}


class ModeBody(BaseModel):
    mode: str


@app.post("/api/projects/{pid}/mode")
def set_mode(pid: str, body: ModeBody) -> dict:
    p = _project(pid)
    try:
        spec = pl.set_mode(p, body.mode)  # type: ignore[arg-type]
    except pl.PipelineError as e:
        raise HTTPException(400, str(e)) from e
    return {"spec": spec.model_dump(), "lint": spec.lint()}


class FreezeBody(BaseModel):
    frozen: bool = True
    force: bool = False


@app.post("/api/projects/{pid}/freeze")
def freeze(pid: str, body: FreezeBody) -> dict:
    p = _project(pid)
    try:
        spec = pl.freeze(p, body.force) if body.frozen else pl.unfreeze(p)
    except pl.PipelineError as e:
        raise HTTPException(400, str(e)) from e
    return {"spec": spec.model_dump(), "lint": spec.lint()}


class BackendBody(BaseModel):
    backend: str | None


@app.post("/api/projects/{pid}/backend")
def set_backend(pid: str, body: BackendBody) -> dict:
    p = _project(pid)
    p.data["backend"] = body.backend
    p.save()
    return {"backend": pl.choose_backend(p, Settings.load())}


class ChatBody(BaseModel):
    message: str


@app.post("/api/projects/{pid}/chat")
def chat(pid: str, body: ChatBody) -> dict:
    p = _project(pid)
    return _job("chat", p, lambda log: pl.spec_turn(Project.load(pid), body.message, log=log))


class GenerateBody(BaseModel):
    feedback: str | None = None
    allow_draft: bool = False


@app.post("/api/projects/{pid}/generate")
def generate(pid: str, body: GenerateBody) -> dict:
    p = _project(pid)
    return _job(
        "generate",
        p,
        lambda log: pl.generate(Project.load(pid), body.feedback, log=log, allow_draft=body.allow_draft),
    )


class RebuildBody(BaseModel):
    code: str | None = None
    review: bool = False


@app.post("/api/projects/{pid}/iterations/{n}/rebuild")
def rebuild(pid: str, n: int, body: RebuildBody) -> dict:
    p = _project(pid)
    if body.code is not None:
        info = p.iteration(n)
        name = "model.scad" if info.get("backend") == "openscad" else "model.py"
        (p.iteration_dir(n) / name).write_text(body.code, encoding="utf-8")
    return _job("rebuild", p, lambda log: pl.rebuild(Project.load(pid), n, log=log, review=body.review))


@app.get("/api/projects/{pid}/iterations/{n}")
def iteration(pid: str, n: int) -> dict:
    p = _project(pid)
    d = p.iteration_dir(n)
    if not d.exists():
        raise HTTPException(404, "no such iteration")
    info = p.iteration(n)

    def rd(rel):
        f = d / rel
        return json.loads(f.read_text(encoding="utf-8")) if f.exists() else None

    script = d / ("model.scad" if info.get("backend") == "openscad" else "model.py")
    report = rd("report.json")
    if report:
        report.pop("analysis", None)
    return {
        "info": info,
        "code": script.read_text(encoding="utf-8") if script.exists() else "",
        "manifest": rd("build/manifest.json"),
        "report": report,
        "review": rd("review.json"),
        "analysis": (rd("report.json") or {}).get("analysis"),
        "renders": sorted(str(x.relative_to(p.root)) for x in (d / "renders").glob("*.png")),
        "blend": (d / "renders" / "scene.blend").exists(),
    }


class SliceBody(BaseModel):
    iteration: int | None = None
    force: bool = False
    overrides: dict | None = None


@app.post("/api/projects/{pid}/slice")
def do_slice(pid: str, body: SliceBody) -> dict:
    p = _project(pid)
    return _job(
        "slice",
        p,
        lambda log: pl.slice_iteration(
            Project.load(pid), body.iteration, log=log, force=body.force, overrides=body.overrides
        ),
    )


@app.get("/api/projects/{pid}/files/{rel:path}")
def project_file(pid: str, rel: str):
    p = _project(pid)
    try:
        f = p.resolve(rel)
    except FileNotFoundError:
        raise HTTPException(404) from None
    if not f.is_file():
        raise HTTPException(404)
    return FileResponse(f, headers={"Cache-Control": "no-store"})


class OpenBody(BaseModel):
    app: str
    path: str


@app.post("/api/projects/{pid}/open")
def open_app(pid: str, body: OpenBody) -> dict:
    p = _project(pid)
    try:
        f = p.resolve(body.path)
        name = apps.open_in(body.app, f)
    except FileNotFoundError:
        raise HTTPException(404, "file not found") from None
    except apps.AppError as e:
        raise HTTPException(400, str(e)) from e
    return {"opened": name, "path": str(f)}


@app.get("/api/jobs/{jid}")
def job(jid: str) -> dict:
    j = jobs.get(jid)
    if not j:
        raise HTTPException(404)
    return {k: v for k, v in j.items() if k != "traceback"}


app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")


def serve(host: str = "127.0.0.1", port: int = 8765, open_browser: bool = True) -> None:
    import threading
    import webbrowser

    import uvicorn

    if host not in ("127.0.0.1", "localhost", "::1"):
        raise SystemExit(
            "printwright only serves on localhost; it can run code and open apps on this machine."
        )
    if open_browser:
        threading.Timer(1.2, lambda: webbrowser.open(f"http://127.0.0.1:{port}/")).start()
    uvicorn.run(app, host=host, port=port, log_level="warning")
