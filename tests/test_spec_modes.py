from printwright.modes import PROFILES, process_preset_name, profile
from printwright.spec import AcceptanceCheck, Dimension, Fit, Hardware, Part, Spec


def severities(spec: Spec) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for i in spec.lint():
        out.setdefault(i["severity"], []).append(i["message"])
    return out


def test_default_mode_is_balanced():
    assert Spec().mode == "balanced"
    assert profile(None).name == "balanced"


def test_precision_requires_tolerances_fits_and_hardware_dims():
    s = Spec(
        mode="precision",
        parts=[Part(name="base"), Part(name="lid")],
        dimensions=[Dimension(name="bore", value=8.0, critical=True)],
        fits=[Fit(name="lid_fit", parts=["base", "lid"])],
        hardware=[Hardware(name="608ZZ bearing")],
    )
    errs = " ".join(severities(s)["error"])
    assert "no tolerance" in errs
    assert "no clearance" in errs
    assert "no nominal size" in errs
    assert "no key dimensions" in errs
    assert not s.can_freeze()


def test_precision_spec_complete_can_freeze():
    s = Spec(
        mode="precision",
        parts=[Part(name="base"), Part(name="lid")],
        dimensions=[Dimension(name="bore", value=8.0, tolerance=0.05, critical=True)],
        fits=[Fit(name="lid_fit", parts=["base", "lid"], nominal=36.0, clearance=0.2)],
        acceptance=[AcceptanceCheck(id="bore", description="bore Ø8", kind="hole", params={"diameter": 8.0})],
    )
    assert s.can_freeze(), s.lint()


def test_open_questions_block_only_precision():
    for mode, blocks in (("creative", False), ("balanced", False), ("precision", True)):
        s = Spec(
            mode=mode,
            open_questions=["which screw?"],
            dimensions=[Dimension(name="height", value=60, tolerance=1, critical=True)],
        )
        assert (not s.can_freeze()) == blocks, mode


def test_creative_wants_overall_size():
    s = Spec(mode="creative")
    assert any("overall size" in m for m in severities(s).get("warning", []))
    s.dimensions.append(Dimension(name="height", value=60))
    assert not any("overall size" in m for m in severities(s).get("warning", []))


def test_names_are_slugified():
    assert Part(name="Main Body").name == "main_body"
    assert Dimension(name="Wall Thickness", value=2).name == "wall_thickness"


def test_digest_ignores_status():
    a = Spec(title="x")
    b = a.model_copy(update={"status": "frozen"})
    assert a.digest() == b.digest()
    c = a.model_copy(update={"title": "y"})
    assert a.digest() != c.digest()


def test_mode_profiles_are_ordered_by_strictness():
    c, b, p = PROFILES["creative"], PROFILES["balanced"], PROFILES["precision"]
    assert c.min_wall_error_mm <= b.min_wall_error_mm <= p.min_wall_error_mm
    assert p.block_on_acceptance_fail and not b.block_on_acceptance_fail and not c.block_on_acceptance_fail
    assert p.backends == ("cadquery",)
    assert c.backends[0] == "organic"
    assert p.slice.wall_loops > b.slice.wall_loops


def test_process_preset_names():
    names = process_preset_name("Bambu Lab X2D", 0.4, PROFILES["precision"].slice)
    assert names[0] == "0.16mm High Quality @BBL X2D"
    names = process_preset_name("Bambu Lab X2D", 0.6, PROFILES["balanced"].slice)
    assert names[0] == "0.20mm Standard @BBL X2D 0.6 nozzle"


def test_markdown_contains_mode_and_parts():
    s = Spec(title="Bracket", mode="precision", parts=[Part(name="arm", filament="Bambu PETG HF")])
    md = s.to_markdown()
    assert "Precision" in md and "arm" in md and "Bambu PETG HF" in md
