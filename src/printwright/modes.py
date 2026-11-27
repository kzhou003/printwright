"""Design modes: balanced (default), creative, precision.

A mode is one switch that sets everything downstream: which CAD backend the model uses, how strict the
spec must be before it can be frozen, which checks block slicing, the default slicer settings, and what the
LLM is told to care about. Keep all mode-dependent numbers here so they can be reviewed in one place.

* creative  - figurines, toys, decorative and organic shapes. Looks matter, millimetres do not. Organic
              SDF modelling, tree supports allowed, only "can it print" checks block.
* balanced  - everyday functional prints (holders, boxes, brackets). The platform picks sensible
              tolerances and settings and may suggest switching to creative or precision.
* precision - engineering parts that must assemble and work (robotics, mechanisms). Exact B-rep
              CadQuery only, every critical dimension toleranced, every fit given a clearance,
              interference checked in the assembled pose, and any failed check blocks slicing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Mode = Literal["balanced", "creative", "precision"]
MODES: tuple[Mode, ...] = ("balanced", "creative", "precision")
DEFAULT_MODE: Mode = "balanced"


@dataclass(frozen=True)
class SliceDefaults:
    layer_height: float  # mm; picks the process preset "<h>mm <quality> @BBL <printer>"
    quality: str  # "Standard" | "High Quality"
    wall_loops: int
    infill_percent: int
    infill_pattern: str
    supports: Literal["none", "auto", "tree"]
    brim: Literal["auto", "none", "outer"]


@dataclass(frozen=True)
class ModeProfile:
    name: Mode
    title: str
    title_zh: str
    summary: str
    summary_zh: str
    backends: tuple[str, ...]  # allowed CAD backends, first is preferred
    slice: SliceDefaults
    # spec gate: what must be present before the spec can be frozen
    require_tolerances: bool
    require_fit_clearances: bool
    require_acceptance_per_critical_dim: bool
    # build gate: which check severities block "ready to slice"
    block_on_acceptance_fail: bool
    interference_is_error: bool
    min_wall_error_mm: float  # thinner than this -> error
    min_wall_warn_mm: float  # thinner than this -> warning
    max_overhang_fraction_warn: float  # share of surface area needing support before warning
    llm_focus: str = ""
    review_focus: str = ""
    extra_rules: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "title": self.title,
            "title_zh": self.title_zh,
            "summary": self.summary,
            "summary_zh": self.summary_zh,
            "backends": list(self.backends),
            "slice": self.slice.__dict__,
            "gates": {
                "require_tolerances": self.require_tolerances,
                "require_fit_clearances": self.require_fit_clearances,
                "block_on_acceptance_fail": self.block_on_acceptance_fail,
                "interference_is_error": self.interference_is_error,
            },
        }


PROFILES: dict[Mode, ModeProfile] = {
    "creative": ModeProfile(
        name="creative",
        title="Creative",
        title_zh="创意",
        summary="Figurines, toys and decorative or organic shapes. Appearance first; dimensions are approximate.",
        summary_zh="玩偶、摆件、装饰和有机造型。外观优先，尺寸只需大致准确。",
        backends=("organic", "cadquery", "openscad"),
        slice=SliceDefaults(0.12, "High Quality", 2, 12, "gyroid", "tree", "auto"),
        require_tolerances=False,
        require_fit_clearances=False,
        require_acceptance_per_critical_dim=False,
        block_on_acceptance_fail=False,
        interference_is_error=False,
        min_wall_error_mm=0.4,
        min_wall_warn_mm=0.8,
        max_overhang_fraction_warn=0.35,
        llm_focus=(
            "Creative mode: the goal is a charming, recognisable, printable object. Favour smooth organic "
            "forms, good proportions, character and colour. Exact dimensions only matter for overall size "
            "and anything that must hold or stand (a stable base, a keyring hole). Ask about style, size, "
            "pose and colours rather than tolerances."
        ),
        review_focus=(
            "Judge likeness to the request, proportions, silhouette, appeal and whether it will stand and "
            "print. Ignore millimetre-level dimensional differences."
        ),
        extra_rules=(
            "Give figurines a flat base of at least 35% of their footprint, or a separate plinth, so they stand and stick to the bed.",
            "Thin protrusions (ears, tails, fingers) need >= 2 mm thickness at the root; merge them into the body with a smooth blend.",
            "Multi-colour: model each colour as its own part in the same coordinates (layout 'assembled') and mesh them with mesh_parts() so they touch but never overlap. The printer has two nozzles; more than two colours needs an AMS and adds purge waste, so keep to two unless colour really matters.",
        ),
    ),
    "balanced": ModeProfile(
        name="balanced",
        title="Balanced",
        title_zh="均衡",
        summary="Everyday functional prints. The platform picks tolerances and settings and suggests another mode when it fits better.",
        summary_zh="日常功能件。由平台选择公差和打印参数；更适合其他模式时会给出建议。",
        backends=("cadquery", "organic", "openscad"),
        slice=SliceDefaults(0.20, "Standard", 3, 15, "gyroid", "none", "auto"),
        require_tolerances=False,
        require_fit_clearances=True,
        require_acceptance_per_critical_dim=False,
        block_on_acceptance_fail=False,
        interference_is_error=True,
        min_wall_error_mm=0.6,
        min_wall_warn_mm=1.2,
        max_overhang_fraction_warn=0.15,
        llm_focus=(
            "Balanced mode: you choose sensible defaults yourself (tolerances, clearances, wall thickness, "
            "print settings) and record them as assumptions instead of asking about every detail. If the "
            "object is clearly decorative/organic, recommend 'creative'; if it is a mechanism, robotics part "
            "or must mate with purchased hardware, recommend 'precision'. Set mode_recommendation accordingly."
        ),
        review_focus="Check that every feature in the spec is present, functional and printable.",
    ),
    "precision": ModeProfile(
        name="precision",
        title="Precision",
        title_zh="精密",
        summary="Engineering parts that must assemble and run. Every fit specified, interference-checked, nothing ships with a failed check.",
        summary_zh="必须能装配、能运转的工程件（机器人、机构）。每处配合都写明间隙并做干涉检查，任何检查未通过都不能切片。",
        backends=("cadquery",),
        slice=SliceDefaults(0.16, "High Quality", 4, 35, "gyroid", "none", "auto"),
        require_tolerances=True,
        require_fit_clearances=True,
        require_acceptance_per_critical_dim=True,
        block_on_acceptance_fail=True,
        interference_is_error=True,
        min_wall_error_mm=0.8,
        min_wall_warn_mm=1.6,
        max_overhang_fraction_warn=0.05,
        llm_focus=(
            "Precision mode: these parts must assemble and work first time. Nothing may be left vague. "
            "Every critical dimension needs a value and a tolerance; every mating interface needs a fit "
            "type and a per-side clearance; every purchased component (screws, nuts, heat-set inserts, "
            "bearings, shafts, servos, motors, magnets) must be listed with its standard and exact key "
            "dimensions. Ask about load direction, mating hardware and assembly order. Add measurable "
            "acceptance checks for every critical dimension (bbox, hole, parameter checks) and plan the "
            "print orientation for strength (layer lines perpendicular to the main load) and accuracy "
            "(round holes vertical where possible)."
        ),
        review_focus=(
            "Be strict. Verify every critical dimension, hole, fit and hardware interface against the spec, "
            "that parts can be assembled in the stated order, that fasteners have tool access, and that the "
            "print orientation gives strength along the load path. Any doubt is an issue."
        ),
        extra_rules=(
            "Model the assembly: build() returns every part in its ASSEMBLED position so interference can be checked; print orientation goes in PRINT_POSE.",
            "Vertical holes print ~0.1-0.2 mm undersize on FDM; design holes at nominal + compensation and record the compensation as a named PARAMS entry.",
            "Horizontal holes > 8 mm: use a teardrop or flat-top profile so they print without support.",
            "Screw clearance holes (ISO 273 medium): M2 2.4, M2.5 2.9, M3 3.4, M4 4.5, M5 5.5 mm. Heat-set inserts: follow the insert maker's hole diameter and depth exactly; put them in PARAMS.",
            "Bearings and shafts: press fit -0.05 to -0.1 mm on diameter for printed bores is unreliable; prefer a 0.1 mm clearance bore plus retaining feature, or say why a press fit is acceptable.",
            "Recommend a small fit-test coupon (the critical hole/peg/slot at the chosen clearances) as its own part when a fit is tighter than 0.2 mm per side.",
        ),
    ),
}


def profile(mode: str | None) -> ModeProfile:
    return PROFILES.get(mode or DEFAULT_MODE, PROFILES[DEFAULT_MODE])  # type: ignore[arg-type]


def process_preset_name(printer_model: str, nozzle: float, sd: SliceDefaults) -> list[str]:
    """Candidate process preset names, most specific first. Bambu names 0.4 mm presets without a suffix."""
    short = printer_model.replace("Bambu Lab ", "")
    suffix = "" if abs(nozzle - 0.4) < 1e-6 else f" {nozzle:g} nozzle"
    h = f"{sd.layer_height:.2f}"
    return [
        f"{h}mm {sd.quality} @BBL {short}{suffix}",
        f"{h}mm Standard @BBL {short}{suffix}",
        f"0.20mm Standard @BBL {short}{suffix}",
    ]
