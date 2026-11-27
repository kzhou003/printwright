"""The design loop: requirements chat -> frozen spec -> CAD -> checks -> renders -> review -> slice.

Each public function is a unit of work the server runs as a background job and the CLI runs directly.
`log` receives human-readable progress lines.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from pathlib import Path

from printwright.analysis.acceptance import evaluate
from printwright.bambu.profiles import PrinterInfo, ProfileIndex, machine_for, printer_info
from printwright.bambu.slicer import plan as slice_plan
from printwright.bambu.slicer import slice_project
from printwright.cad.build import build
from printwright.config import Settings
from printwright.llm import LLMError, Msg, Provider, complete_model, extract_code, get_provider
from printwright.llm import prompts as P
from printwright.modes import MODES, Mode, profile
from printwright.project import Project
from printwright.render.render import render
from printwright.spec import Spec

Log = Callable[[str], None]


def _noop(_: str) -> None:
    pass


class PipelineError(RuntimeError):
    pass


# --------------------------------------------------------------------------- context


class Context:
    """Lazily loaded Bambu presets and printer facts, shared by one job."""

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or Settings.load()
        self._idx: ProfileIndex | None = None

    @property
    def idx(self) -> ProfileIndex:
        if self._idx is None:
            self._idx = ProfileIndex.discover(self.settings)
        return self._idx

    def printer(self, spec: Spec) -> PrinterInfo:
        return printer_info(self.idx, machine_for(self.idx, spec.print.printer, spec.print.nozzle))

    def filaments(self, spec: Spec) -> list[str]:
        return self.idx.filament_families(machine_for(self.idx, spec.print.printer, spec.print.nozzle))


# --------------------------------------------------------------------------- spec


def spec_turn(
    project: Project,
    message: str,
    ctx: Context | None = None,
    provider: Provider | None = None,
    log: Log = _noop,
) -> dict:
    ctx = ctx or Context()
    provider = provider or get_provider(ctx.settings)
    spec = project.spec
    if spec.status == "frozen":
        spec.status = "draft"  # talking about requirements reopens the spec
    printer = ctx.printer(spec)
    system = P.spec_system(spec.mode, printer.summary(), ctx.filaments(spec))
    history = [Msg(m["role"], m["content"]) for m in project.data["chat"][-30:]]
    current = f"Current spec (JSON):\n```json\n{spec.model_dump_json(indent=1)}\n```\n\nUser: {message}"
    log(f"asking {provider.name} to update the spec")
    turn, res = complete_model(provider, system, [*history, Msg("user", current)], P.SpecTurn)
    new = turn.spec
    if new.mode != spec.mode:  # mode changes are the user's call; keep it as a recommendation
        from printwright.spec import ModeRecommendation

        new.mode_recommendation = new.mode_recommendation or ModeRecommendation(
            mode=new.mode, reason="The assistant suggested this mode."
        )
        new.mode = spec.mode
    new.status = "draft"
    project.add_chat("user", message)
    project.add_chat("assistant", turn.reply, cost_usd=res.cost_usd, model=res.model)
    project.spec = new
    project.save()
    return {"reply": turn.reply, "spec": new.model_dump(), "lint": new.lint(), "cost_usd": res.cost_usd}


def set_mode(project: Project, mode: Mode) -> Spec:
    if mode not in MODES:
        raise PipelineError(f"unknown mode {mode!r}")
    spec = project.spec
    if spec.mode != mode:
        spec.mode = mode
        spec.status = "draft"
        if spec.mode_recommendation and spec.mode_recommendation.mode == mode:
            spec.mode_recommendation = None
        sd = profile(mode).slice
        spec.print.process = None  # let the new mode pick the process preset
        if mode == "creative" and spec.print.supports is None:
            spec.print.supports = sd.supports
        project.data["backend"] = None
        project.spec = spec
        project.save()
    return spec


def freeze(project: Project, force: bool = False) -> Spec:
    spec = project.spec
    errors = [i for i in spec.lint() if i["severity"] == "error"]
    if errors and not force:
        raise PipelineError("Spec cannot be frozen yet:\n" + "\n".join(f"- {e['message']}" for e in errors))
    spec.status = "frozen"
    project.spec = spec
    project.save()
    return spec


def unfreeze(project: Project) -> Spec:
    spec = project.spec
    spec.status = "draft"
    project.spec = spec
    project.save()
    return spec


# --------------------------------------------------------------------------- CAD generation


def choose_backend(project: Project, settings: Settings) -> str:
    spec = project.spec
    allowed = profile(spec.mode).backends
    chosen = project.data.get("backend")
    if chosen in allowed:
        return chosen
    if spec.mode == "balanced" and settings.cad_backend in allowed:
        return settings.cad_backend
    return allowed[0]


def _script_name(backend: str) -> str:
    return "model.scad" if backend == "openscad" else "model.py"


def _parts_for_render(spec: Spec, build_dir: Path, manifest: dict, pose: str = "assembly") -> list[dict]:
    colors = {p.name: p.color for p in spec.parts}
    key = "assembly_stl" if pose == "assembly" else "stl"
    return [
        {"name": p["name"], "stl": build_dir / p[key], "color": colors.get(p["name"], "#cccccc")}
        for p in manifest["parts"]
    ]


def _check_part_names(spec: Spec, manifest: dict) -> str | None:
    got = {p["name"] for p in manifest["parts"]}
    want = set(spec.part_names())
    if got != want:
        return f"build() returned parts {sorted(got)} but the spec defines {sorted(want)}; the keys must match exactly."
    return None


def generate(
    project: Project,
    feedback: str | None = None,
    ctx: Context | None = None,
    provider: Provider | None = None,
    log: Log = _noop,
    allow_draft: bool = False,
) -> dict:
    """Write (or revise) the CAD script with the LLM, then build, check, repair, render and review."""
    ctx = ctx or Context()
    s = ctx.settings
    spec = project.spec
    if spec.status != "frozen" and not allow_draft:
        raise PipelineError("Freeze the spec first (it is the contract the model is checked against).")
    provider = provider or get_provider(s)
    backend = choose_backend(project, s)
    printer = ctx.printer(spec)
    prev_n = project.latest_iteration()
    prev = project.iteration(prev_n) if prev_n else {}
    prev_code = None
    if prev_n:
        f = project.iteration_dir(prev_n) / _script_name(prev.get("backend", backend))
        prev_code = f.read_text(encoding="utf-8") if f.exists() and prev.get("backend") == backend else None
    prev_report = _read_json(project.iteration_dir(prev_n) / "report.json") if prev_code else None
    prev_review = _read_json(project.iteration_dir(prev_n) / "review.json") if prev_code else None

    n, d = project.new_iteration()
    log(f"iteration v{n:03d}: backend {backend}, mode {spec.mode}, provider {provider.name}")
    system = P.codegen_system(spec, backend, printer.summary())
    msgs = [Msg("user", P.codegen_user(spec, prev_code, prev_report, prev_review, feedback))]
    (d / "prompt.md").write_text(f"# system\n\n{system}\n\n# user\n\n{msgs[0].content}\n", encoding="utf-8")

    attempts: list[dict] = []
    cost = 0.0
    code: str | None = None
    result = None
    report = None
    max_attempts = 1 + s.max_repair_attempts
    mp = profile(spec.mode)
    script = d / _script_name(backend)
    for attempt in range(1, max_attempts + 1):
        log(f"  attempt {attempt}: asking for {'the script' if attempt == 1 else 'a fix'}")
        res = provider.complete(system, msgs)
        cost += res.cost_usd or 0.0
        code = extract_code(res.text)
        if not code:
            msgs += [
                Msg("assistant", res.text),
                Msg("user", "Return the complete script in one fenced code block."),
            ]
            attempts.append({"attempt": attempt, "stage": "no_code"})
            continue
        script.write_text(code, encoding="utf-8")
        (d / f"attempt_{attempt}{script.suffix}").write_text(code, encoding="utf-8")
        log("  building")
        result = build(script, d / "build", s)
        if not result.ok:
            log(f"  build failed ({result.stage}): {result.error.splitlines()[-1] if result.error else ''}")
            attempts.append({"attempt": attempt, "stage": result.stage, "error": result.error[-2000:]})
            msgs += [Msg("assistant", res.text), Msg("user", P.repair_user(result.error, result.stage))]
            continue
        name_err = _check_part_names(spec, result.manifest)
        if name_err:
            attempts.append({"attempt": attempt, "stage": "parts", "error": name_err})
            msgs += [Msg("assistant", res.text), Msg("user", name_err + " Return the complete script.")]
            continue
        log("  checking printability and acceptance")
        report = evaluate(spec, d / "build", result.manifest, printer)
        failing = [
            r for r in report["checks"] if r["status"] == "fail" and (r["blocking"] or r["kind"] != "auto")
        ]
        attempts.append(
            {
                "attempt": attempt,
                "stage": "checked",
                "counts": report["counts"],
                "failing": [r["id"] for r in failing],
            }
        )
        if not failing or attempt == max_attempts:
            break
        if not mp.block_on_acceptance_fail and all(not r["blocking"] for r in failing) and attempt >= 2:
            break  # balanced/creative: one repair round for non-blocking spec checks is enough
        log(f"  {len(failing)} check(s) fail; asking for a fix")
        msgs += [Msg("assistant", res.text), Msg("user", P.checks_repair_user(report))]

    summary: dict = {
        "n": n,
        "backend": backend,
        "mode": spec.mode,
        "spec_digest": spec.digest(),
        "created": time.time(),
        "source": "llm",
        "provider": provider.name,
        "attempts": attempts,
        "feedback": feedback,
        "ok": bool(result and result.ok and report is not None),
    }
    if not summary["ok"]:
        summary["error"] = (result.error if result else "no script produced")[-3000:]
        summary["cost_usd"] = round(cost, 4)
        project.record_iteration(n, summary)
        raise PipelineError(
            f"v{n:03d}: could not produce a working model after {len(attempts)} attempt(s). "
            f"Last error:\n{summary['error'][-1500:]}"
        )
    summary.update(_finish(project, n, spec, result.manifest, report, ctx, provider, log))
    summary["cost_usd"] = round(cost + (summary.pop("review_cost", 0.0) or 0.0), 4)
    project.record_iteration(n, summary)
    log(f"v{n:03d} done: {report['counts']}, ready_to_slice={report['ready_to_slice']}")
    return summary


def _finish(
    project: Project,
    n: int,
    spec: Spec,
    manifest: dict,
    report: dict,
    ctx: Context,
    provider: Provider | None,
    log: Log,
) -> dict:
    d = project.iteration_dir(n)
    (d / "report.json").write_text(json.dumps(report, indent=2, default=float), encoding="utf-8")
    log("  rendering")
    rend = render(
        _parts_for_render(spec, d / "build", manifest),
        d / "renders",
        ctx.settings,
        blend=d / "renders" / "scene.blend",
    )
    out = {
        "ready_to_slice": report["ready_to_slice"],
        "counts": report["counts"],
        "blocking": report["blocking"],
        "render_engine": rend.get("engine"),
    }
    if provider is not None and ctx.settings.vision_review:
        log("  visual review")
        try:
            review, res = complete_model(
                provider,
                P.review_system(spec),
                [Msg("user", P.review_user(spec, report))],
                P.Review,
                images=[Path(rend["sheet"])],
            )
            (d / "review.json").write_text(review.model_dump_json(indent=2), encoding="utf-8")
            out["verdict"] = review.verdict
            out["review_cost"] = res.cost_usd or 0.0
        except LLMError as e:
            log(f"  review skipped: {e}")
            out["verdict"] = "skipped"
    return out


def rebuild(
    project: Project, n: int | None = None, ctx: Context | None = None, log: Log = _noop, review: bool = False
) -> dict:
    """Re-run build/checks/renders for an iteration's script (after you edited it by hand). No LLM unless review."""
    ctx = ctx or Context()
    n = n or project.latest_iteration()
    if not n:
        raise PipelineError("No iteration to rebuild.")
    d = project.iteration_dir(n)
    info = project.iteration(n)
    backend = info.get("backend", "cadquery")
    script = d / _script_name(backend)
    if not script.exists():
        raise PipelineError(f"{script.name} not found in v{n:03d}")
    spec = project.spec
    printer = ctx.printer(spec)
    log(f"rebuilding v{n:03d}")
    result = build(script, d / "build", ctx.settings)
    if not result.ok:
        info.update({"ok": False, "error": result.error[-3000:], "source": "manual"})
        project.record_iteration(n, info)
        raise PipelineError(f"Build failed ({result.stage}):\n{result.error[-1500:]}")
    name_err = _check_part_names(spec, result.manifest)
    if name_err:
        raise PipelineError(name_err)
    report = evaluate(spec, d / "build", result.manifest, printer)
    provider = get_provider(ctx.settings) if review else None
    info.update(
        {
            "n": n,
            "backend": backend,
            "ok": True,
            "source": "manual",
            "spec_digest": spec.digest(),
            "created": info.get("created", time.time()),
        }
    )
    info.update(_finish(project, n, spec, result.manifest, report, ctx, provider, log))
    info.pop("review_cost", None)
    project.record_iteration(n, info)
    return info


