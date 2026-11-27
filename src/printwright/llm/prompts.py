"""All prompts in one place. Printer facts are injected from Bambu Studio's presets, never hard-coded."""

from __future__ import annotations

import json

from pydantic import BaseModel, Field

from printwright.modes import PROFILES, profile
from printwright.spec import Spec

# --------------------------------------------------------------------------- structured replies


class SpecTurn(BaseModel):
    """Reply in the requirements chat."""

    reply: str = Field(description="what you say to the user: short, concrete, in the user's language")
    spec: Spec = Field(description="the COMPLETE updated spec (not a diff)")


class ReviewIssue(BaseModel):
    severity: str = Field(description="critical | major | minor")
    title: str
    detail: str
    suggestion: str = ""


class Review(BaseModel):
    verdict: str = Field(description="pass | revise")
    summary: str
    issues: list[ReviewIssue] = Field(default_factory=list)


# --------------------------------------------------------------------------- shared knowledge

FDM_RULES = """FDM design rules (0.4 mm nozzle unless stated; scale with nozzle size):
- Units are millimetres. Z is up. The part's print orientation is decided deliberately (see PRINT_POSE).
- Walls: at least 2 perimeters (>= 0.9 mm); 1.2-2.0 mm for normal walls, 2.4+ mm where loaded or threaded.
- Overhangs up to 45 degrees from vertical print without support; bridges up to ~15 mm. Avoid needing
  support under functional surfaces.
- First layer: bottom edges get a 0.4-0.6 mm 45 degree chamfer (not a fillet) against elephant foot; top
  edges may be filleted. A larger flat base improves adhesion.
- Holes: vertical holes print ~0.1-0.2 mm undersize; horizontal holes become slightly oval. Teardrop or
  flat-top profiles for horizontal holes larger than ~8 mm.
- Fits (per side): press 0.0-0.05, snug 0.1, sliding 0.15-0.2, free/running 0.25-0.35, loose 0.4+.
  Parts printed separately and assembled need these gaps designed in.
- Text/emboss: >= 0.6 mm tall, strokes >= 0.8 mm wide. Engrave >= 0.4 mm deep.
- Snap fits and living hinges: PETG/PP/TPU, not PLA; size cantilevers for <= 2-3 % strain.
- Strength is weakest between layers: orient so the main load runs along the layer lines, not across them.
- Heat: PLA softens ~55-60 C (no cars in summer, no dishwasher); PETG ~70 C; ABS/ASA ~90-100 C and UV-stable (ASA).
- Multi-material/colour on dual-nozzle printers: one part per material in the same coordinates; parts may
  touch but must never overlap. TPU must use the direct-drive nozzle."""

CADQUERY_GUIDE = """CadQuery script contract (backend "cadquery"):
```python
import cadquery as cq            # only geometry/maths imports are allowed (cadquery, math, numpy, ...)

PARAMS = {                        # every meaningful dimension, mm, keys = spec dimension names
    "length": 60.0,
    "hole_d": 3.4,
}
PRINT_POSE = {"lid": {"rotate": [180, 0, 0]}}   # optional; degrees about X then Y then Z

def build(p):                     # p = PARAMS; return {part_name: Workplane | Shape | Assembly}
    base = cq.Workplane("XY").box(p["length"], 40, 10, centered=(True, True, False))
    return {"base": base}         # parts in ASSEMBLED position; keys = spec part names
```
Rules: do not export, open files or print; the runner exports STEP/STL. Build each part as ONE solid.
Prefer robust operations: sketch -> extrude/revolve, boolean union/cut, shell, hole/cboreHole/cskHole,
fillet/chamfer on selected edges with radii smaller than the adjacent faces. If a fillet might fail, use a
smaller radius or a chamfer; never wrap geometry in try/except to hide failures.
Useful API: Workplane("XY"|"XZ"|"YZ"), .box(l,w,h,centered=(True,True,False)), .cylinder(h,r),
.circle(r).extrude(h), .rect(w,h), .polyline(pts).close(), .slot2D(len,d), .polygon(n,d), .sphere(r),
.text(txt, size, height, combine="cut"|"a"), .pushPoints([(x,y),...]), .rarray(xs,ys,nx,ny),
.polarArray(r,start,angle,n), .hole(d[,depth]), .cboreHole(d,cbore_d,cbore_depth), .cskHole(d,csk_d,90),
.faces(">Z"|"<Z"|">X"...), .edges("|Z"|">Z"|"#Z"), .fillet(r), .chamfer(d), .shell(-t), .revolve(deg, (0,0,0),(0,1,0)),
.loft(), .sweep(path), .mirror("YZ"), .translate((x,y,z)), .rotate((0,0,0),(0,0,1),deg), .union(), .cut(), .intersect(),
cq.Workplane().spline(pts), .twistExtrude(h, deg), cq.Solid.makeTorus(R,r), .split(keepTop=True)."""

