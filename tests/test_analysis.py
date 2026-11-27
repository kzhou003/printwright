import json

import cadquery as cq
import pytest
import trimesh

from printwright.analysis.acceptance import evaluate
from printwright.analysis.mesh import analyze_part, holes_at, overhang, wall_thickness
from printwright.cad.build import build_python
from printwright.spec import AcceptanceCheck, Dimension, Part, PrintPlan, Spec

from .conftest import BOX_TWO_PARTS


def stl(tmp_path, wp, name="p.stl"):
    f = tmp_path / name
    cq.exporters.export(wp, str(f), tolerance=0.01, angularTolerance=0.1)
    return f


def test_wall_thickness_of_shelled_box(tmp_path):
    f = stl(tmp_path, cq.Workplane().box(40, 30, 20, centered=(True, True, False)).faces(">Z").shell(-2))
    t = wall_thickness(trimesh.load(f), samples=2000)
    assert t.p5 == pytest.approx(2.0, abs=0.05)


def test_hole_detection(tmp_path):
    wp = (
        cq.Workplane()
        .box(30, 30, 5, centered=(True, True, False))
        .faces(">Z")
        .workplane()
        .pushPoints([(-8, 0), (8, 0)])
        .hole(3.4)
    )
    m = trimesh.load(stl(tmp_path, wp))
    holes = holes_at(m, "z", 2.5)
    assert len(holes) == 2
    assert all(h["diameter"] == pytest.approx(3.4, abs=0.02) for h in holes)


def test_overhang_detects_t_shape(tmp_path):
    stem = cq.Workplane().box(5, 5, 20, centered=(True, True, False))
    top = cq.Workplane().workplane(offset=20).box(30, 5, 3, centered=(True, True, False))
    m = trimesh.load(stl(tmp_path, stem.union(top)))
    oh = overhang(m)
    assert oh["area_mm2"] == pytest.approx(30 * 5 - 5 * 5, rel=0.02)
    upside = analyze_part(stl(tmp_path, stem.union(top), "t.stl"), "t")["orientations"][0]
    assert upside["overhang_mm2"] < 1  # flipping the T removes the overhang


def _build_box(tmp_path, src=BOX_TWO_PARTS):
    script = tmp_path / "model.py"
    script.write_text(src)
    r = build_python(script, tmp_path / "out")
    assert r.ok, r.error
    return r


def test_acceptance_balanced_vs_precision(tmp_path, printer):
    r = _build_box(tmp_path)
    checks = [
        AcceptanceCheck(
            id="base_size",
            description="base 40x40x20",
            kind="bbox",
            part="base",
            params={"x": 40, "y": 40, "z": 20, "tol": 0.1},
        ),
        AcceptanceCheck(
            id="wrong", description="impossible size", kind="bbox", part="base", params={"x": 50, "tol": 0.1}
        ),
        AcceptanceCheck(
            id="clear",
            description="lip clearance",
            kind="parameter",
            params={"name": "lip_clearance", "value": 0.2, "tol": 0.01},
        ),
        AcceptanceCheck(id="noint", description="no overlap", kind="no_interference"),
    ]
    parts = [Part(name="base"), Part(name="lid")]
    for mode, blocked in (("balanced", False), ("precision", True)):
        spec = Spec(
            mode=mode,
            parts=parts,
            acceptance=checks,
            dimensions=[Dimension(name="wall", value=2.0, tolerance=0.05, critical=True)],
        )
        rep = evaluate(spec, tmp_path / "out", r.manifest, printer, thickness_samples=800)
        by = {c["id"]: c for c in rep["checks"]}
        assert by["base_size"]["status"] == "pass"
        assert by["wrong"]["status"] == "fail"
        assert by["clear"]["status"] == "pass"
        assert by["noint"]["status"] == "pass"
        assert by["param:wall"]["status"] == "pass"
        assert rep["ready_to_slice"] is (not blocked), mode
        json.dumps(rep, default=float)


def test_interference_blocks_in_precision(tmp_path, printer):
    r = _build_box(tmp_path, BOX_TWO_PARTS.replace('"lip_clearance": 0.2', '"lip_clearance": -0.5'))
    spec = Spec(mode="precision", parts=[Part(name="base"), Part(name="lid")])
    rep = evaluate(spec, tmp_path / "out", r.manifest, printer, thickness_samples=500)
    assert "interference:base-lid" in rep["blocking"]
    spec.mode = "creative"
    rep = evaluate(spec, tmp_path / "out", r.manifest, printer, thickness_samples=500)
    assert rep["ready_to_slice"]


def test_too_big_for_printer_blocks(tmp_path, printer):
    r = _build_box(tmp_path, BOX_TWO_PARTS.replace('"outer": 40.0', '"outer": 300.0'))
    spec = Spec(mode="creative", parts=[Part(name="base"), Part(name="lid")])
    rep = evaluate(spec, tmp_path / "out", r.manifest, printer, thickness_samples=300)
    assert "fit_printer:base" in rep["blocking"]


def test_multi_material_must_fit_both_nozzles(tmp_path, printer):
    r = _build_box(tmp_path, BOX_TWO_PARTS.replace('"outer": 40.0', '"outer": 245.0'))
    spec = Spec(
        mode="creative",
        parts=[Part(name="base", color="#ff0000"), Part(name="lid", color="#0000ff")],
        print=PrintPlan(layout="assembled"),
    )
    rep = evaluate(spec, tmp_path / "out", r.manifest, printer, thickness_samples=300)
    assert "fit_assembled" in rep["blocking"]  # 245 mm is wider than the 235.5 mm both nozzles reach