def import_script(
    project: Project, code: str, backend: str = "cadquery", ctx: Context | None = None, log: Log = _noop
) -> dict:
    """Start a new iteration from your own script (no LLM)."""
    n, d = project.new_iteration()
    (d / _script_name(backend)).write_text(code, encoding="utf-8")
    project.record_iteration(
        n, {"n": n, "backend": backend, "ok": False, "source": "manual", "created": time.time()}
    )
    return rebuild(project, n, ctx, log)


# --------------------------------------------------------------------------- slicing


def slice_iteration(
    project: Project,
    n: int | None = None,
    ctx: Context | None = None,
    log: Log = _noop,
    force: bool = False,
    overrides: dict | None = None,
) -> dict:
    ctx = ctx or Context()
    n = n or project.latest_iteration()
    if not n:
        raise PipelineError("Nothing to slice yet.")
    info = project.iteration(n)
    if not info.get("ok"):
        raise PipelineError(f"v{n:03d} did not build.")
    if not info.get("ready_to_slice") and not force:
        raise PipelineError(
            f"v{n:03d} has blocking check failures: {', '.join(info.get('blocking', []))}. "
            "Fix them or slice with force."
        )
    spec = project.spec
    d = project.iteration_dir(n)
    manifest = _read_json(d / "build" / "manifest.json") or {}
    key = "assembly_stl" if spec.print.layout == "assembled" else "stl"
    stls = {p["name"]: d / "build" / p[key] for p in manifest.get("parts", [])}
    sp = slice_plan(spec, stls, ctx.idx, overrides)
    k, out = project.new_slice_dir()
    stem = re.sub(r"[^A-Za-z0-9_-]+", "_", spec.title).strip("_")[:40] or "print"
    log(f"slicing v{n:03d} -> s{k:03d}: {sp.machine}, {sp.process}, {len(sp.filaments)} filament(s)")
    rep = slice_project(sp, ctx.idx, out, f"{stem}_v{n:03d}", ctx.settings)
    rep.update(
        {"n": k, "iteration": n, "forced": force and not info.get("ready_to_slice"), "created": time.time()}
    )
    (out / "slice.json").write_text(json.dumps(rep, indent=2), encoding="utf-8")
    project.data["slices"].append(
        {
            k2: rep.get(k2)
            for k2 in (
                "n",
                "iteration",
                "ok",
                "files",
                "forced",
                "created",
                "plates_info",
                "error",
                "warnings",
            )
        }
    )
    project.save()
    if not rep["ok"]:
        raise PipelineError(f"Slicing failed: {rep.get('error')}")
    pi = (rep.get("plates_info") or [{}])[0]
    log(f"sliced: {pi.get('print_seconds', 0) / 60:.0f} min, {pi.get('weight_g', 0):.1f} g")
    return rep


