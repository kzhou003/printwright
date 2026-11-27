import json

import pytest

from printwright.config import Settings
from printwright.llm import LLMError, Msg, complete_model, extract_code, extract_json
from printwright.llm.claude_code import ClaudeCodeProvider, transcript
from printwright.llm.fake import FakeProvider
from printwright.llm.prompts import Review, SpecTurn, codegen_system, spec_system
from printwright.spec import Spec


def test_extract_code_prefers_python_and_longest():
    text = "notes\n```json\n{}\n```\n```python\nshort = 1\n```\n```python\nimport cadquery as cq\nx = 2\n```"
    assert extract_code(text).startswith("import cadquery")
    assert extract_code("no code") is None


def test_extract_json_bare_and_fenced():
    assert extract_json('blah {"a": {"b": "}"}} tail') == {"a": {"b": "}"}}
    assert extract_json('```json\n{"x": 1}\n```') == {"x": 1}
    assert extract_json("nothing") is None


def test_complete_model_repairs_invalid_json():
    FakeProvider.queue = [
        '{"verdict": "pass"}',
        json.dumps({"verdict": "revise", "summary": "s", "issues": []}),
    ]
    review, _ = complete_model(FakeProvider(), "sys", [Msg("user", "x")], Review)
    assert review.verdict == "revise"
    assert "invalid" in FakeProvider.calls[-1]["last"]


def test_complete_model_gives_up():
    FakeProvider.queue = ["nope", "still nope"]
    with pytest.raises(LLMError):
        complete_model(FakeProvider(), "sys", [Msg("user", "x")], Review, retries=1)


def test_spec_turn_schema_roundtrip():
    FakeProvider.queue = []
    turn, _ = complete_model(FakeProvider(), "sys", [Msg("user", "box")], SpecTurn)
    assert turn.spec.parts[0].name == "body"


def test_claude_code_command_is_locked_down(monkeypatch, tmp_path):
    fake = tmp_path / "claude"
    fake.write_text("#!/bin/sh\n")
    fake.chmod(0o755)
    p = ClaudeCodeProvider(Settings(claude_code_bin=str(fake), claude_code_model="sonnet"))
    cmd = p.command("SYS", {"type": "object"}, with_images=False, img_dir=None)
    assert "--strict-mcp-config" in cmd and "--no-session-persistence" in cmd
    assert cmd[cmd.index("--tools") + 1] == ""
    assert cmd[cmd.index("--setting-sources") + 1] == ""
    assert cmd[cmd.index("--model") + 1] == "sonnet"
    cmd = p.command("SYS", None, with_images=True, img_dir="/tmp/x")
    assert cmd[cmd.index("--tools") + 1] == "Read" and "--add-dir" in cmd


def test_transcript():
    assert transcript([Msg("user", "hi")]) == "hi"
    t = transcript([Msg("user", "a"), Msg("assistant", "b"), Msg("user", "c")])
    assert "<assistant>" in t and t.endswith("c")


def test_prompts_carry_mode_and_printer():
    s = spec_system("precision", "Bambu Lab X2D: 256 x 256 x 261", ["Bambu PLA Basic"])
    assert "Precision mode" in s and "256 x 256 x 261" in s
    c = codegen_system(Spec(mode="creative"), "organic", "printer")
    assert "printwright.cad.organic" in c and "Creative mode" in c
