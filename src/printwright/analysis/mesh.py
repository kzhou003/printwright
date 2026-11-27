"""Printability analysis of a part in print orientation (z up, resting on z=0).

All measurements are made on the exported STL with plain geometry so they are independent of the model
script and of the LLM that wrote it:

* integrity      watertight, consistent winding, number of separate bodies
* size           bounding box vs the printer's build volume (per nozzle for multi-nozzle printers)
* bed contact    area of downward faces touching the bed, and height / contact ratio (tipping risk)
* overhangs      downward faces steeper than the support threshold, excluding the bed layer
* wall thickness rays shot inward from surface samples; distance to the opposite wall
* holes          circular interior loops of a planar cross-section (for hole acceptance checks)
* orientation    the six axis-aligned orientations scored by support area, contact and height
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import trimesh

BED_EPS = 0.05  # mm; faces this close to z=0 count as bed contact


@dataclass
class Thickness:
    min: float
    p5: float
    median: float
    samples: int


def load(path) -> trimesh.Trimesh:
    m = trimesh.load(path, force="mesh")
    if not isinstance(m, trimesh.Trimesh) or len(m.faces) == 0:
        raise ValueError(f"{path}: not a triangle mesh")
    return m


BRIDGE_MM = 8.0  # overhang regions spanning no more than this print fine by bridging (small hole ceilings)


def overhang(m: trimesh.Trimesh, threshold_deg: float = 45.0, bridge_mm: float = BRIDGE_MM) -> dict:
    """Area of faces that need support: facing down more steeply than threshold from vertical.

    A face overhangs when the angle between its normal and straight-down is below (90 - threshold): with the
    usual 45 degree rule, normals within 45 degrees of -Z need support. Connected overhang regions whose XY span
    is at most `bridge_mm` (the top of a small horizontal hole, a short notch) bridge without support and are
    reported separately as `bridged_mm2`.
    """
    zmin = m.bounds[0][2]
    nz = m.face_normals[:, 2]
    cz = m.triangles_center[:, 2]
    limit = -math.cos(math.radians(90 - threshold_deg))
    on_bed = cz < zmin + BED_EPS
    mask = (nz < limit) & ~on_bed
    idx = np.nonzero(mask)[0]
    area = bridged = 0.0
    if len(idx):
        adj = m.face_adjacency
        keep = mask[adj[:, 0]] & mask[adj[:, 1]]
        groups = trimesh.graph.connected_components(adj[keep], nodes=idx, min_len=1)
        for g in groups:
            g = np.asarray(g)
            pts = m.triangles[g].reshape(-1, 3)
            span = float(np.max(pts[:, :2].max(axis=0) - pts[:, :2].min(axis=0)))
            a = float(m.area_faces[g].sum())
            if span <= bridge_mm:
                bridged += a
            else:
                area += a
    return {
        "area_mm2": round(area, 2),
        "bridged_mm2": round(bridged, 2),
        "fraction": round(float(area / max(m.area, 1e-9)), 4),
        "threshold_deg": threshold_deg,
    }


def bed_contact(m: trimesh.Trimesh) -> dict:
    zmin = m.bounds[0][2]
    mask = (m.face_normals[:, 2] < -0.999) & (m.triangles_center[:, 2] < zmin + BED_EPS)
    area = float(m.area_faces[mask].sum())
    h = float(m.extents[2])
    eq_d = 2 * math.sqrt(area / math.pi) if area > 0 else 0.0
    return {"area_mm2": round(area, 2), "height_to_base": round(h / eq_d, 2) if eq_d else None}


def wall_thickness(m: trimesh.Trimesh, samples: int = 3000, seed: int = 0) -> Thickness | None:
    if not m.is_watertight:
        return None
    pts, fidx = (
        trimesh.sample.sample_surface_even(m, samples, seed=seed)
        if hasattr(trimesh.sample, "sample_surface_even")
        else trimesh.sample.sample_surface(m, samples, seed=seed)
    )
    if len(pts) == 0:
        return None
    normals = m.face_normals[fidx]
    origins = pts - normals * 1e-3
    locs, ray_idx, _ = m.ray.intersects_location(origins, -normals, multiple_hits=False)
    if len(locs) == 0:
        return None
    d = np.linalg.norm(locs - origins[ray_idx], axis=1)
    d = d[d > 1e-4]
    if len(d) == 0:
        return None
    return Thickness(float(d.min()), float(np.percentile(d, 5)), float(np.median(d)), int(len(d)))


def holes_at(
    m: trimesh.Trimesh, axis: str = "z", at: float = 0.0, min_circularity: float = 0.85
) -> list[dict]:
    """Round holes cut by the plane axis=at. Returns diameter (area-equivalent), circularity and centre."""
    from shapely.geometry import Polygon

    normal = {"x": [1, 0, 0], "y": [0, 1, 0], "z": [0, 0, 1]}[axis]
    origin = [0.0, 0.0, 0.0]
    origin["xyz".index(axis)] = at
    sec = m.section(plane_origin=origin, plane_normal=normal)
    if sec is None:
        return []
    planar, to_3d = sec.to_2D()
    out = []
    for poly in planar.polygons_full:
        for ring in poly.interiors:
            hole = Polygon(ring)
            a, p = hole.area, hole.length
            if a <= 0 or p <= 0:
                continue
            circ = 4 * math.pi * a / (p * p)
            if circ < min_circularity:
                continue
            c2 = np.array([hole.centroid.x, hole.centroid.y, 0.0, 1.0])
            c3 = (to_3d @ c2)[:3]
            out.append(
                {
                    "diameter": round(2 * math.sqrt(a / math.pi), 4),
                    "circularity": round(circ, 3),
                    "center": [round(float(v), 3) for v in c3],
                }
            )
    return sorted(out, key=lambda h: h["diameter"])


ORIENTATIONS = {
    "as modelled": (0, 0, 0),
    "upside down": (180, 0, 0),
    "on +Y side": (90, 0, 0),
    "on -Y side": (-90, 0, 0),
    "on -X side": (0, 90, 0),
    "on +X side": (0, -90, 0),
}


def orientation_study(m: trimesh.Trimesh, threshold_deg: float = 45.0) -> list[dict]:
    rows = []
    for name, (rx, ry, rz) in ORIENTATIONS.items():
        mm = m.copy()
        for axis, ang in (((1, 0, 0), rx), ((0, 1, 0), ry), ((0, 0, 1), rz)):
            if ang:
                mm.apply_transform(trimesh.transformations.rotation_matrix(math.radians(ang), axis))
        mm.apply_translation([0, 0, -mm.bounds[0][2]])
        oh = overhang(mm, threshold_deg)
        bc = bed_contact(mm)
        rows.append(
            {
                "orientation": name,
                "rotate": [rx, ry, rz],
                "overhang_mm2": oh["area_mm2"],
                "contact_mm2": bc["area_mm2"],
                "height_mm": round(float(mm.extents[2]), 2),
            }
        )
    # lower support area first, then more bed contact, then lower height
    rows.sort(key=lambda r: (round(r["overhang_mm2"], 0), -r["contact_mm2"], r["height_mm"]))
    return rows


def union_mesh(paths) -> trimesh.Trimesh:
    """Exact union of several closed meshes (manifold3d), shifted so it rests on z=0."""
    import manifold3d

    acc = None
    for p in paths:
        m = load(p)
        man = manifold3d.Manifold(
            manifold3d.Mesh(
                vert_properties=np.asarray(m.vertices, np.float32), tri_verts=np.asarray(m.faces, np.uint32)
            )
        )
        acc = man if acc is None else acc + man
    mm = acc.to_mesh()
    out = trimesh.Trimesh(vertices=mm.vert_properties[:, :3], faces=mm.tri_verts, process=False)
    out.apply_translation([0, 0, -out.bounds[0][2]])
    return out


def analyze_part(
    path, name: str, line_width: float = 0.42, threshold_deg: float = 45.0, thickness_samples: int = 3000
) -> dict:
    return analyze_mesh(load(path), name, line_width, threshold_deg, thickness_samples)


def analyze_mesh(
    m: trimesh.Trimesh,
    name: str,
    line_width: float = 0.42,
    threshold_deg: float = 45.0,
    thickness_samples: int = 3000,
) -> dict:
    bodies = m.split(only_watertight=False)
    lo, hi = m.bounds
    t = wall_thickness(m, thickness_samples) if thickness_samples else None
    return {
        "name": name,
        "triangles": int(len(m.faces)),
        "watertight": bool(m.is_watertight),
        "winding_consistent": bool(m.is_winding_consistent),
        "bodies": int(len(bodies)),
        "volume_mm3": round(float(m.volume), 2) if m.is_watertight else None,
        "area_mm2": round(float(m.area), 2),
        "bbox": {
            "min": lo.round(3).tolist(),
            "max": hi.round(3).tolist(),
            "size": (hi - lo).round(3).tolist(),
        },
        "on_bed": bool(abs(lo[2]) < 0.02),
        "bed_contact": bed_contact(m),
        "overhang": overhang(m, threshold_deg),
        "thickness": None
        if t is None
        else {
            "min": round(t.min, 3),
            "p5": round(t.p5, 3),
            "median": round(t.median, 3),
            "samples": t.samples,
        },
        "line_width": line_width,
        "orientations": orientation_study(m, threshold_deg),
    }
