"""Deterministic offline provider for tests and demos (provider name "fake").

Queue replies with FakeProvider.queue; when the queue is empty it answers spec requests with a small spec
and code requests with a parametric box, so the whole pipeline can run without any account.
"""

from __future__ import annotations

import json
from pathlib import Path

from printwright.llm.base import LLMResult, Msg, Provider

BOX = """import cadquery as cq

PARAMS = {"length": 60.0, "width": 40.0, "height": 20.0, "wall": 2.0, "hole_d": 5.2}
PRINT_POSE = {}


def build(p):
    body = (cq.Workplane("XY").box(p["length"], p["width"], p["height"], centered=(True, True, False))
            .faces(">Z").shell(-p["wall"])
            .faces("<Z").workplane().hole(p["hole_d"]))
    return {"body": body}
"""


class FakeProvider(Provider):
    name = "fake"
    queue: list[str] = []
    calls: list[dict] = []

    def complete(
        self, system: str, messages: list[Msg], images: list[Path] | None = None, schema: dict | None = None
    ) -> LLMResult:
        FakeProvider.calls.append(
            {
                "system": system[:200],
                "last": messages[-1].content[:300],
                "images": [str(p) for p in images or []],
                "schema": bool(schema),
            }
        )
        if FakeProvider.queue:
            return LLMResult(text=FakeProvider.queue.pop(0), model="fake", cost_usd=0.0)
        if schema and "verdict" in json.dumps(schema):
            return LLMResult(
                text=json.dumps({"verdict": "pass", "summary": "Looks like the spec.", "issues": []}),
                model="fake",
                cost_usd=0.0,
            )
        if schema:
            spec = {
                "reply": "Here is a first spec for a small open box with a drain hole.",
                "spec": {
                    "title": "Test box",
                    "summary": "Open box",
                    "mode": "balanced",
                    "parts": [{"name": "body", "filament": "Bambu PLA Basic", "color": "#4C8BF5"}],
                    "dimensions": [
                        {"name": "length", "value": 60},
                        {"name": "width", "value": 40},
                        {"name": "height", "value": 20},
                        {"name": "wall", "value": 2},
                        {"name": "hole_d", "value": 5.2, "tolerance": 0.15, "critical": True},
                    ],
                    "acceptance": [
                        {
                            "id": "size",
                            "description": "outer size 60x40x20",
                            "kind": "bbox",
                            "params": {"x": 60, "y": 40, "z": 20, "tol": 0.2},
                        },
                        {
                            "id": "drain",
                            "description": "one Ø5.2 drain hole",
                            "kind": "hole",
                            "params": {"axis": "z", "diameter": 5.2, "tol": 0.1, "count": 1},
                        },
                    ],
                },
            }
            return LLMResult(text=json.dumps(spec), model="fake", cost_usd=0.0)
        return LLMResult(text=f"```python\n{BOX}```", model="fake", cost_usd=0.0)
