"""Every bundled example must build and pass all of its own acceptance checks."""

import json

import pytest

from printwright import pipeline as pl


@pytest.mark.parametrize("name", [e["name"] for e in pl.list_examples()])
def test_example_passes_its_spec(name, fast_settings, idx):
    ctx = pl.Context(fast_settings.model_copy(update={"vision_review": False}))
    ctx._idx = idx
    p = pl.create_from_example(name, ctx)
    info = p.iteration(1)
    rep = json.loads((p.iteration_dir(1) / "report.json").read_text())
    failing = [
        (c["id"], c["measured"], c["expected"], c["message"]) for c in rep["checks"] if c["status"] == "fail"
    ]
    assert not failing, failing
    assert info["ready_to_slice"]
    if p.spec.print.layout == "assembled":  # placement is judged on the whole object, not on each inlay
        ids = {c["id"] for c in rep["checks"]}
        assert not any(i.startswith(("contact:", "on_bed:")) and not i.endswith(":assembly") for i in ids), (
            ids
        )
    assert p.spec.status == "frozen"


def test_examples_are_listed():
    names = {e["name"]: e["mode"] for e in pl.list_examples()}
    assert names == {"cable-clip": "balanced", "cat-figurine": "creative", "bearing-block": "precision"}
