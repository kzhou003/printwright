"""printwright command line. `printwright serve` starts the web app; every step also works from here."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer

from printwright import __version__
from printwright import pipeline as pl
from printwright.config import Settings, doctor
from printwright.modes import MODES, PROFILES
from printwright.project import Project

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Spec-first design-to-print workbench: requirements -> spec -> CAD -> checks -> Bambu Studio.",
)


def _p(pid: str) -> Project:
    try:
        return Project.load(pid)
    except FileNotFoundError:
        matches = [x["id"] for x in Project.list() if x["id"].startswith(pid)]
        if len(matches) == 1:
            return Project.load(matches[0])
        raise typer.BadParameter(f"project {pid!r} not found ({len(matches)} prefix matches)") from None


def _log(line: str) -> None:
    typer.echo(line)


def _fail(e: Exception) -> None:
    typer.secho(str(e), fg="red", err=True)
    raise typer.Exit(1)


@app.command()
def version() -> None:
    """Print the version."""
    typer.echo(__version__)


@app.command("doctor")
def doctor_cmd() -> None:
    """Show which tools and LLM providers were found."""
    typer.echo(json.dumps(doctor(), indent=2))


@app.command()
def serve(port: int = 8765, no_browser: bool = False) -> None:
    """Start the local web app on http://127.0.0.1:PORT."""
    from printwright.server import serve as run

    typer.echo(f"printwright {__version__} on http://127.0.0.1:{port}  (Ctrl+C to stop)")
    run(port=port, open_browser=not no_browser)


@app.command()
def modes() -> None:
    """Explain the three design modes."""
    for m in MODES:
        p = PROFILES[m]
        sd = p.slice
        typer.secho(f"{p.name} ({p.title_zh})", bold=True)
        typer.echo(f"  {p.summary}")
        typer.echo(
            f"  CAD: {', '.join(p.backends)} | slice: {sd.layer_height} mm {sd.quality}, {sd.wall_loops} walls, "
            f"{sd.infill_percent}% {sd.infill_pattern}, supports {sd.supports}"
        )


@app.command("new")
def new(
    title: str, mode: Annotated[str, typer.Option(help="balanced | creative | precision")] = "balanced"
) -> None:
    """Create a project."""
    if mode not in MODES:
        raise typer.BadParameter(f"mode must be one of {MODES}")
    p = Project.create(title, mode)  # type: ignore[arg-type]
    typer.echo(p.id)


@app.command()
def example(
    name: Annotated[str | None, typer.Argument(help="example name; omit to list them")] = None,
) -> None:
    """Create a project from a bundled example (no AI needed): spec, script, build, checks, renders."""
    if not name:
        for e in pl.list_examples():
            typer.echo(f"{e['name']:<16} {e['mode']:<10} {e['title']}: {e['summary']}")
        return
    try:
        p = pl.create_from_example(name, log=_log)
    except Exception as e:
        _fail(e)
    info = p.iteration(1)
    typer.echo(f"{p.id}: ready_to_slice={info.get('ready_to_slice')} {info.get('counts')}")


@app.command("list")
def list_cmd() -> None:
    """List projects."""
    for x in Project.list():
        typer.echo(f"{x['id']:<40} {x['mode']:<10} {x['status']:<7} v{x['iterations']:<3} {x['title']}")


@app.command()
def chat(project: str, message: str) -> None:
    """Send a requirements message; the assistant updates the spec."""
    try:
        r = pl.spec_turn(_p(project), message, log=_log)
    except Exception as e:
        _fail(e)
    typer.echo("\n" + r["reply"])
    for i in r["lint"]:
        typer.secho(f"[{i['severity']}] {i['message']}", fg="yellow" if i["severity"] != "error" else "red")


@app.command()
def spec(
    project: str,
    edit: Annotated[Path | None, typer.Option(help="replace the spec with this JSON or YAML file")] = None,
    freeze: bool = False,
    unfreeze: bool = False,
    force: bool = False,
    as_json: bool = False,
) -> None:
    """Show, replace, freeze or unfreeze the spec."""
    p = _p(project)
    try:
        if edit:
            import yaml

            from printwright.spec import Spec

            data = yaml.safe_load(edit.read_text(encoding="utf-8"))
            s = Spec.model_validate(data)
            s.status = "draft"
            p.spec = s
            p.save()
        if freeze:
            pl.freeze(p, force)
        if unfreeze:
            pl.unfreeze(p)
    except Exception as e:
        _fail(e)
    s = p.spec
    typer.echo(s.model_dump_json(indent=2) if as_json else s.to_markdown())
    typer.echo(f"status: {s.status}")
    for i in s.lint():
        typer.secho(f"[{i['severity']}] {i['message']}", fg="yellow" if i["severity"] != "error" else "red")


