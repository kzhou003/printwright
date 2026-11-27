import io
import json
import zipfile

import pytest

from printwright.bambu.profiles import ProfileError, ProfileIndex, machine_for
from printwright.bambu.slicer import _write_presets, plan, read_3mf
from printwright.spec import Part, PrintPlan, Spec

from .conftest import requires_bambu


def test_flatten_resolves_inheritance(idx):
    m = idx.flatten("machine", "Bambu Lab X2D 0.4 nozzle")
    assert m["printable_height"] == "261"  # child wins
    assert m["printable_area"][1] == "256x0"  # inherited
    assert "inherits" not in m


def test_printer_info_dual_nozzle(printer):
    assert printer.size == (256, 256, 261)
    assert [e.kind for e in printer.extruders] == ["Direct Drive", "Bowden"]
    assert printer.extruders[1].size == pytest.approx((235.5, 256, 256))
    assert "both nozzles" in printer.summary()


def test_compatible_and_filament_resolution(idx):
    m = machine_for(idx, "Bambu Lab X2D", 0.4)
    assert "0.16mm High Quality @BBL X2D" in idx.compatible("process", m)
    assert idx.resolve_filament("Bambu PLA Basic", m) == "Bambu PLA Basic @BBL X2D 0.4 nozzle"
    assert idx.filament_families(m) == ["Bambu PETG HF", "Bambu PLA Basic"]
    with pytest.raises(ProfileError, match="No filament preset"):
        idx.resolve_filament("Unobtainium", m)


def test_unknown_nozzle(idx):
    with pytest.raises(ProfileError):
        machine_for(idx, "Bambu Lab X2D", 0.6)


def _spec(mode="balanced", **kw):
    pp = PrintPlan(printer="Bambu Lab X2D", **kw)
    return Spec(
        mode=mode, parts=[Part(name="a"), Part(name="b", filament="Bambu PETG HF", color="#000000")], print=pp
    )


def test_plan_uses_mode_defaults(idx, tmp_path):
    files = {"a": tmp_path / "a.stl", "b": tmp_path / "b.stl"}
    p = plan(_spec("precision"), files, idx)
    assert p.process == "0.16mm High Quality @BBL X2D"
    assert p.overrides["wall_loops"] == "4" and p.overrides["sparse_infill_density"] == "35%"
    assert p.overrides["curr_bed_type"] == "Textured PEI Plate"
    assert p.part_slots == [1, 2] and len(p.filaments) == 2
    p = plan(_spec("creative"), files, idx)
    assert p.process == "0.12mm High Quality @BBL X2D"
    assert p.overrides["enable_support"] == "1" and p.overrides["support_type"] == "tree(auto)"


def test_same_filament_and_colour_share_a_slot(idx, tmp_path):
    spec = Spec(parts=[Part(name="a"), Part(name="b")], print=PrintPlan(printer="Bambu Lab X2D"))
    p = plan(spec, {"a": tmp_path / "a.stl", "b": tmp_path / "b.stl"}, idx)
    assert p.part_slots == [1, 1] and len(p.filaments) == 1


def test_quantity_duplicates_separate_parts(idx, tmp_path):
    spec = Spec(parts=[Part(name="a", quantity=3)], print=PrintPlan(printer="Bambu Lab X2D"))
    p = plan(spec, {"a": tmp_path / "a.stl"}, idx)
    assert len(p.part_files) == 3


def test_bed_type_incompatibility_is_explained(idx, tmp_path):
    with pytest.raises(ProfileError, match="Cool Plate"):
        plan(_spec(bed_type="Cool Plate"), {"a": tmp_path / "a.stl", "b": tmp_path / "b.stl"}, idx)


def test_written_presets_are_flat_and_have_extruder_variants(idx, tmp_path):
    p = plan(_spec(), {"a": tmp_path / "a.stl", "b": tmp_path / "b.stl"}, idx)
    m, pr, fs = _write_presets(p, idx, tmp_path)
    f = json.loads(fs[1].read_text())
    assert "inherits" not in f
    assert f["filament_extruder_variant"] == ["Direct Drive Standard", "Bowden Standard"]
    assert f["filament_colour"] == ["#000000"]
    assert json.loads(pr.read_text())["wall_loops"] == "3"


def test_read_3mf(tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(
            "Metadata/slice_info.config",
            """<config><plate>
            <metadata key="index" value="1"/><metadata key="prediction" value="3600"/>
            <metadata key="weight" value="12.5"/><metadata key="support_used" value="false"/>
            <filament id="1" type="PLA" color="#FF0000" used_m="4.1" used_g="12.5"/></plate></config>""",
        )
        z.writestr("Metadata/project_settings.config", json.dumps({"printer_model": "Bambu Lab X2D"}))
        z.writestr("Metadata/plate_1.png", b"png")
    f = tmp_path / "x.gcode.3mf"
    f.write_bytes(buf.getvalue())
    info = read_3mf(f, tmp_path)
    pl = info["plates_info"][0]
    assert pl["print_seconds"] == 3600 and pl["weight_g"] == 12.5 and pl["filaments"][0]["type"] == "PLA"
    assert info["printer_model"] == "Bambu Lab X2D" and (tmp_path / "plate_1.png").exists()


@requires_bambu
@pytest.mark.integration
def test_real_x2d_slice(tmp_path):
    """End to end with the installed Bambu Studio and its real X2D presets."""
    import cadquery as cq

    from printwright.bambu.slicer import slice_project

    idx = ProfileIndex.discover()
    try:
        machine_for(idx, "Bambu Lab X2D", 0.4)
    except ProfileError:
        pytest.skip("installed Bambu Studio has no X2D presets")
    a, b = tmp_path / "a.stl", tmp_path / "b.stl"
    cq.exporters.export(cq.Workplane().box(30, 30, 4, centered=(True, True, False)), str(a))
    cq.exporters.export(
        cq.Workplane().workplane(offset=4).box(10, 10, 2, centered=(True, True, False)), str(b)
    )
    spec = Spec(
        parts=[Part(name="a", color="#FFFFFF"), Part(name="b", filament="Bambu PETG HF", color="#000000")],
        print=PrintPlan(layout="assembled"),
    )
    p = plan(spec, {"a": a, "b": b}, idx)
    rep = slice_project(p, idx, tmp_path / "out", "t")
    assert rep["ok"], rep.get("error")
    assert rep["printer_model"] == "Bambu Lab X2D"
    assert {f["type"] for f in rep["plates_info"][0]["filaments"]} == {"PLA", "PETG"}
    assert (tmp_path / "out" / "t.3mf").exists()
