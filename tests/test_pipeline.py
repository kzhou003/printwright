"""The whole loop offline: fake LLM, matplotlib renders, fixture presets (no Bambu Studio needed)."""

import json

import pytest

from printwright import pipeline as pl
from printwright.llm.fake import BOX, FakeProvider
from printwright.project import Project
from printwright.spec import PrintPlan


@pytest.fixture
def ctx(fast_settings, idx):
    c = pl.Context(fast_settings)
    c._idx = idx
    return c


@pytest.fixture
def project(ctx):
    FakeProvider.queue, FakeProvider.calls = [], []
    p = Project.create("Test box")
    pl.spec_turn(p, "a box", ctx, FakeProvider())
    spec = p.spec
    spec.print = PrintPlan(printer="Bambu Lab X2D")
    p.spec = spec
    p.save()
    return p


def test_generate_requires_frozen_spec(project, ctx):
    with pytest.raises(pl.PipelineError, match="Freeze"):
        pl.generate(project, ctx=ctx, provider=FakeProvider())


def test_full_loop(project, ctx):
    pl.freeze(project)
    out = pl.generate(project, ctx=ctx, provider=FakeProvider())
    assert out["ok"] and out["ready_to_slice"]
    assert out["counts"]["fail"] == 0
    assert out["verdict"] == "pass"
    d = project.iteration_dir(1)
    for f in (
        "model.py",
        "prompt.md",
        "report.json",
        "review.json",
        "build/parts/body.stl",
        "build/parts/body.step",
        "renders/iso.png",
        "renders/sheet.png",
    ):
        assert (d / f).exists(), f
    rep = json.loads((d / "report.json").read_text())
    assert any(c["id"] == "drain" and c["status"] == "pass" for c in rep["checks"])
    # the review saw the render sheet
    assert FakeProvider.calls[-1]["images"][0].endswith("sheet.png")


def test_build_error_triggers_repair(project, ctx):
    pl.freeze(project)
    broken = "```python\nimport cadquery as cq\ndef build(p):\n    return {'body': cq.Workplane().box(1,1,1).fillet(9)}\n```"
    FakeProvider.queue = [broken, f"```python\n{BOX}```"]
    out = pl.generate(project, ctx=ctx, provider=FakeProvider())
    assert [a["stage"] for a in out["attempts"]] == ["run", "checked"]
    assert "failed when run" in FakeProvider.calls[2]["last"]  # calls: spec, attempt 1, attempt 2, review


def test_wrong_part_names_are_rejected(project, ctx):
    pl.freeze(project)
    wrong = BOX.replace('{"body": body}', '{"shell": body}')
    FakeProvider.queue = [f"```python\n{wrong}```", f"```python\n{BOX}```"]
    out = pl.generate(project, ctx=ctx, provider=FakeProvider())
    assert out["attempts"][0]["stage"] == "parts"


def test_mode_switch_unfreezes_and_changes_backend(project, ctx):
    pl.freeze(project)
    assert pl.choose_backend(project, ctx.settings) == "cadquery"
    spec = pl.set_mode(project, "creative")
    assert spec.status == "draft"
    assert pl.choose_backend(project, ctx.settings) == "organic"
    pl.set_mode(project, "precision")
    assert pl.choose_backend(project, ctx.settings) == "cadquery"


def test_llm_cannot_change_mode(project, ctx):
    turn = {"reply": "ok", "spec": {**project.spec.model_dump(), "mode": "precision"}}
    FakeProvider.queue = [json.dumps(turn)]
    r = pl.spec_turn(project, "make it", ctx, FakeProvider())
    assert r["spec"]["mode"] == "balanced"
    assert r["spec"]["mode_recommendation"]["mode"] == "precision"


def test_manual_edit_and_rebuild(project, ctx):
    pl.freeze(project)
    pl.generate(project, ctx=ctx, provider=FakeProvider())
    script = project.iteration_dir(1) / "model.py"
    script.write_text(script.read_text().replace('"wall": 2.0', '"wall": 0.3'))
    info = pl.rebuild(project, 1, ctx)
    assert info["source"] == "manual"
    assert not info["ready_to_slice"]  # 0.3 mm walls are below the hard minimum
    with pytest.raises(pl.PipelineError, match="blocking"):
        pl.slice_iteration(project, 1, ctx)