ORGANIC_GUIDE = """Organic script contract (backend "organic", signed distance fields -> watertight mesh):
```python
from printwright.cad.organic import (sphere, ellipsoid, capsule, rounded_cone, rounded_box, cylinder, torus,
                                     half_space_above, half_space_below, mesh, mesh_parts)

PARAMS = {"height": 60.0, "voxel": 0.35}

def build(p):
    s = p["height"] / 60.0                      # design at 60 mm, scale to the requested height
    body = ellipsoid((14, 11, 18), (0, 0, 18))
    head = sphere(12, (0, 0, 41))
    ear = rounded_cone((6, 0, 49), (9, 0, 58), 3.5, 2.0)
    shape = body.smooth_union(head, 5).smooth_union(ear, 2.5).smooth_union(ear.mirror_x(), 2.5)
    shape = shape & half_space_above(2.0)       # flat base for bed adhesion
    shape = shape.translate((0, 0, -2.0)).scale(s)
    return {"body": mesh(shape, voxel=p["voxel"])}
```
Operators: a | b (union), a & b (intersect), a - b (subtract), a.smooth_union(b, k), a.smooth_subtract(b, k),
a.smooth_intersect(b, k), .round(r), .shell(t), .translate(v), .rotate("x"|"y"|"z", deg, center),
.scale(s), .mirror_x(), .mirror_y(). Blend radius k (mm) controls softness: 1-3 small features, 4-8 body parts.
Single colour: return {"body": mesh(shape, voxel=...)}. Several colours: return
mesh_parts({"body": body, "belly": belly, "eyes": eyes}, voxel=...) (also imported from printwright.cad.organic).
The FIRST part is the full outline of the object (include the areas the inlays will cover); later parts are inlays
that may poke out through the surface: they are trimmed to the outline and cut out of the parts before them with
exact booleans, so colours meet flush with no overlap. Do NOT intersect inlays with the body yourself. Inlays should sink >= 0.8 mm into the body so they stay attached. voxel 0.3-0.5 mm (smaller = finer,
slower). Lay out the model so it rests on z=0 with a flat base; no part may float."""

SCAD_GUIDE = """OpenSCAD contract (backend "openscad"): declare parts as `// @parts: base, lid`, read the
selected part from the variable PART (default "base"), model each part already in print orientation, and
put all dimensions in top-level variables. Use $fn >= 96 for round features."""


def printer_block(printer_summary: str, filaments: list[str]) -> str:
    fam = ", ".join(filaments[:80])
    return f"Target printer (from Bambu Studio's presets):\n{printer_summary}\nAvailable filament families: {fam}"


def modes_block() -> str:
    return "\n".join(f"- {p.name}: {p.summary}" for p in PROFILES.values())


# --------------------------------------------------------------------------- spec chat


def spec_system(mode: str, printer_summary: str, filaments: list[str]) -> str:
    mp = profile(mode)
    rules = "\n".join(f"- {r}" for r in mp.extra_rules)
    return f"""You are a senior product designer and mechanical engineer who designs parts for FDM 3D printing.
You run the requirements conversation for printwright: turn the user's wishes into a precise, buildable
design spec. The spec is the contract that CAD generation and automatic checks will be held to.

Design modes:
{modes_block()}
Current mode: {mp.name}. {mp.llm_focus}
{rules}

{printer_block(printer_summary, filaments)}

{FDM_RULES}

How to work:
- Always return the COMPLETE updated spec. Keep everything the user already agreed unless they change it.
- Fill gaps with sensible defaults and list each default under `assumptions`; put genuinely blocking
  unknowns under `open_questions` (at most 5, most important first) and ask them in `reply`.
- Dimension names are snake_case and become the CAD script's PARAMS keys. Part names are snake_case.
- Write acceptance checks that code can measure on the finished geometry: bbox (x,y,z in print orientation),
  hole (axis, diameter, tol, count), parameter (name, value, tol), min_wall, max_overhang, volume,
  single_body, watertight, no_interference.
- Choose filament per part from the available families and explain the choice when it matters
  (heat, UV, flexibility, strength). Pick `print.layout`: "assembled" only for multi-colour single objects.
- Pick `print.bed_type` so every chosen filament can be printed on it (Textured PEI suits PLA/PETG/ABS/ASA/TPU).
- Keep `mode` as given unless the user asks to change it; use `mode_recommendation` to suggest another.
- `reply` is in the same language the user writes in. Be brief and concrete; no filler."""


