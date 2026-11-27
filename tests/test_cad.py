import io

import numpy as np
import pytest
import trimesh

from printwright.cad.build import build_python
from printwright.cad.guard import check_openscad, check_python
from printwright.cad.organic import capsule, half_space_above, mesh, rounded_box, sphere

from .conftest import BOX_TWO_PARTS

# ------------------------------------------------------------------ guard


@pytest.mark.parametrize(
    "src, needle",
    [
        ("import os\ndef build(p): pass", "import of 'os'"),
        ("import subprocess\ndef build(p): pass", "import of 'subprocess'"),
        ("from pathlib import Path\ndef build(p): pass", "import from 'pathlib'"),
        ("def build(p):\n    open('x','w')", "open()"),
        ("def build(p):\n    eval('1')", "eval()"),
        ("def build(p):\n    return ().__class__.__bases__", "dunder"),
        ("import cadquery as cq\ndef build(p):\n    cq.exporters.export(1, 'x')", "exporters"),
        ("import cadquery as cq", "must define build"),
    ],
)
def test_guard_rejects(src, needle):
    issues = " ".join(map(str, check_python(src)))
    assert needle in issues


def test_guard_accepts_normal_scripts():
    assert check_python(BOX_TWO_PARTS) == []
    organic = "from printwright.cad.organic import sphere, mesh\nimport math\ndef build(p):\n    return {'b': mesh(sphere(5))}"
    assert check_python(organic) == []


def test_openscad_guard():
    assert check_openscad("include </etc/passwd>") != []
    assert check_openscad('import("../../x.stl");') != []
    assert check_openscad("cube(10);") == []


# ------------------------------------------------------------------ organic SDF


def test_sphere_mesh_is_watertight_and_accurate():
    m = mesh(sphere(10), voxel=0.5)
    assert m.is_watertight
    assert len(m.split(only_watertight=False)) == 1
    assert m.volume == pytest.approx(4 / 3 * np.pi * 1000, rel=0.01)


def test_smooth_union_and_flat_base():
    shape = sphere(8, (0, 0, 8)).smooth_union(capsule((0, 0, 14), (0, 0, 24), 3), 3) & half_space_above(1.0)
    m = mesh(shape.translate((0, 0, -1.0)), voxel=0.5)
    assert m.is_watertight
    assert m.bounds[0][2] == pytest.approx(0.0, abs=0.3)
    # the cut leaves a flat disc on the bed
    bottom = m.area_faces[(m.face_normals[:, 2] < -0.99) & (m.triangles_center[:, 2] < 0.3)].sum()
    assert bottom > 30  # cut circle of the r=8 sphere at z=1 is ~47 mm2


def test_rounded_box_size():
    m = mesh(rounded_box((20, 10, 6), radius=1.0), voxel=0.25)
    assert m.extents == pytest.approx([20, 10, 6], abs=0.3)


def test_unbounded_shape_is_rejected():
    with pytest.raises(ValueError, match="unbounded"):
        mesh(half_space_above(0))


def test_rotation_moves_bounds():
    s = capsule((0, 0, 0), (10, 0, 0), 1).rotate("z", 90)
    lo, hi = s.bounds
    assert hi[1] == pytest.approx(11, abs=1e-6)


# ------------------------------------------------------------------ runner


def test_two_part_build_pose_and_touching_parts(tmp_path):
    script = tmp_path / "model.py"
    script.write_text(BOX_TWO_PARTS)
    r = build_python(script, tmp_path / "out")
    assert r.ok, r.error
    parts = {p["name"]: p for p in r.manifest["parts"]}
    assert parts["base"]["kind"] == "brep" and parts["base"]["valid"]
    assert parts["lid"]["assembly_bbox"]["min"][2] == pytest.approx(17.0)
    assert parts["lid"]["bbox"]["min"][2] == pytest.approx(0.0, abs=1e-6)  # print pose rests on the bed
    assert (tmp_path / "out" / "parts" / "lid.step").exists()
    assert (tmp_path / "out" / "assembly.step").exists()
    inter = r.manifest["interference"][0]
    assert inter["overlap_mm3"] == 0  # the lid only touches the rim
    assert inter["min_distance_mm"] == pytest.approx(0.0, abs=1e-6)


def test_overlap_is_detected(tmp_path):
    script = tmp_path / "model.py"
    script.write_text(BOX_TWO_PARTS.replace('"lip_clearance": 0.2', '"lip_clearance": -0.5'))
    r = build_python(script, tmp_path / "out")
    assert r.ok
    assert r.manifest["interference"][0]["overlap_mm3"] > 10


def test_runtime_error_is_reported(tmp_path):
    script = tmp_path / "model.py"
    script.write_text(
        "import cadquery as cq\ndef build(p):\n    return {'a': cq.Workplane().box(1, 1, 1).fillet(5)}\n"
    )
    r = build_python(script, tmp_path / "out")
    assert not r.ok and r.stage == "run"
    assert "model.py" in r.error


def test_guard_stage(tmp_path):
    script = tmp_path / "model.py"
    script.write_text("import os\ndef build(p):\n    return {}\n")
    r = build_python(script, tmp_path / "out")
    assert not r.ok and r.stage == "guard"


def test_organic_part_build(tmp_path):
    script = tmp_path / "model.py"
    script.write_text(
        "from printwright.cad.organic import sphere, half_space_above, mesh\n"
        "PARAMS = {'r': 6.0}\n"
        "def build(p):\n"
        "    return {'ball': mesh((sphere(p['r'], (0, 0, p['r'])) & half_space_above(1)).translate((0, 0, -1)), voxel=0.5)}\n"
    )
    r = build_python(script, tmp_path / "out")
    assert r.ok, r.error
    part = r.manifest["parts"][0]
    assert part["kind"] == "mesh" and part["valid"] and part["solids"] == 1
    assert part["bbox"]["min"][2] == pytest.approx(0, abs=1e-6)


def test_mesh_parts_meet_without_overlap():
    import manifold3d

    from printwright.cad.organic import mesh_parts

    body = sphere(10, (0, 0, 10))
    patch = sphere(4, (0, -8, 10))  # pokes out of the body's front
    parts = mesh_parts({"body": body, "patch": patch}, voxel=0.5)
    for m in parts.values():
        assert m.is_watertight and len(m.split(only_watertight=False)) == 1
        # still watertight after an STL round trip (readers merge vertices by position)
        buf = io.BytesIO()
        m.export(buf, file_type="stl")
        buf.seek(0)
        assert trimesh.load(buf, file_type="stl").is_watertight

    def man(m):
        return manifold3d.Manifold(
            manifold3d.Mesh(
                vert_properties=np.asarray(m.vertices, np.float32), tri_verts=np.asarray(m.faces, np.uint32)
            )
        )

    assert (man(parts["body"]) ^ man(parts["patch"])).volume() < 1e-3
    # together they fill exactly the body's outline; the patch was trimmed to it
    whole = mesh(body, voxel=0.5).volume
    assert parts["body"].volume + parts["patch"].volume == pytest.approx(whole, rel=1e-3)
    assert parts["patch"].volume < 4 / 3 * np.pi * 64
