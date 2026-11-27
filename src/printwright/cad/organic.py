"""Organic modelling with signed distance fields (creative mode).

B-rep CAD is the right tool for brackets and gears and the wrong one for a cat. Here shapes are signed
distance functions (negative inside), combined with smooth blends, and meshed with marching cubes. The
result is always a closed, watertight mesh, which is exactly what a slicer wants.

    from printwright.cad.organic import sphere, ellipsoid, capsule, rounded_box, half_space, mesh

    head = sphere(12, (0, 0, 40))
    body = ellipsoid((14, 11, 18), (0, 0, 20))
    ear = capsule((6, 0, 50), (9, 0, 58), 3)
    cat = body.smooth_union(head, 4).smooth_union(ear, 2).smooth_union(ear.mirror_x(), 2)
    cat = cat & half_space_above(0)          # flat base on the bed
    return {"body": mesh(cat, voxel=0.4)}

All lengths are millimetres. Every primitive knows a conservative bounding box, so mesh() needs no bounds.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence

import numpy as np

Vec = Sequence[float]
Bounds = tuple[np.ndarray, np.ndarray] | None  # (lo, hi); None = unbounded
_INF = np.array([np.inf] * 3)


def _b(lo: Vec, hi: Vec) -> Bounds:
    return (np.asarray(lo, float), np.asarray(hi, float))


def _union_bounds(a: Bounds, b: Bounds, pad: float = 0.0) -> Bounds:
    if a is None or b is None:
        return None
    return (np.minimum(a[0], b[0]) - pad, np.maximum(a[1], b[1]) + pad)


def _intersect_bounds(a: Bounds, b: Bounds) -> Bounds:
    if a is None:
        return b
    if b is None:
        return a
    return (np.maximum(a[0], b[0]), np.minimum(a[1], b[1]))


def _transform_bounds(b: Bounds, fn: Callable[[np.ndarray], np.ndarray]) -> Bounds:
    if b is None:
        return None
    lo, hi = b
    corners = np.array([[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])])
    t = fn(corners)
    return (t.min(axis=0), t.max(axis=0))


def _smin(a: np.ndarray, b: np.ndarray, k: float) -> np.ndarray:
    """Polynomial smooth minimum (Quilez). k = blend radius in mm."""
    if k <= 0:
        return np.minimum(a, b)
    h = np.clip(0.5 + 0.5 * (b - a) / k, 0.0, 1.0)
    return b * (1 - h) + a * h - k * h * (1 - h)


def _rot(axis: str, deg: float) -> np.ndarray:
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return {
        "x": np.array([[1, 0, 0], [0, c, -s], [0, s, c]]),
        "y": np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]]),
        "z": np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]]),
    }[axis]


class SDF:
    """A signed distance field: f(points[N,3]) -> distances[N] (negative inside)."""

    def __init__(self, f: Callable[[np.ndarray], np.ndarray], bounds: Bounds):
        self.f = f
        self.bounds = bounds

    def __call__(self, p: np.ndarray) -> np.ndarray:
        return self.f(p)

    # boolean operators -----------------------------------------------------------------------
    def __or__(self, o: SDF) -> SDF:
        return SDF(lambda p: np.minimum(self(p), o(p)), _union_bounds(self.bounds, o.bounds))

    def __and__(self, o: SDF) -> SDF:
        return SDF(lambda p: np.maximum(self(p), o(p)), _intersect_bounds(self.bounds, o.bounds))

    def __sub__(self, o: SDF) -> SDF:
        return SDF(lambda p: np.maximum(self(p), -o(p)), self.bounds)

    def smooth_union(self, o: SDF, k: float) -> SDF:
        return SDF(lambda p: _smin(self(p), o(p), k), _union_bounds(self.bounds, o.bounds, k))

    def smooth_subtract(self, o: SDF, k: float) -> SDF:
        return SDF(lambda p: -_smin(-self(p), o(p), k), self.bounds)

    def smooth_intersect(self, o: SDF, k: float) -> SDF:
        return SDF(lambda p: -_smin(-self(p), -o(p), k), _intersect_bounds(self.bounds, o.bounds))

    # shaping ---------------------------------------------------------------------------------
    def round(self, r: float) -> SDF:
        """Grow by r with rounded edges (shape gets r bigger)."""
        b = None if self.bounds is None else (self.bounds[0] - r, self.bounds[1] + r)
        return SDF(lambda p: self(p) - r, b)

    def shell(self, t: float) -> SDF:
        """Hollow skin of thickness t centred on the surface."""
        b = None if self.bounds is None else (self.bounds[0] - t, self.bounds[1] + t)
        return SDF(lambda p: np.abs(self(p)) - t / 2, b)

    # transforms ------------------------------------------------------------------------------
    def translate(self, v: Vec) -> SDF:
        v = np.asarray(v, float)
        return SDF(lambda p: self(p - v), _transform_bounds(self.bounds, lambda c: c + v))

    def rotate(self, axis: str, deg: float, center: Vec = (0, 0, 0)) -> SDF:
        R = _rot(axis, deg)
        c = np.asarray(center, float)
        return SDF(
            lambda p: self((p - c) @ R + c), _transform_bounds(self.bounds, lambda q: (q - c) @ R.T + c)
        )

    def scale(self, s: float) -> SDF:
        return SDF(lambda p: self(p / s) * s, _transform_bounds(self.bounds, lambda c: c * s))

    def mirror_x(self) -> SDF:
        m = np.array([-1.0, 1.0, 1.0])
        return SDF(lambda p: self(p * m), _transform_bounds(self.bounds, lambda c: c * m))

    def mirror_y(self) -> SDF:
        m = np.array([1.0, -1.0, 1.0])
        return SDF(lambda p: self(p * m), _transform_bounds(self.bounds, lambda c: c * m))


# --------------------------------------------------------------------------- primitives


def sphere(r: float, center: Vec = (0, 0, 0)) -> SDF:
    c = np.asarray(center, float)
    return SDF(lambda p: np.linalg.norm(p - c, axis=1) - r, _b(c - r, c + r))


def ellipsoid(radii: Vec, center: Vec = (0, 0, 0)) -> SDF:
    r = np.asarray(radii, float)
    c = np.asarray(center, float)

    def f(p: np.ndarray) -> np.ndarray:  # Quilez' bound-preserving approximation
        q = p - c
        k0 = np.linalg.norm(q / r, axis=1)
        k1 = np.linalg.norm(q / (r * r), axis=1)
        return np.where(k1 > 1e-12, k0 * (k0 - 1.0) / np.maximum(k1, 1e-12), -r.min())

    return SDF(f, _b(c - r, c + r))


def capsule(a: Vec, b: Vec, r: float) -> SDF:
    a_, b_ = np.asarray(a, float), np.asarray(b, float)
    ba = b_ - a_
    denom = float(ba @ ba) or 1e-12

    def f(p: np.ndarray) -> np.ndarray:
        pa = p - a_
        h = np.clip((pa @ ba) / denom, 0.0, 1.0)
        return np.linalg.norm(pa - h[:, None] * ba, axis=1) - r

    return SDF(f, _b(np.minimum(a_, b_) - r, np.maximum(a_, b_) + r))


def rounded_cone(a: Vec, b: Vec, ra: float, rb: float) -> SDF:
    """A limb that tapers from radius ra at a to rb at b (ends rounded). Approximate but well-behaved."""
    a_, b_ = np.asarray(a, float), np.asarray(b, float)
    ba = b_ - a_
    denom = float(ba @ ba) or 1e-12

    def f(p: np.ndarray) -> np.ndarray:
        pa = p - a_
        h = np.clip((pa @ ba) / denom, 0.0, 1.0)
        return np.linalg.norm(pa - h[:, None] * ba, axis=1) - (ra + (rb - ra) * h)

    r = max(ra, rb)
    return SDF(f, _b(np.minimum(a_, b_) - r, np.maximum(a_, b_) + r))


def rounded_box(size: Vec, center: Vec = (0, 0, 0), radius: float = 0.0) -> SDF:
    h = np.asarray(size, float) / 2 - radius
    c = np.asarray(center, float)

    def f(p: np.ndarray) -> np.ndarray:
        q = np.abs(p - c) - h
        return np.linalg.norm(np.maximum(q, 0), axis=1) + np.minimum(q.max(axis=1), 0) - radius

    half = np.asarray(size, float) / 2
    return SDF(f, _b(c - half, c + half))


def cylinder(r: float, height: float, center: Vec = (0, 0, 0), radius: float = 0.0) -> SDF:
    """Z-axis cylinder centred on `center`, optional edge rounding."""
    c = np.asarray(center, float)

    def f(p: np.ndarray) -> np.ndarray:
        q = p - c
        d = np.stack(
            [np.hypot(q[:, 0], q[:, 1]) - (r - radius), np.abs(q[:, 2]) - (height / 2 - radius)], axis=1
        )
        return np.minimum(d.max(axis=1), 0) + np.linalg.norm(np.maximum(d, 0), axis=1) - radius

    return SDF(f, _b(c - [r, r, height / 2], c + [r, r, height / 2]))


def torus(R: float, r: float, center: Vec = (0, 0, 0)) -> SDF:
    """Torus in the XY plane."""
    c = np.asarray(center, float)

    def f(p: np.ndarray) -> np.ndarray:
        q = p - c
        return np.hypot(np.hypot(q[:, 0], q[:, 1]) - R, q[:, 2]) - r

    return SDF(f, _b(c - [R + r, R + r, r], c + [R + r, R + r, r]))


def half_space_above(z0: float = 0.0) -> SDF:
    """Everything with z >= z0. Intersect with it to cut a flat base."""
    return SDF(lambda p: z0 - p[:, 2], None)


def half_space_below(z0: float) -> SDF:
    return SDF(lambda p: p[:, 2] - z0, None)


# --------------------------------------------------------------------------- meshing

MAX_VOXELS = 220**3


def mesh(sdf: SDF, voxel: float = 0.4, bounds: Bounds = None):
    """Marching cubes -> watertight trimesh.Trimesh. Voxel is grown automatically to cap memory."""
    import trimesh
    from skimage import measure

    b = bounds or sdf.bounds
    if b is None or not np.all(np.isfinite(b[0])) or not np.all(np.isfinite(b[1])):
        raise ValueError("mesh(): shape is unbounded; intersect it with a bounded primitive or pass bounds=")
    lo, hi = np.asarray(b[0], float), np.asarray(b[1], float)
    pad = 2 * voxel
    lo, hi = lo - pad, hi + pad
    span = hi - lo
    n = np.ceil(span / voxel).astype(int) + 1
    if np.prod(n) > MAX_VOXELS:
        voxel = float((np.prod(span) / MAX_VOXELS) ** (1 / 3)) * 1.02
        n = np.ceil(span / voxel).astype(int) + 1
    xs, ys, zs = (lo[i] + np.arange(n[i]) * voxel for i in range(3))
    vol = np.empty((n[0], n[1], n[2]), dtype=np.float32)
    gx, gy = np.meshgrid(xs, ys, indexing="ij")
    flat_xy = np.stack([gx.ravel(), gy.ravel()], axis=1)
    for k, z in enumerate(zs):  # one slab at a time keeps memory flat
        pts = np.column_stack([flat_xy, np.full(len(flat_xy), z)])
        vol[:, :, k] = sdf(pts).reshape(n[0], n[1])
    vol[0, :, :] = vol[-1, :, :] = vol[:, 0, :] = vol[:, -1, :] = vol[:, :, 0] = vol[:, :, -1] = voxel
    if vol.min() >= 0:
        raise ValueError("mesh(): the shape is empty (no point is inside)")
    verts, faces, _n, _v = measure.marching_cubes(
        vol, level=0.0, spacing=(voxel, voxel, voxel), allow_degenerate=False
    )
    m = trimesh.Trimesh(vertices=verts + lo, faces=faces, process=True)
    m.merge_vertices()
    if m.volume < 0:
        m.invert()
    return m


def mesh_parts(parts: dict[str, SDF], voxel: float = 0.4) -> dict:
    """Mesh a multi-colour object so the colours meet exactly, with no overlap and no gaps.

    The FIRST part is the envelope (the whole object's outline). Every later part is an inlay: it is trimmed to
    the envelope and to everything listed after it, and cut out of the parts before it, all with exact mesh
    booleans. Let inlays poke out through the surface; don't intersect them with the body yourself (shared
    coincident surfaces are what produce slivers):

        body = ...                              # full silhouette, including where the belly will be
        belly = ellipsoid((8, 5, 10), (0, -11, 16))   # pokes out of the front; trimmed automatically
        return mesh_parts({"body": body, "belly": belly}, voxel=0.35)
    """
    import manifold3d
    import trimesh

    def to_man(m):
        man = manifold3d.Manifold(
            manifold3d.Mesh(
                vert_properties=np.asarray(m.vertices, np.float32), tri_verts=np.asarray(m.faces, np.uint32)
            )
        )
        if man.status() != manifold3d.Error.NoError:
            raise ValueError(f"mesh is not manifold ({man.status().name})")
        return man

    names = list(parts)
    mans = [to_man(mesh(parts[n], voxel)) for n in names]
    envelope = mans[0]
    out = {}
    for i, n in enumerate(names):
        # Subtract the ORIGINAL later parts (whose surfaces cross this one) rather than their trimmed versions
        # (whose surfaces coincide with the envelope): coincident-surface booleans leave slivers that turn
        # non-manifold once an STL reader merges vertices by position.
        m = envelope if i == 0 else mans[i] ^ envelope
        for later in mans[i + 1 :]:
            m = m - later
        mm = m.to_mesh()
        # process=False: manifold3d output is already a valid manifold; trimesh's vertex merging can break it
        tm = trimesh.Trimesh(vertices=mm.vert_properties[:, :3], faces=mm.tri_verts, process=False)
        if len(tm.faces) == 0:
            raise ValueError(f"part {n!r} is empty after trimming and removing the parts listed after it")
        out[n] = tm
    return out
