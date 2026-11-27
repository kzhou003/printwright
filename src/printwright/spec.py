"""The design spec: the single agreed description of what to build.

The spec is written together with the LLM in the requirements chat, edited by the user, then *frozen*.
CAD generation only starts from a frozen spec, and every iteration records which spec it was built from.
The spec is also the test oracle: its acceptance checks are evaluated against the generated geometry by
code (see analysis/acceptance.py), not by the model that wrote the CAD.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from printwright.modes import DEFAULT_MODE, Mode, profile

CheckKind = Literal[
    "bbox",
    "volume",
    "watertight",
    "single_body",
    "min_wall",
    "max_overhang",
    "hole",
    "parameter",
    "no_interference",
]

SLUG = re.compile(r"[^a-z0-9_]+")


def slugify(s: str) -> str:
    return SLUG.sub("_", s.strip().lower()).strip("_") or "part"


class Dimension(BaseModel):
    name: str = Field(description="snake_case; the CAD script's PARAMS uses the same key")
    value: float
    unit: Literal["mm", "deg"] = "mm"
    tolerance: float | None = Field(
        None, description="± tolerance in the same unit; required for critical dims in precision mode"
    )
    critical: bool = False
    note: str = ""

    @field_validator("name")
    @classmethod
    def _slug(cls, v: str) -> str:
        return slugify(v)


class Part(BaseModel):
    name: str = Field(description="snake_case; used for file names and as the key returned by build()")
    description: str = ""
    filament: str = Field("Bambu PLA Basic", description="Bambu Studio filament family, e.g. 'Bambu PETG HF'")
    color: str = Field("#D9D9D9", description="hex colour used for renders and the filament slot")
    quantity: int = Field(1, ge=1)

    @field_validator("name")
    @classmethod
    def _slug(cls, v: str) -> str:
        return slugify(v)


class Hardware(BaseModel):
    """Purchased parts the print must mate with."""

    name: str = Field(description="e.g. 'M3x10 socket head cap screw'")
    standard: str = Field("", description="e.g. 'ISO 4762', '608ZZ', 'MG90S'")
    quantity: int = 1
    key_dimensions: dict[str, float] = Field(
        default_factory=dict, description="mm, e.g. {'thread_d': 3, 'head_d': 5.5}"
    )
    note: str = ""


class Fit(BaseModel):
    name: str
    parts: list[str] = Field(
        default_factory=list, description="the two part names (or a part and a hardware item) that mate"
    )
    kind: Literal["clearance", "sliding", "running", "press", "snap", "thread", "insert"] = "clearance"
    nominal: float | None = Field(None, description="nominal mating size, mm")
    clearance: float | None = Field(None, description="per-side gap in mm (negative = interference)")
    note: str = ""


class PrintPlan(BaseModel):
    printer: str = "Bambu Lab X2D"
    nozzle: float = 0.4
    process: str | None = Field(None, description="Bambu Studio process preset; None = chosen from the mode")
    layout: Literal["separate", "assembled"] = Field(
        "separate", description="separate: parts arranged on the plate; assembled: one multi-material object"
    )
    bed_type: Literal[
        "Textured PEI Plate", "Cool Plate", "Engineering Plate", "High Temp Plate", "Supertack Plate"
    ] = Field(
        "Textured PEI Plate", description="build plate fitted to the printer; must suit every filament used"
    )
    supports: Literal["none", "auto", "tree"] | None = None
    infill_percent: int | None = Field(None, ge=0, le=100)
    wall_loops: int | None = Field(None, ge=1, le=20)
    brim: Literal["auto", "none", "outer"] | None = None
    orientation_note: str = ""


class AcceptanceCheck(BaseModel):
    id: str
    description: str
    kind: CheckKind
    part: str | None = Field(None, description="part name; None = every part / whole assembly")
    params: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "bbox: {x,y,z,tol} in print orientation; volume: {min_cm3,max_cm3}; min_wall: {mm}; "
            "max_overhang: {max_fraction}; hole: {axis:'z',at,diameter,tol,count}; "
            "parameter: {name,value,tol}; watertight/single_body/no_interference: {}"
        ),
    )


class ModeRecommendation(BaseModel):
    mode: Mode
    reason: str


class Spec(BaseModel):
    schema_version: int = 1
    mode: Mode = DEFAULT_MODE
    mode_recommendation: ModeRecommendation | None = None
    title: str = "Untitled"
    summary: str = ""
    use_case: str = ""
    environment: str = Field("", description="temperature, outdoor/UV, loads, food contact, ...")
    parts: list[Part] = Field(default_factory=lambda: [Part(name="body")])
    dimensions: list[Dimension] = Field(default_factory=list)
    features: list[str] = Field(default_factory=list)
    hardware: list[Hardware] = Field(default_factory=list)
    fits: list[Fit] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    style: str = Field("", description="look and feel; mostly for creative mode")
    print: PrintPlan = Field(default_factory=PrintPlan)
    acceptance: list[AcceptanceCheck] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    status: Literal["draft", "frozen"] = "draft"

    # ------------------------------------------------------------------ helpers
    def digest(self) -> str:
        body = self.model_dump(exclude={"status", "open_questions", "mode_recommendation"})
        return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()[:12]

    def part_names(self) -> list[str]:
        return [p.name for p in self.parts]

    def lint(self) -> list[dict]:
        """Problems that block freezing (severity 'error') or deserve attention ('warning')."""
        mp = profile(self.mode)
        out: list[dict] = []

        def add(sev: str, msg: str) -> None:
            out.append({"severity": sev, "message": msg})

        if not self.parts:
            add("error", "The spec needs at least one part.")
        names = self.part_names()
        if len(set(names)) != len(names):
            add("error", "Part names must be unique.")
        if self.open_questions:
            add(
                "warning" if self.mode != "precision" else "error",
                f"{len(self.open_questions)} open question(s) still unanswered.",
            )
        if not self.dimensions and self.mode != "creative":
            add("warning", "No dimensions are specified; the model will guess the size.")
        if self.mode == "creative" and not any(
            d.name in {"height", "overall_height", "length", "width", "size"} for d in self.dimensions
        ):
            add(
                "warning",
                "Give at least an overall size (e.g. height) so the figurine prints at the right scale.",
            )
        if mp.require_tolerances:
            for d in self.dimensions:
                if d.critical and d.tolerance is None:
                    add("error", f"Critical dimension '{d.name}' has no tolerance (precision mode).")
            if not any(d.critical for d in self.dimensions):
                add("error", "Precision mode needs at least one critical dimension.")
        if mp.require_fit_clearances:
            for f in self.fits:
                if f.clearance is None:
                    add("error", f"Fit '{f.name}' has no clearance.")
        if self.mode == "precision":
            if len(self.parts) > 1 and not self.fits:
                add("error", "Several parts but no fits: describe how they mate.")
            for f in self.fits:
                if f.nominal is None:
                    add("error", f"Fit '{f.name}' has no nominal size.")
            for h in self.hardware:
                if not h.key_dimensions:
                    add("error", f"Hardware '{h.name}' has no key dimensions.")
        if mp.require_acceptance_per_critical_dim:

            def covers(c: AcceptanceCheck, d: Dimension) -> bool:
                blob = f"{c.id} {c.description} {json.dumps(c.params, default=str)}"
                if d.name in blob:
                    return True
                nums = [v for v in c.params.values() if isinstance(v, int | float)]
                return c.kind in ("hole", "bbox") and any(abs(v - d.value) < 1e-6 for v in nums)

            for d in self.dimensions:
                if d.critical and not any(covers(c, d) for c in self.acceptance):
                    add("warning", f"No acceptance check covers critical dimension '{d.name}'.")
        for c in self.acceptance:
            if c.part and c.part not in names:
                add("error", f"Acceptance check '{c.id}' refers to unknown part '{c.part}'.")
        if self.mode_recommendation and self.mode_recommendation.mode != self.mode:
            add(
                "info", f"Suggested mode: {self.mode_recommendation.mode} - {self.mode_recommendation.reason}"
            )
        return out

    def can_freeze(self) -> bool:
        return not any(i["severity"] == "error" for i in self.lint())

    def to_markdown(self) -> str:
        mp = profile(self.mode)
        L = [f"# {self.title}", "", f"**Mode:** {mp.title} ({mp.title_zh}) - {mp.summary}", ""]
        if self.summary:
            L += [self.summary, ""]
        if self.use_case:
            L += [f"**Use case:** {self.use_case}", ""]
        if self.environment:
            L += [f"**Environment:** {self.environment}", ""]
        if self.style:
            L += [f"**Style:** {self.style}", ""]
        L += ["## Parts", "", "| part | filament | colour | qty | description |", "|---|---|---|---|---|"]
        L += [f"| {p.name} | {p.filament} | {p.color} | {p.quantity} | {p.description} |" for p in self.parts]
        if self.dimensions:
            L += [
                "",
                "## Dimensions",
                "",
                "| name | value | tol | critical | note |",
                "|---|---|---|---|---|",
            ]
            for d in self.dimensions:
                tol = f"±{d.tolerance:g}" if d.tolerance is not None else ""
                L.append(
                    f"| {d.name} | {d.value:g} {d.unit} | {tol} | {'yes' if d.critical else ''} | {d.note} |"
                )
        for title, items in (
            ("Features", self.features),
            ("Constraints", self.constraints),
            ("Assumptions", self.assumptions),
            ("Open questions", self.open_questions),
        ):
            if items:
                L += ["", f"## {title}", ""] + [f"- {x}" for x in items]
        if self.hardware:
            L += ["", "## Purchased hardware", ""]
            for h in self.hardware:
                dims = ", ".join(f"{k}={v:g}" for k, v in h.key_dimensions.items())
                L.append(f"- {h.quantity}x {h.name} {h.standard} ({dims}) {h.note}".rstrip())
        if self.fits:
            L += ["", "## Fits", ""]
            for f in self.fits:
                c = f"{f.clearance:+g} mm/side" if f.clearance is not None else "clearance ?"
                n = f"Ø/size {f.nominal:g}" if f.nominal is not None else ""
                L.append(f"- **{f.name}** ({f.kind}, {' / '.join(f.parts)}): {n} {c} {f.note}".rstrip())
        p = self.print
        L += ["", "## Print plan", "", f"- {p.printer}, {p.nozzle:g} mm nozzle, layout: {p.layout}"]
        if p.process:
            L.append(f"- process: {p.process}")
        if p.orientation_note:
            L.append(f"- orientation: {p.orientation_note}")
        if self.acceptance:
            L += ["", "## Acceptance checks", ""]
            for c in self.acceptance:
                L.append(f"- `{c.id}` ({c.kind}{', ' + c.part if c.part else ''}): {c.description}")
        return "\n".join(L) + "\n"


def default_spec(title: str, mode: Mode = DEFAULT_MODE) -> Spec:
    return Spec(title=title, mode=mode)


def spec_json_schema() -> dict:
    return Spec.model_json_schema()