@app.command()
def mode(project: str, new_mode: str) -> None:
    """Switch a project's design mode (balanced | creative | precision)."""
    try:
        s = pl.set_mode(_p(project), new_mode)  # type: ignore[arg-type]
    except Exception as e:
        _fail(e)
    typer.echo(f"mode: {s.mode} (spec is back to draft; review and freeze it again)")


@app.command()
def generate(
    project: str,
    feedback: Annotated[str | None, typer.Option("--feedback", "-f")] = None,
    allow_draft: bool = False,
) -> None:
    """Generate or revise the CAD model, then build, check, render and review it."""
    try:
        r = pl.generate(_p(project), feedback, log=_log, allow_draft=allow_draft)
    except Exception as e:
        _fail(e)
    typer.echo(
        json.dumps(
            {k: r.get(k) for k in ("n", "counts", "ready_to_slice", "verdict", "blocking", "cost_usd")},
            indent=2,
        )
    )


@app.command()
def rebuild(project: str, iteration: int | None = None, review: bool = False) -> None:
    """Rebuild an iteration after editing its model.py by hand (no LLM unless --review)."""
    try:
        r = pl.rebuild(_p(project), iteration, log=_log, review=review)
    except Exception as e:
        _fail(e)
    typer.echo(json.dumps({k: r.get(k) for k in ("n", "counts", "ready_to_slice", "blocking")}, indent=2))


@app.command("import")
def import_cmd(project: str, script: Path, backend: str = "cadquery") -> None:
    """Start a new iteration from your own CadQuery / organic / OpenSCAD script."""
    try:
        r = pl.import_script(_p(project), script.read_text(encoding="utf-8"), backend, log=_log)
    except Exception as e:
        _fail(e)
    typer.echo(json.dumps({k: r.get(k) for k in ("n", "counts", "ready_to_slice", "blocking")}, indent=2))


@app.command()
def report(project: str, iteration: int | None = None) -> None:
    """Print the check report of an iteration."""
    p = _p(project)
    n = iteration or p.latest_iteration()
    f = p.iteration_dir(n or 0) / "report.json"
    if not n or not f.exists():
        _fail(FileNotFoundError("no report"))
    rep = json.loads(f.read_text(encoding="utf-8"))
    colors = {"pass": "green", "warn": "yellow", "fail": "red", "skip": "white"}
    for r in rep["checks"]:
        typer.secho(f"{r['status']:>4}  {r['id']:<32} {r['title']}  {r['message']}", fg=colors[r["status"]])
    typer.echo(f"ready_to_slice: {rep['ready_to_slice']}")


@app.command("slice")
def slice_cmd(project: str, iteration: int | None = None, force: bool = False) -> None:
    """Slice an iteration with Bambu Studio -> .3mf project + .gcode.3mf."""
    try:
        r = pl.slice_iteration(_p(project), iteration, log=_log, force=force)
    except Exception as e:
        _fail(e)
    p = _p(project)
    out = p.root / "slices" / f"s{r['n']:03d}"
    for kind, name in r["files"].items():
        typer.echo(f"{kind:>8}: {out / name}")


@app.command("open")
def open_cmd(
    project: str,
    target: Annotated[str, typer.Argument(help="bambu | fusion | blender | reveal")],
    iteration: int | None = None,
) -> None:
    """Open the latest result in Bambu Studio, Fusion (STEP) or Blender."""
    from printwright.apps import open_in

    p = _p(project)
    n = iteration or p.latest_iteration()
    if target == "bambu":
        sl = [s for s in p.data["slices"] if s.get("ok")]
        if not sl:
            _fail(RuntimeError("nothing sliced yet; run `printwright slice` first"))
        s = sl[-1]
        path = p.root / "slices" / f"s{s['n']:03d}" / s["files"]["sliced"]
    elif target == "fusion":
        d = p.iteration_dir(n or 0) / "build"
        path = (
            d / "assembly.step"
            if (d / "assembly.step").exists()
            else next(iter(sorted((d / "parts").glob("*.st*"))))
        )
    elif target == "blender":
        path = p.iteration_dir(n or 0) / "renders" / "scene.blend"
    else:
        path = p.iteration_dir(n or 0)
    try:
        typer.echo(f"opened {path} in {open_in(target, path)}")
    except Exception as e:
        _fail(e)


@app.command()
def config(
    key: Annotated[str | None, typer.Argument()] = None, value: Annotated[str | None, typer.Argument()] = None
) -> None:
    """Show settings, or set one: `printwright config provider openai`."""
    s = Settings.load()
    if key is None:
        typer.echo(s.model_dump_json(indent=2))
        typer.echo(f"# {Settings.path()}")
        return
    if key not in Settings.model_fields:
        raise typer.BadParameter(f"unknown setting {key}")
    if value is None:
        typer.echo(json.dumps(getattr(s, key)))
        return
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        parsed = value
    s = Settings.model_validate({**s.model_dump(), key: parsed})
    s.save()
    typer.echo(f"{key} = {json.dumps(getattr(s, key))}")


if __name__ == "__main__":
    app()
