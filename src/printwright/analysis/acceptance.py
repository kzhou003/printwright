"""Evaluate a build against the printer, the mode's rules and the spec's acceptance checks.

Produces one report per iteration. ``ready_to_slice`` is the gate the UI and CLI respect:

* always blocking: build failure, a part that does not fit the printer, a non-watertight part, walls thinner
  than the mode's hard minimum, overlapping parts where the mode forbids it;
* precision mode additionally blocks on every failed spec acceptance check and on a mismatch between a
  critical spec dimension and the script's PARAMS.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from printwright.analysis.mesh import analyze_mesh, analyze_part, holes_at, load, union_mesh
from printwright.bambu.profiles import PrinterInfo
from printwright.modes import profile
from printwright.spec import AcceptanceCheck, Spec

OVERLAP_EPS_MM3 = 0.01  # below this an overlap is boolean round-off, not geometry


def _row(
    cid, title, status, *, kind="auto", part=None, measured=None, expected=None, message="", blocking=False
):
    return {
        "id": cid,
        "title": title,
        "kind": kind,
        "part": part,
        "status": status,
        "measured": measured,
        "expected": expected,
        "message": message,
        "blocking": blocking and status == "fail",
    }


def _fits(size, area_wh, height) -> bool:
    sx, sy, sz = size
    w, h = area_wh
    return sz <= height + 1e-6 and ((sx <= w and sy <= h) or (sy <= w and sx <= h))


def _common_area(printer: PrinterInfo) -> tuple[float, float, float]:
    x0 = max(e.area[0] for e in printer.extruders)
    y0 = max(e.area[1] for e in printer.extruders)
    x1 = min(e.area[2] for e in printer.extruders)
    y1 = min(e.area[3] for e in printer.extruders)
    return (x1 - x0, y1 - y0, min(e.height for e in printer.extruders))


def evaluate(
    spec: Spec, build_dir: Path, manifest: dict, printer: PrinterInfo, thickness_samples: int = 3000
) -> dict:
    mp = profile(spec.mode)
    rows: list[dict] = []
    analyses: dict[str, dict] = {}
    params = manifest.get("params", {})
    filaments = {p.name: p.filament for p in spec.parts}
    multi_material = (
        len({filaments.get(p["name"], "") for p in manifest["parts"]}) > 1
        or len({p.color for p in spec.parts}) > 1
    )

    assembled = spec.print.layout == "assembled" and len(manifest["parts"]) > 1

    # ------------------------------------------------------------------ per-part printability
    for p in manifest["parts"]:
        name = p["name"]
        a = analyze_part(build_dir / p["stl"], name, printer.line_width, 45.0, thickness_samples)
        analyses[name] = a
        size = a["bbox"]["size"]
        sx, sy, sz = printer.size
        fits = _fits(size, (sx, sy), sz)
        rows.append(
            _row(
                f"fit_printer:{name}",
                "Fits the build volume",
                "pass" if fits else "fail",
                part=name,
                measured=size,
                expected=[sx, sy, sz],
                blocking=True,
                message="" if fits else "Part is larger than the printer; split it or scale it.",
            )
        )
        if spec.print.layout == "separate" and multi_material and len(printer.extruders) > 1:
            cw = _common_area(printer)
            if not _fits(size, cw[:2], cw[2]):
                rows.append(
                    _row(
                        f"fit_dual:{name}",
                        "Fits the area both nozzles reach",
                        "warn",
                        part=name,
                        measured=size,
                        expected=list(cw),
                        message="Only one nozzle can print this part; keep it single-material.",
                    )
                )
        rows.append(
            _row(
                f"watertight:{name}",
                "Closed (watertight) solid",
                "pass" if a["watertight"] else "fail",
                part=name,
                blocking=True,
                message="" if a["watertight"] else "Mesh has holes; slicers may print it wrongly.",
            )
        )
        if p.get("kind") == "brep" and not p.get("valid", True):
            rows.append(
                _row(
                    f"brep_valid:{name}",
                    "Valid B-rep solid",
                    "fail",
                    part=name,
                    blocking=True,
                    message="OpenCascade reports the solid as invalid.",
                )
            )
        if a["bodies"] > 1:
            rows.append(
                _row(
                    f"bodies:{name}",
                    "One connected body",
                    "warn",
                    part=name,
                    measured=a["bodies"],
                    expected=1,
                    message="Several loose bodies in one part; check for floating pieces.",
                )
            )
        t = a["thickness"]
        if t:
            if t["p5"] < mp.min_wall_error_mm:
                st, blk = "fail", True
            elif t["p5"] < mp.min_wall_warn_mm:
                st, blk = "warn", False
            else:
                st, blk = "pass", False
            rows.append(
                _row(
                    f"wall:{name}",
                    "Wall thickness (5th percentile)",
                    st,
                    part=name,
                    measured=t["p5"],
                    expected=f">= {mp.min_wall_warn_mm} (hard min {mp.min_wall_error_mm})",
                    blocking=blk,
                    message=""
                    if st == "pass"
                    else f"Thin regions (min {t['min']} mm); at {printer.line_width} mm line width they may not print.",
                )
            )
        if not assembled:
            _placement_rows(rows, a, name, spec, mp)

    if assembled:  # one multi-material object: judge placement on the union of its parts, as printed
        union = union_mesh([build_dir / p["assembly_stl"] for p in manifest["parts"]])
        a = analyze_mesh(union, "assembly", printer.line_width, 45.0, thickness_samples=0)
        analyses["_assembly"] = a
        _placement_rows(rows, a, "assembly", spec, mp)

    slots = {(p.filament, p.color.upper()) for p in spec.parts}
    if len(slots) > len(printer.extruders):
        rows.append(
            _row(
                "filament_count",
                "Filaments vs nozzles",
                "warn",
                measured=len(slots),
                expected=f"<= {len(printer.extruders)} without AMS",
                message=f"{len(slots)} filament/colour combinations on a {len(printer.extruders)}-nozzle printer: "
                "needs an AMS and adds filament changes and purge waste.",
            )
        )

    # ------------------------------------------------------------------ assembly
    for rec in manifest.get("interference", []):
        if "error" in rec:
            rows.append(
                _row(
                    f"interference:{rec['a']}-{rec['b']}", "Interference check", "warn", message=rec["error"]
                )
            )
            continue
        ov = rec.get("overlap_mm3", 0.0) or 0.0
        if ov > OVERLAP_EPS_MM3:
            st = "fail" if mp.interference_is_error else "warn"
            rows.append(
                _row(
                    f"interference:{rec['a']}-{rec['b']}",
                    "Parts do not overlap",
                    st,
                    measured=ov,
                    expected=0,
                    blocking=mp.interference_is_error,
                    message=f"{rec['a']} and {rec['b']} overlap by {ov:.3f} mm3 in the assembled pose.",
                )
            )
        else:
            rows.append(
                _row(
                    f"interference:{rec['a']}-{rec['b']}",
                    "Parts do not overlap",
                    "pass",
                    measured=0,
                    expected=0,
                    message=f"min distance {rec['min_distance_mm']} mm" if "min_distance_mm" in rec else "",
                )
            )
    if spec.print.layout == "assembled" and len(manifest["parts"]) > 1:
        lo = np.min([p["assembly_bbox"]["min"] for p in manifest["parts"]], axis=0)
        hi = np.max([p["assembly_bbox"]["max"] for p in manifest["parts"]], axis=0)
        size = (hi - lo).round(3).tolist()
        cw = _common_area(printer) if multi_material else printer.size
        ok = _fits(size, cw[:2], cw[2])
        rows.append(
            _row(
                "fit_assembled",
                "Assembled multi-material object fits",
                "pass" if ok else "fail",
                measured=size,
                expected=list(cw),
                blocking=True,
            )
        )

    # ------------------------------------------------------------------ spec dimensions vs PARAMS
    for d in spec.dimensions:
        if d.name not in params:
            if d.critical and spec.mode == "precision":
                rows.append(
                    _row(
                        f"param:{d.name}",
                        f"PARAMS exposes '{d.name}'",
                        "warn",
                        kind="spec",
                        message="Critical dimension is not a named parameter; it cannot be checked directly.",
                    )
                )
            continue
        try:
            v = float(params[d.name])
        except (TypeError, ValueError):
            continue
        tol = d.tolerance if d.tolerance is not None else (0.0 if spec.mode == "precision" else 0.5)
        ok = abs(v - d.value) <= tol + 1e-9
        st = "pass" if ok else ("fail" if d.critical or spec.mode == "precision" else "warn")
        rows.append(
            _row(
                f"param:{d.name}",
                f"PARAMS['{d.name}'] matches spec",
                st,
                kind="spec",
                measured=v,
                expected=f"{d.value:g} ± {tol:g}",
                blocking=mp.block_on_acceptance_fail,
            )
        )

    # ------------------------------------------------------------------ spec acceptance checks
    for c in spec.acceptance:
        rows.append(_check(c, spec, build_dir, manifest, analyses, mp.block_on_acceptance_fail))

    counts = {s: sum(1 for r in rows if r["status"] == s) for s in ("pass", "warn", "fail", "skip")}
    blocking = [r for r in rows if r["blocking"]]
    return {
        "mode": spec.mode,
        "checks": rows,
        "counts": counts,
        "blocking": [r["id"] for r in blocking],
        "ready_to_slice": not blocking,
        "analysis": analyses,
    }


def _placement_rows(rows: list, a: dict, name: str, spec: Spec, mp) -> None:
    """Bed contact, overhang and stability for one printed object (a part, or an assembled object)."""
    if not a["on_bed"]:
        rows.append(
            _row(
                f"on_bed:{name}",
                "Rests on the bed",
                "warn",
                part=name,
                measured=a["bbox"]["min"][2],
                expected=0.0,
            )
        )
    oh = a["overhang"]
    supports = spec.print.supports or mp.slice.supports
    if oh["area_mm2"] > 25 and supports == "none":
        better = a["orientations"][0]
        st = "warn"
        msg = f"{oh['area_mm2']} mm2 needs support but supports are off."
        if better["overhang_mm2"] < oh["area_mm2"] * 0.5:
            msg += f" Orientation '{better['orientation']}' would need only {better['overhang_mm2']} mm2."
        rows.append(
            _row(
                f"overhang:{name}",
                "Overhangs without support",
                st,
                part=name,
                measured=oh["area_mm2"],
                expected="< 25 mm2",
                message=msg,
            )
        )
    elif oh["fraction"] > mp.max_overhang_fraction_warn:
        rows.append(
            _row(
                f"overhang:{name}",
                "Overhang share",
                "warn",
                part=name,
                measured=oh["fraction"],
                expected=f"<= {mp.max_overhang_fraction_warn}",
                message="Large supported area; expect scars.",
            )
        )
    hb = a["bed_contact"]["height_to_base"]
    if hb is not None and hb > 4:
        rows.append(
            _row(
                f"tip:{name}",
                "Stable on the bed",
                "warn",
                part=name,
                measured=hb,
                expected="<= 4",
                message="Tall and narrow: add a brim or lay it down.",
            )
        )
    if a["bed_contact"]["area_mm2"] < 20:
        rows.append(
            _row(
                f"contact:{name}",
                "Bed contact area",
                "warn",
                part=name,
                measured=a["bed_contact"]["area_mm2"],
                expected=">= 20 mm2",
                message="Very small first layer; the part may detach.",
            )
        )


def _check(
    c: AcceptanceCheck, spec: Spec, build_dir: Path, manifest: dict, analyses: dict, block: bool
) -> dict:
    p = c.params or {}
    names = [c.part] if c.part else list(analyses)
    a = analyses.get(names[0]) if len(names) == 1 else None

    def row(status, measured=None, expected=None, message=""):
        return _row(
            c.id,
            c.description,
            status,
            kind=c.kind,
            part=c.part,
            measured=measured,
            expected=expected,
            message=message,
            blocking=block,
        )

    try:
        if c.kind == "bbox":
            if a is None:
                lo = np.min([analyses[n]["bbox"]["min"] for n in names], axis=0)
                hi = np.max([analyses[n]["bbox"]["max"] for n in names], axis=0)
                size = (hi - lo).tolist()
            else:
                size = a["bbox"]["size"]
            tol = float(p.get("tol", 0.1 if spec.mode == "precision" else 0.5))
            exp = {k: float(p[k]) for k in ("x", "y", "z") if k in p}
            meas = {k: round(size["xyz".index(k)], 3) for k in exp}
            bad = [k for k in exp if abs(meas[k] - exp[k]) > tol]
            # Accept a 90 degree turn in XY when both x and y are given
            if (
                bad
                and {"x", "y"} <= set(exp)
                and set(bad) <= {"x", "y"}
                and abs(size[1] - exp["x"]) <= tol
                and abs(size[0] - exp["y"]) <= tol
            ):
                bad = []
            return row(
                "fail" if bad else "pass",
                meas,
                {k: f"{v:g} ± {tol:g}" for k, v in exp.items()},
                f"off in {', '.join(bad)}" if bad else "",
            )
        if c.kind == "volume":
            vols = [analyses[n]["volume_mm3"] for n in names]
            if any(v is None for v in vols):
                return row("fail", message="volume undefined (not watertight)")
            v = sum(vols) / 1000.0
            lo, hi = float(p.get("min_cm3", 0)), float(p.get("max_cm3", 1e12))
            return row("pass" if lo <= v <= hi else "fail", round(v, 3), f"{lo:g}..{hi:g} cm3")
        if c.kind == "watertight":
            bad = [n for n in names if not analyses[n]["watertight"]]
            return row("fail" if bad else "pass", message=", ".join(bad))
        if c.kind == "single_body":
            bad = [n for n in names if analyses[n]["bodies"] != 1]
            return row("fail" if bad else "pass", {n: analyses[n]["bodies"] for n in names}, 1)
        if c.kind == "min_wall":
            need = float(p.get("mm", 1.2))
            meas = {n: (analyses[n]["thickness"] or {}).get("p5") for n in names}
            bad = [n for n, v in meas.items() if v is None or v < need]
            return row("fail" if bad else "pass", meas, f">= {need:g} mm")
        if c.kind == "max_overhang":
            lim = float(p.get("max_fraction", 0.1))
            meas = {n: analyses[n]["overhang"]["fraction"] for n in names}
            bad = [n for n, v in meas.items() if v > lim]
            return row("fail" if bad else "pass", meas, f"<= {lim:g}")
        if c.kind == "hole":
            return _hole(c, build_dir, manifest, names, row)
        if c.kind == "parameter":
            key = str(p.get("name", ""))
            if key not in manifest.get("params", {}):
                return row("fail", message=f"PARAMS has no '{key}'")
            v = float(manifest["params"][key])
            exp, tol = float(p.get("value", 0)), float(p.get("tol", 0))
            return row("pass" if abs(v - exp) <= tol + 1e-9 else "fail", v, f"{exp:g} ± {tol:g}")
        if c.kind == "no_interference":
            bad = [
                r for r in manifest.get("interference", []) if (r.get("overlap_mm3") or 0) > OVERLAP_EPS_MM3
            ]
            return row(
                "fail" if bad else "pass", [f"{r['a']}/{r['b']}: {r['overlap_mm3']}" for r in bad] or 0, 0
            )
    except Exception as e:  # a malformed check must not break the report
        return row("skip", message=f"could not evaluate: {type(e).__name__}: {e}")
    return row("skip", message=f"unknown check kind {c.kind}")


def _hole(c: AcceptanceCheck, build_dir: Path, manifest: dict, names: list[str], row) -> dict:
    p = c.params or {}
    axis = str(p.get("axis", "z")).lower()
    d, tol = float(p["diameter"]), float(p.get("tol", 0.1))
    count = int(p.get("count", 1))
    by_part = {q["name"]: q for q in manifest["parts"]}
    found: list[float] = []
    for n in names:
        m = load(build_dir / by_part[n]["stl"])
        lo, hi = m.bounds[:, "xyz".index(axis)]
        planes = [float(p["at"])] if "at" in p else []
        planes += list(np.linspace(lo, hi, 13)[1:-1])
        seen: list[tuple] = []
        for at in planes:
            for h in holes_at(m, axis, at):
                key = tuple(round(v, 1) for i, v in enumerate(h["center"]) if i != "xyz".index(axis))
                if key in seen:
                    continue
                if abs(h["diameter"] - d) <= max(tol, 0.5):  # collect near misses to report them
                    seen.append(key)
                    found.append(h["diameter"])
            if "at" in p and found:
                break
    ok = [x for x in found if abs(x - d) <= tol]
    status = "pass" if len(ok) >= count else "fail"
    msg = (
        ""
        if status == "pass"
        else (
            f"found {len(ok)} of {count} holes within tolerance"
            + (f"; near misses: {sorted(found)}" if found else "")
        )
    )
    return row(status, sorted(found)[:12], f"{count} x Ø{d:g} ± {tol:g} along {axis}", msg)