def _read_json(p: Path) -> dict | None:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


# --------------------------------------------------------------------------- examples

EXAMPLES = Path(__file__).with_name("examples")


def list_examples() -> list[dict]:
    out = []
    for d in sorted(EXAMPLES.iterdir()):
        if (d / "spec.json").exists():
            s = Spec.model_validate_json((d / "spec.json").read_text(encoding="utf-8"))
            out.append({"name": d.name, "title": s.title, "mode": s.mode, "summary": s.summary})
    return out


def create_from_example(name: str, ctx: Context | None = None, log: Log = _noop) -> Project:
    """A ready project from a bundled example: frozen spec + script, built and checked, no LLM needed."""
    d = EXAMPLES / name
    if not (d / "spec.json").exists():
        raise PipelineError(
            f"unknown example {name!r}; available: {', '.join(e['name'] for e in list_examples())}"
        )
    spec = Spec.model_validate_json((d / "spec.json").read_text(encoding="utf-8"))
    p = Project.create(spec.title, spec.mode)
    spec.status = "frozen"
    p.spec = spec
    p.add_chat(
        "assistant", f"Created from the bundled example '{name}'. Ask for changes here or edit the script."
    )
    p.save()
    backend = (
        "organic" if "printwright.cad.organic" in (d / "model.py").read_text(encoding="utf-8") else "cadquery"
    )
    p.data["backend"] = backend
    p.save()
    import_script(p, (d / "model.py").read_text(encoding="utf-8"), backend, ctx, log)
    return p
