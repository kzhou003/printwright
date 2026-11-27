"""Executes one model script in a child process and exports its parts.

    python -m printwright.cad.runner <script.py> <out_dir> [params.json]

Script contract (CadQuery or organic backend):

    PARAMS = {"width": 40.0, ...}           # every dimension, in mm, named like the spec
    PRINT_POSE = {"lid": {"rotate": [180, 0, 0]}}   # optional; degrees about X, then Y, then Z
    def build(p):                          # p is PARAMS (possibly with overrides)
        return {"base": base, "lid": lid}  # parts in their ASSEMBLED position

A part may be a cadquery Workplane / Shape / Assembly (exact B-rep, exported to STEP and STL), a
trimesh.Trimesh, a manifold3d.Manifold, or a printwright.cad.organic SDF (meshed automatically).

Outputs in out_dir:
    parts/<name>.stl, parts/<name>.step   print pose: rotated by PRINT_POSE, resting on z=0, centred in XY
    assembly/<name>.stl, assembly.step    assembled pose, for renders and interference checks
    manifest.json                         sizes, volumes, validity, pairwise interference
Any exception is printed to stderr with a traceback and the exit code is 1.
"""

from __future__ import annotations

import importlib.util
import json
import math
import sys
import traceback
from pathlib import Path

STL_TOL = 0.01  # mm chordal deviation
STL_ANG = 0.1  # rad


def _load(script: Path):
    spec = importlib.util.spec_from_file_location("pw_model", script)
    mod = importlib.util.module_from_spec(spec)
    mod.__dict__["show_object"] = lambda *a, **k: None  # tolerate CQ-editor style scripts
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def _to_part(obj):
    """Normalise to ('brep', cq.Shape) or ('mesh', trimesh.Trimesh)."""
    import cadquery as cq
    import trimesh

    if isinstance(obj, cq.Workplane):
        vals = [v for v in obj.vals() if isinstance(v, cq.Shape)]
        if not vals:
            raise ValueError("Workplane has no solids")
        return "brep", vals[0] if len(vals) == 1 else cq.Compound.makeCompound(vals)
    if isinstance(obj, cq.Assembly):
        return "brep", obj.toCompound()
    if isinstance(obj, cq.Shape):
        return "brep", obj
    if isinstance(obj, trimesh.Trimesh):
        return "mesh", obj
    try:
        import manifold3d

        if isinstance(obj, manifold3d.Manifold):
            m = obj.to_mesh()
            return "mesh", trimesh.Trimesh(
                vertices=m.vert_properties[:, :3], faces=m.tri_verts, process=False
            )
    except ImportError:
        pass
    from printwright.cad.organic import SDF, mesh

    if isinstance(obj, SDF):
        return "mesh", mesh(obj)
    raise TypeError(f"unsupported part type {type(obj).__name__}")


def _pose_brep(shape, pose: dict):
    import cadquery as cq

    rx, ry, rz = (list(pose.get("rotate", [0, 0, 0])) + [0, 0, 0])[:3]
    for axis, ang in (((1, 0, 0), rx), ((0, 1, 0), ry), ((0, 0, 1), rz)):
        if ang:
            shape = shape.rotate(cq.Vector(0, 0, 0), cq.Vector(*axis), float(ang))
    bb = shape.BoundingBox()
    return shape.translate(cq.Vector(-(bb.xmin + bb.xmax) / 2, -(bb.ymin + bb.ymax) / 2, -bb.zmin))


def _pose_mesh(m, pose: dict):
    import numpy as np
    import trimesh

    m = m.copy()
    rx, ry, rz = (list(pose.get("rotate", [0, 0, 0])) + [0, 0, 0])[:3]
    for axis, ang in (((1, 0, 0), rx), ((0, 1, 0), ry), ((0, 0, 1), rz)):
        if ang:
            m.apply_transform(trimesh.transformations.rotation_matrix(math.radians(float(ang)), axis))
    lo, hi = m.bounds
    m.apply_translation(np.array([-(lo[0] + hi[0]) / 2, -(lo[1] + hi[1]) / 2, -lo[2]]))
    return m


def _bbox(kind, obj) -> dict:
    if kind == "brep":
        b = obj.BoundingBox()
        lo, hi = (b.xmin, b.ymin, b.zmin), (b.xmax, b.ymax, b.zmax)
    else:
        lo, hi = obj.bounds
    return {
        "min": [round(float(v), 4) for v in lo],
        "max": [round(float(v), 4) for v in hi],
        "size": [round(float(b - a), 4) for a, b in zip(lo, hi, strict=True)],
    }


