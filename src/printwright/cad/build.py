"""Build a model script into STEP/STL files in a separate, time-limited process."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from printwright.cad.guard import check_openscad, check_python
from printwright.config import Settings, find_openscad

SECRET_ENV = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|AUTH)", re.I)


@dataclass
class BuildResult:
    ok: bool
    out_dir: Path
    manifest: dict = field(default_factory=dict)
    error: str = ""
    stage: str = ""  # guard | run | timeout
    seconds: float = 0.0

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "error": self.error,
            "stage": self.stage,
            "seconds": round(self.seconds, 2),
            "manifest": self.manifest,
        }


def _clean_env() -> dict:
    env = {k: v for k, v in os.environ.items() if not SECRET_ENV.search(k)}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.setdefault("MPLBACKEND", "Agg")
    return env


def _limit_resources() -> None:  # pragma: no cover - runs in the child
    try:
        import resource

        resource.setrlimit(resource.RLIMIT_CPU, (600, 600))
    except (ImportError, ValueError, OSError):
        pass


def build_python(
    script: Path, out_dir: Path, timeout_s: int = 180, overrides: dict | None = None
) -> BuildResult:
    t0 = time.time()
    script, out_dir = script.resolve(), out_dir.resolve()  # the child runs with cwd=out_dir
    issues = check_python(script.read_text(encoding="utf-8"))
    if issues:
        return BuildResult(
            False,
            out_dir,
            error="Script rejected by the safety guard:\n" + "\n".join(map(str, issues)),
            stage="guard",
        )
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, "-m", "printwright.cad.runner", str(script), str(out_dir)]
    if overrides:
        ov = out_dir / "overrides.json"
        ov.write_text(json.dumps(overrides), encoding="utf-8")
        cmd.append(str(ov))
    try:
        proc = subprocess.run(
            cmd,
            cwd=out_dir,
            env=_clean_env(),
            capture_output=True,
            text=True,
            timeout=timeout_s,
            preexec_fn=_limit_resources if os.name == "posix" else None,
        )
    except subprocess.TimeoutExpired:
        return BuildResult(
            False,
            out_dir,
            error=f"Build timed out after {timeout_s} s.",
            stage="timeout",
            seconds=time.time() - t0,
        )
    (out_dir / "build.log").write_text(proc.stdout + "\n--- stderr ---\n" + proc.stderr, encoding="utf-8")
    if proc.returncode != 0:
        return BuildResult(
            False, out_dir, error=_trim_traceback(proc.stderr, script), stage="run", seconds=time.time() - t0
        )
    manifest = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
    return BuildResult(True, out_dir, manifest=manifest, seconds=time.time() - t0)


def _trim_traceback(stderr: str, script: Path) -> str:
    """Keep the frames from the model script and the final error; LLMs repair better with less noise."""
    lines = stderr.strip().splitlines()
    if len(lines) <= 40:
        return stderr.strip()
    keep = [ln for i, ln in enumerate(lines) if script.name in ln or (i > 0 and script.name in lines[i - 1])]
    return "\n".join(["Traceback (trimmed):", *keep[-16:], *lines[-6:]])


OPENSCAD_PARTS = re.compile(r"//\s*@parts:\s*(.+)")


def build_openscad(script: Path, out_dir: Path, settings: Settings, timeout_s: int = 180) -> BuildResult:
    """OpenSCAD backend: parts are declared with '// @parts: a, b' and selected with -D PART="a".

    OpenSCAD produces meshes only (no STEP). Each part is expected to be modelled in print pose.
    """
    import trimesh

    t0 = time.time()
    exe = find_openscad(settings)
    if not exe:
        return BuildResult(
            False, out_dir, error="OpenSCAD is not installed (https://openscad.org).", stage="run"
        )
    src = script.read_text(encoding="utf-8")
    issues = check_openscad(src)
    if issues:
        return BuildResult(False, out_dir, error="\n".join(map(str, issues)), stage="guard")
    m = OPENSCAD_PARTS.search(src)
    names = [n.strip() for n in m.group(1).split(",")] if m else ["body"]
    (out_dir / "parts").mkdir(parents=True, exist_ok=True)
    (out_dir / "assembly").mkdir(parents=True, exist_ok=True)
    manifest: dict = {"params": {}, "parts": [], "interference": []}
    for name in names:
        stl = out_dir / "parts" / f"{name}.stl"
        cmd = [str(exe), "-o", str(stl), "--export-format", "binstl", "-D", f'PART="{name}"', str(script)]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s, env=_clean_env())
        except subprocess.TimeoutExpired:
            return BuildResult(False, out_dir, error=f"OpenSCAD timed out on part {name}", stage="timeout")
        if proc.returncode != 0 or not stl.exists():
            return BuildResult(
                False, out_dir, error=proc.stderr[-3000:], stage="run", seconds=time.time() - t0
            )
        mesh = trimesh.load(stl, force="mesh")
        (out_dir / "assembly" / f"{name}.stl").write_bytes(stl.read_bytes())
        lo, hi = mesh.bounds
        bbox = {"min": lo.round(4).tolist(), "max": hi.round(4).tolist(), "size": (hi - lo).round(4).tolist()}
        manifest["parts"].append(
            {
                "name": name,
                "kind": "mesh",
                "solids": len(mesh.split(only_watertight=False)),
                "valid": bool(mesh.is_watertight),
                "volume_mm3": float(mesh.volume) if mesh.is_watertight else None,
                "bbox": bbox,
                "assembly_bbox": bbox,
                "stl": f"parts/{name}.stl",
                "assembly_stl": f"assembly/{name}.stl",
                "pose": {},
            }
        )
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return BuildResult(True, out_dir, manifest=manifest, seconds=time.time() - t0)


def build(
    script: Path, out_dir: Path, settings: Settings | None = None, overrides: dict | None = None
) -> BuildResult:
    s = settings or Settings.load()
    if script.suffix == ".scad":
        return build_openscad(script, out_dir, s, s.build_timeout_s)
    return build_python(script, out_dir, s.build_timeout_s, overrides)
