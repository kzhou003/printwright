"""Render four views of a set of parts: Blender when available, matplotlib otherwise."""

from __future__ import annotations

import contextlib
import json
import subprocess
from pathlib import Path

import numpy as np

from printwright.config import Settings, find_blender, native_cmd

VIEWS = ("iso", "front", "right", "top")


def render(
    parts: list[dict],
    out_dir: Path,
    settings: Settings | None = None,
    blend: Path | None = None,
    bed: bool = True,
) -> dict:
    """parts: [{name, stl (path), color}]. Returns {engine, views: {name: path}, sheet, blend?, error?}."""
    s = settings or Settings.load()
    out_dir.mkdir(parents=True, exist_ok=True)
    blender = find_blender(s) if s.renderer in ("auto", "blender") else None
    result: dict = {}
    if blender:
        result = _blender(blender, parts, out_dir, blend, bed)
    if not result.get("views"):
        err = result.get("error")
        result = _matplotlib(parts, out_dir)
        if err:
            result["blender_error"] = err
    result["sheet"] = str(contact_sheet([Path(result["views"][v]) for v in VIEWS], out_dir / "sheet.png"))
    return result


def _blender(exe: Path, parts: list[dict], out_dir: Path, blend: Path | None, bed: bool) -> dict:
    job = {
        "parts": [{**p, "stl": str(p["stl"])} for p in parts],
        "out_dir": str(out_dir),
        "bed": bed,
        "blend": str(blend) if blend else None,
        "resolution": 900,
    }
    jf = out_dir / "render_job.json"
    jf.write_text(json.dumps(job), encoding="utf-8")
    script = Path(__file__).with_name("blender_scene.py")
    cmd = native_cmd([str(exe), "-b", "--factory-startup", "-P", str(script), "--", str(jf)])
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired:
        return {"error": "Blender timed out"}
    (out_dir / "render.log").write_text(proc.stdout[-20000:] + proc.stderr[-20000:], encoding="utf-8")
    views = {v: str(out_dir / f"{v}.png") for v in VIEWS if (out_dir / f"{v}.png").exists()}
    if len(views) != len(VIEWS):
        tail = "\n".join((proc.stdout + proc.stderr).strip().splitlines()[-12:])
        return {"error": f"Blender render failed (exit {proc.returncode}):\n{tail}"}
    out = {"engine": "blender", "views": views}
    if blend and blend.exists():
        out["blend"] = str(blend)
    return out


def _matplotlib(parts: list[dict], out_dir: Path) -> dict:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import trimesh
    from mpl_toolkits.mplot3d.art3d import Poly3DCollection

    meshes = []
    for p in parts:
        m = trimesh.load(p["stl"], force="mesh")
        if len(m.faces) > 60000:
            with contextlib.suppress(Exception):
                m = m.simplify_quadric_decimation(face_count=60000)
        meshes.append((m, p.get("color", "#cccccc")))
    lo = np.min([m.bounds[0] for m, _ in meshes], axis=0)
    hi = np.max([m.bounds[1] for m, _ in meshes], axis=0)
    c, r = (lo + hi) / 2, max(float((hi - lo).max()) / 2, 1.0)
    light = np.array([0.4, -0.5, 0.75])
    light /= np.linalg.norm(light)
    angles = {"iso": (28, -55), "front": (0, -90), "right": (0, 0), "top": (90, -90)}
    views = {}
    for name, (elev, azim) in angles.items():
        fig = plt.figure(figsize=(6, 6), dpi=150)
        ax = fig.add_subplot(111, projection="3d", proj_type="ortho" if name != "iso" else "persp")
        for m, col in meshes:
            base = np.array(matplotlib.colors.to_rgb(col))
            shade = 0.35 + 0.65 * np.clip(m.face_normals @ light, 0, 1)
            fc = np.clip(base[None, :] * shade[:, None], 0, 1)
            pc = Poly3DCollection(m.triangles, facecolors=fc, edgecolors="none", linewidths=0)
            ax.add_collection3d(pc)
        for i, setter in enumerate((ax.set_xlim, ax.set_ylim, ax.set_zlim)):
            setter(c[i] - r, c[i] + r)
        ax.set_box_aspect((1, 1, 1))
        ax.view_init(elev=elev, azim=azim)
        ax.set_axis_off()
        path = out_dir / f"{name}.png"
        fig.savefig(path, bbox_inches="tight", facecolor="white")
        plt.close(fig)
        views[name] = str(path)
    return {"engine": "matplotlib", "views": views}


def contact_sheet(images: list[Path], out: Path, cell: int = 600) -> Path:
    """2x2 grid with labels: one image is cheaper for vision review than four."""
    from PIL import Image, ImageDraw

    sheet = Image.new("RGB", (cell * 2, cell * 2), "white")
    draw = ImageDraw.Draw(sheet)
    for i, p in enumerate(images[:4]):
        im = Image.open(p).convert("RGB")
        im.thumbnail((cell, cell))
        x, y = (i % 2) * cell, (i // 2) * cell
        sheet.paste(im, (x + (cell - im.width) // 2, y + (cell - im.height) // 2))
        draw.text((x + 10, y + 8), p.stem, fill=(40, 40, 40))
    sheet.save(out)
    return out