def _interference(parts: dict) -> list[dict]:
    """Pairwise overlap volume (mm^3) and minimum distance in the assembled pose."""
    out = []
    names = list(parts)
    for i, a in enumerate(names):
        for b in names[i + 1 :]:
            ka, sa = parts[a]
            kb, sb = parts[b]
            rec = {"a": a, "b": b}
            try:
                # Overlap volume comes from manifold3d on the tessellated parts. OpenCascade's B-rep
                # intersection is unreliable with coplanar faces (a lid resting on a rim): it returns
                # open shells whose Volume() is wrong in both directions. Manifold booleans are exact on
                # the mesh and give 0 for touching parts.
                ma, mb = (_as_manifold(k, s) for k, s in ((ka, sa), (kb, sb)))
                rec["overlap_mm3"] = round(max(float((ma ^ mb).volume()), 0.0), 5)
                if ka == kb == "brep":
                    rec["min_distance_mm"] = round(float(sa.distance(sb)), 4)
            except Exception as e:  # report, never crash the build on a check
                rec["error"] = f"{type(e).__name__}: {e}"
            out.append(rec)
    return out


def _as_manifold(kind, shape):
    import manifold3d
    import numpy as np

    if kind == "brep":
        import trimesh

        verts, tris = shape.tessellate(STL_TOL, STL_ANG)
        m = trimesh.Trimesh([(v.x, v.y, v.z) for v in verts], tris, process=True)
    else:
        m = shape
    man = manifold3d.Manifold(
        manifold3d.Mesh(
            vert_properties=np.asarray(m.vertices, np.float32), tri_verts=np.asarray(m.faces, np.uint32)
        )
    )
    if man.status() != manifold3d.Error.NoError:  # never report "no overlap" for a mesh we could not use
        raise ValueError(f"mesh is not a closed manifold ({man.status().name})")
    return man


def run(script: Path, out: Path, overrides: dict | None = None) -> dict:
    import cadquery as cq

    mod = _load(script)
    params = dict(getattr(mod, "PARAMS", {}) or {})
    params.update(overrides or {})
    poses = dict(getattr(mod, "PRINT_POSE", {}) or {})
    result = mod.build(params) if mod.build.__code__.co_argcount else mod.build()
    if not isinstance(result, dict):
        result = {"body": result}
    if not result:
        raise ValueError("build() returned no parts")

    parts = {str(k): _to_part(v) for k, v in result.items()}
    (out / "parts").mkdir(parents=True, exist_ok=True)
    (out / "assembly").mkdir(parents=True, exist_ok=True)
    manifest = {"params": params, "parts": [], "interference": []}
    assy = cq.Assembly()
    for name, (kind, obj) in parts.items():
        rec: dict = {"name": name, "kind": kind}
        if kind == "brep":
            solids = obj.Solids()
            rec["solids"] = len(solids)
            rec["valid"] = bool(obj.isValid())
            rec["volume_mm3"] = round(float(obj.Volume()), 3)
            rec["assembly_bbox"] = _bbox(kind, obj)
            cq.exporters.export(
                obj, str(out / "assembly" / f"{name}.stl"), tolerance=STL_TOL, angularTolerance=STL_ANG
            )
            assy.add(obj, name=name)
            posed = _pose_brep(obj, poses.get(name, {}))
            cq.exporters.export(posed, str(out / "parts" / f"{name}.step"))
            cq.exporters.export(
                posed, str(out / "parts" / f"{name}.stl"), tolerance=STL_TOL, angularTolerance=STL_ANG
            )
            rec["step"] = f"parts/{name}.step"
            rec["bbox"] = _bbox(kind, posed)
        else:
            rec["solids"] = len(obj.split(only_watertight=False))
            rec["valid"] = bool(obj.is_watertight)
            rec["volume_mm3"] = round(float(obj.volume), 3) if obj.is_watertight else None
            rec["assembly_bbox"] = _bbox(kind, obj)
            obj.export(out / "assembly" / f"{name}.stl")
            posed = _pose_mesh(obj, poses.get(name, {}))
            posed.export(out / "parts" / f"{name}.stl")
            rec["bbox"] = _bbox(kind, posed)
        rec["stl"] = f"parts/{name}.stl"
        rec["assembly_stl"] = f"assembly/{name}.stl"
        rec["pose"] = poses.get(name, {})
        manifest["parts"].append(rec)
    if any(k == "brep" for k, _ in parts.values()):
        try:
            assy.save(str(out / "assembly.step"))
            manifest["assembly_step"] = "assembly.step"
        except Exception as e:
            manifest["assembly_step_error"] = str(e)
    if len(parts) > 1:
        manifest["interference"] = _interference(parts)
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if len(argv) < 2:
        print(__doc__, file=sys.stderr)
        return 2
    script, out = Path(argv[0]).resolve(), Path(argv[1]).resolve()
    overrides = json.loads(Path(argv[2]).read_text()) if len(argv) > 2 else None
    out.mkdir(parents=True, exist_ok=True)
    try:
        m = run(script, out, overrides)
    except Exception:
        traceback.print_exc()
        return 1
    print(json.dumps({"ok": True, "parts": [p["name"] for p in m["parts"]]}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