# --------------------------------------------------------------------------- CAD generation


def backend_guide(backend: str) -> str:
    return {"cadquery": CADQUERY_GUIDE, "organic": ORGANIC_GUIDE, "openscad": SCAD_GUIDE}[backend]


def codegen_system(spec: Spec, backend: str, printer_summary: str) -> str:
    mp = profile(spec.mode)
    rules = "\n".join(f"- {r}" for r in mp.extra_rules)
    return f"""You write parametric CAD scripts that become real 3D-printed parts.
Mode: {mp.name}. {mp.llm_focus}
{rules}

{printer_summary}

{FDM_RULES}

{backend_guide(backend)}

Output: exactly one fenced code block containing the complete script, preceded by at most five short
bullet points on design decisions (orientation, clearances, compensation). The script must implement
every part, feature, fit and dimension in the spec; PARAMS keys must match spec dimension names and the
returned dict keys must match the spec part names exactly: {", ".join(spec.part_names())}."""


def codegen_user(
    spec: Spec,
    previous_code: str | None = None,
    report: dict | None = None,
    review: dict | None = None,
    feedback: str | None = None,
) -> str:
    parts = [
        "Frozen spec (JSON):",
        "```json",
        json.dumps(spec.model_dump(exclude={"status", "mode_recommendation"}), indent=1),
        "```",
    ]
    if previous_code:
        parts += [
            "",
            "Current script (revise it; keep what works):",
            "```python",
            previous_code.strip(),
            "```",
        ]
    if report:
        parts += ["", "Automatic check results for the current script:", summarize_report(report)]
    if review and review.get("issues"):
        parts += ["", "Visual review findings:"]
        parts += [
            f"- [{i['severity']}] {i['title']}: {i['detail']} {i.get('suggestion', '')}"
            for i in review["issues"]
        ]
    if feedback:
        parts += ["", "User feedback (highest priority):", feedback]
    if not previous_code:
        parts += ["", "Write the script."]
    else:
        parts += ["", "Return the complete revised script."]
    return "\n".join(parts)


def repair_user(error: str, stage: str) -> str:
    what = {
        "guard": "was rejected by the safety guard",
        "run": "failed when run",
        "timeout": "timed out",
    }.get(stage, "failed")
    return (
        f"The script {what}:\n```\n{error.strip()[:6000]}\n```\n"
        "Fix the cause (not the symptom) and return the complete corrected script in one code block."
    )


def checks_repair_user(report: dict) -> str:
    return (
        "The script builds, but these checks fail:\n"
        + summarize_report(report, only_problems=True)
        + "\nChange the geometry so they pass without breaking anything else, and return the complete script. "
        "If a check itself contradicts the spec, keep to the spec and say so in one bullet."
    )


def summarize_report(report: dict, only_problems: bool = False) -> str:
    lines = []
    for r in report.get("checks", []):
        if only_problems and r["status"] in ("pass", "skip"):
            continue
        meas = "" if r["measured"] is None else f" measured={r['measured']}"
        exp = "" if r["expected"] is None else f" expected={r['expected']}"
        part = f" [{r['part']}]" if r.get("part") else ""
        lines.append(
            f"- {r['status'].upper()} {r['id']}{part}: {r['title']}.{meas}{exp} {r['message']}".rstrip()
        )
    return "\n".join(lines) or "- all checks pass"


# --------------------------------------------------------------------------- visual review


def review_system(spec: Spec) -> str:
    mp = profile(spec.mode)
    return f"""You review renders of a freshly generated CAD model against its spec before it is printed.
The image is a 2x2 sheet: iso (perspective), front (looking +Y), right (looking -X), top (looking -Z);
parts are shown in their assembled position, coloured per part, on a grey bed.
Mode: {mp.name}. {mp.review_focus}
Automatic geometry checks already measured sizes, holes, walls and overhangs; use them, do not re-guess
numbers from pixels. Focus on what code cannot see: missing or misplaced features, wrong shape or
proportions, wrong orientation, things that will not assemble or function, ugly or fragile details.
verdict "pass" only if nothing critical or major remains."""


def review_user(spec: Spec, report: dict) -> str:
    return (
        "Spec:\n```json\n"
        + json.dumps(spec.model_dump(exclude={"status", "mode_recommendation"}), indent=1)
        + "\n```\nAutomatic checks:\n"
        + summarize_report(report)
        + "\nReview the render sheet."
    )
