from __future__ import annotations

from pathlib import Path

import pytest

from printwright.bambu.profiles import ProfileIndex, printer_info
from printwright.config import Settings, find_bambu_studio, find_blender

FIXTURES = Path(__file__).parent / "fixtures"

BOX_TWO_PARTS = """import cadquery as cq

PARAMS = {"outer": 40.0, "height": 20.0, "wall": 2.0, "lid_t": 3.0, "lip_clearance": 0.2}
PRINT_POSE = {"lid": {"rotate": [180, 0, 0]}}


def build(p):
    o, h, w = p["outer"], p["height"], p["wall"]
    base = cq.Workplane().box(o, o, h, centered=(True, True, False)).faces(">Z").shell(-w)
    lip = o - 2 * w - 2 * p["lip_clearance"]
    lid = (cq.Workplane().workplane(offset=h).box(o, o, p["lid_t"], centered=(True, True, False))
           .faces("<Z").workplane().rect(lip, lip).extrude(3))
    return {"base": base, "lid": lid}
"""


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """Every test gets its own PRINTWRIGHT_HOME and a fast, offline configuration."""
    monkeypatch.setenv("PRINTWRIGHT_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("PRINTWRIGHT_PROVIDER", raising=False)
    return tmp_path / "home"


@pytest.fixture
def idx() -> ProfileIndex:
    i = ProfileIndex()
    i.add_tree(FIXTURES / "profiles" / "BBL", "bundled")
    return i


@pytest.fixture
def printer(idx):
    return printer_info(idx, "Bambu Lab X2D 0.4 nozzle")


@pytest.fixture
def fast_settings() -> Settings:
    return Settings(provider="fake", renderer="matplotlib", vision_review=True, max_repair_attempts=2)


requires_bambu = pytest.mark.skipif(not find_bambu_studio(Settings()), reason="Bambu Studio not installed")
requires_blender = pytest.mark.skipif(not find_blender(Settings()), reason="Blender not installed")
