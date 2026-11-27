"""Slice with Bambu Studio's command line and read back the results.

Two files are produced per slice job:

* ``<name>.3mf``        a Bambu Studio project (geometry + printer/process/filament settings, not sliced).
                        Open it in Bambu Studio to inspect, change settings and slice yourself.
* ``<name>.gcode.3mf``  the same project, sliced for the chosen printer. Bambu Studio opens it straight in
                        the preview tab, ready to print or send.

Findings baked into this module (verified against Bambu Studio 2.09 and the X2D presets):

* the CLI needs every preset flattened (no ``inherits``); we resolve the chain ourselves;
* on multi-nozzle printers each filament preset must list one ``filament_extruder_variant`` per printer
  extruder variant, otherwise the second filament fails with "could not found extruder_type Bowden";
  the GUI fills this in from the printer, the CLI does not, so we copy ``printer_extruder_variant``;
* ``--load-filament-ids`` maps each input file to a filament slot, ``--assemble`` merges inputs into one
  multi-part object (multi-colour prints) while keeping their coordinates.
"""

from __future__ import annotations

import json
import re
import subprocess
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from printwright.bambu.profiles import ProfileError, ProfileIndex, machine_for
from printwright.config import Settings, find_bambu_studio, native_cmd
from printwright.modes import process_preset_name, profile
from printwright.spec import Spec

SUPPORT = {"none": (0, None), "auto": (1, "normal(auto)"), "tree": (1, "tree(auto)")}
BRIM = {"auto": "auto_brim", "none": "no_brim", "outer": "outer_only"}
BED_TEMP_KEY = {
    "Textured PEI Plate": "textured_plate_temp",
    "Cool Plate": "cool_plate_temp",
    "Engineering Plate": "eng_plate_temp",
    "High Temp Plate": "hot_plate_temp",
    "Supertack Plate": "supertack_plate_temp",
}


@dataclass
class SlicePlan:
    machine: str
    process: str
    filaments: list[str]  # preset names, slot order
    colors: list[str]
    part_files: list[Path]
    part_slots: list[int]  # 1-based filament slot per part file
    assemble: bool
    overrides: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "machine": self.machine,
            "process": self.process,
            "filaments": self.filaments,
            "colors": self.colors,
            "parts": [p.name for p in self.part_files],
            "slots": self.part_slots,
            "assemble": self.assemble,
            "overrides": self.overrides,
        }


def plan(
    spec: Spec, part_stls: dict[str, Path], idx: ProfileIndex, extra_overrides: dict | None = None
) -> SlicePlan:
    mp = profile(spec.mode)
    pp = spec.print
    machine = machine_for(idx, pp.printer, pp.nozzle)
    if pp.process:
        process = pp.process
        idx.get("process", process)
    else:
        compatible = set(idx.compatible("process", machine))
        process = next(
            (c for c in process_preset_name(pp.printer, pp.nozzle, mp.slice) if c in compatible), None
        )
        if not process:
            raise ProfileError(f"No process preset for {machine}; compatible: {sorted(compatible)[:10]}")

    slots: dict[tuple[str, str], int] = {}
    filaments, colors, files, part_slots = [], [], [], []
    for part in spec.parts:
        if part.name not in part_stls:
            continue
        preset = idx.resolve_filament(part.filament, machine)
        key = (preset, part.color.upper())
        if key not in slots:
            slots[key] = len(filaments) + 1
            filaments.append(preset)
            colors.append(part.color)
        for _ in range(part.quantity if pp.layout == "separate" else 1):
            files.append(part_stls[part.name])
            part_slots.append(slots[key])

    temp_key = BED_TEMP_KEY.get(pp.bed_type)
    if temp_key:
        bad = []
        for name in filaments:
            v = idx.flatten("filament", name).get(temp_key, ["1"])
            if str(v[0] if isinstance(v, list) else v).strip() in {"0", ""}:
                bad.append(name)
        if bad:
            raise ProfileError(
                f"{', '.join(bad)} cannot be printed on the {pp.bed_type}; "
                f"choose another bed_type in the print plan."
            )

    sup = pp.supports or mp.slice.supports
    ov = {
        "sparse_infill_density": f"{pp.infill_percent if pp.infill_percent is not None else mp.slice.infill_percent}%",
        "sparse_infill_pattern": mp.slice.infill_pattern,
        "wall_loops": str(pp.wall_loops or mp.slice.wall_loops),
        "enable_support": str(SUPPORT[sup][0]),
        "brim_type": BRIM[pp.brim or mp.slice.brim],
        "curr_bed_type": pp.bed_type,
    }
    if SUPPORT[sup][1]:
        ov["support_type"] = SUPPORT[sup][1]
    ov.update(extra_overrides or {})
    return SlicePlan(
        machine,
        process,
        filaments,
        colors,
        files,
        part_slots,
        assemble=pp.layout == "assembled" and len(files) > 1,
        overrides=ov,
    )


def _write_presets(p: SlicePlan, idx: ProfileIndex, work: Path) -> tuple[Path, Path, list[Path]]:
    m = idx.flatten("machine", p.machine)
    proc = idx.flatten("process", p.process)
    for k, v in p.overrides.items():
        proc[k] = v
    variants = m.get("printer_extruder_variant")
    fpaths = []
    for i, (name, color) in enumerate(zip(p.filaments, p.colors, strict=True), 1):
        f = idx.flatten("filament", name)
        f["filament_colour"] = [color]
        if variants and len(m.get("nozzle_diameter", [])) > 1:
            f["filament_extruder_variant"] = list(variants)
        fp = work / f"filament_{i}.json"
        fp.write_text(json.dumps(f), encoding="utf-8")
        fpaths.append(fp)
    mp_, pp_ = work / "machine.json", work / "process.json"
    mp_.write_text(json.dumps(m), encoding="utf-8")
    pp_.write_text(json.dumps(proc), encoding="utf-8")
    return mp_, pp_, fpaths


def _cmd(
    exe: Path,
    mp_: Path,
    pp_: Path,
    fps: list[Path],
    p: SlicePlan,
    out_name: str,
    out_dir: Path,
    do_slice: bool,
) -> list[str]:
    cmd = [
        str(exe),
        "--debug",
        "2",
        "--load-settings",
        f"{mp_};{pp_}",
        "--load-filaments",
        ";".join(map(str, fps)),
        "--load-filament-ids",
        ",".join(map(str, p.part_slots)),
        "--arrange",
        "1",
        "--orient",
        "0",
        "--ensure-on-bed",
    ]
    if p.assemble:
        cmd.append("--assemble")
    if do_slice:
        cmd += ["--slice", "0"]
    cmd += ["--export-3mf", out_name, "--outputdir", str(out_dir), *map(str, p.part_files)]
    return native_cmd(cmd)


WARN = re.compile(r"found (?:NON_CRITICAL )?slicing warnings?: (.*)")


def slice_project(
    p: SlicePlan,
    idx: ProfileIndex,
    out_dir: Path,
    stem: str,
    settings: Settings | None = None,
    timeout_s: int = 900,
) -> dict:
    s = settings or Settings.load()
    exe = find_bambu_studio(s)
    if not exe:
        raise ProfileError("Bambu Studio is not installed or not found; set bambu_studio_path in settings.")
    out_dir.mkdir(parents=True, exist_ok=True)
    work = out_dir / "presets"
    work.mkdir(exist_ok=True)
    mp_, pp_, fps = _write_presets(p, idx, work)
    t0 = time.time()
    report: dict = {"plan": p.to_dict(), "files": {}}

    # 1) project file (not sliced) - always useful for opening and tweaking in the GUI
    proj = subprocess.run(
        _cmd(exe, mp_, pp_, fps, p, f"{stem}.3mf", out_dir, False),
        capture_output=True,
        text=True,
        timeout=timeout_s,
        cwd=out_dir,  # the CLI writes result.json into the working directory
    )
    if proj.returncode == 0 and (out_dir / f"{stem}.3mf").exists():
        report["files"]["project"] = f"{stem}.3mf"
    # 2) sliced file
    (out_dir / "result.json").unlink(missing_ok=True)  # don't let the project run's result pass for the slice
    proc = subprocess.run(
        _cmd(exe, mp_, pp_, fps, p, f"{stem}.gcode.3mf", out_dir, True),
        capture_output=True,
        text=True,
        timeout=timeout_s,
        cwd=out_dir,  # the CLI writes result.json into the working directory
    )
    log = proc.stdout + proc.stderr
    (out_dir / "slice.log").write_text(log, encoding="utf-8")
    report["seconds"] = round(time.time() - t0, 1)
    report["return_code"] = proc.returncode
    report["warnings"] = sorted({w.strip() for w in WARN.findall(log)})
    result_json = out_dir / "result.json"
    if result_json.exists():
        res = json.loads(result_json.read_text(encoding="utf-8"))
        report["error_string"] = res.get("error_string")
        report["plates"] = [_plate_summary(pl) for pl in res.get("sliced_plates", [])]
    sliced = out_dir / f"{stem}.gcode.3mf"
    report["ok"] = proc.returncode == 0 and sliced.exists()
    if report["ok"]:
        report["files"]["sliced"] = sliced.name
        report.update(read_3mf(sliced, out_dir))
    else:
        errs = [ln for ln in log.splitlines() if "[error]" in ln][-5:]
        report["error"] = (
            report.get("error_string") or "\n".join(errs) or f"Bambu Studio exited {proc.returncode}"
        )
    (out_dir / "slice.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def _plate_summary(pl: dict) -> dict:
    return {
        "index": pl.get("id"),
        "seconds": pl.get("main_predication"),
        "filaments": [
            {"slot": f.get("id"), "grams": round(f.get("total_used_g", 0.0), 2)}
            for f in pl.get("filaments", [])
        ],
        "filament_changes": pl.get("filament_change_times", 0),
        "objects": [o.get("name") for o in pl.get("objects", [])],
    }


def read_3mf(path: Path, out_dir: Path | None = None) -> dict:
    """Print time, weight, per-filament usage and the plate thumbnail from a sliced .gcode.3mf."""
    import xml.etree.ElementTree as ET

    info: dict = {"plates_info": []}
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        if "Metadata/slice_info.config" in names:
            root = ET.fromstring(z.read("Metadata/slice_info.config"))
            for plate in root.iter("plate"):
                meta = {m.get("key"): m.get("value") for m in plate.iter("metadata")}
                fil = [
                    {
                        "slot": int(f.get("id")),
                        "type": f.get("type"),
                        "color": f.get("color"),
                        "grams": float(f.get("used_g", 0)),
                        "meters": float(f.get("used_m", 0)),
                    }
                    for f in plate.iter("filament")
                ]
                info["plates_info"].append(
                    {
                        "index": int(meta.get("index", 1)),
                        "print_seconds": int(float(meta.get("prediction", 0))),
                        "weight_g": float(meta.get("weight", 0)),
                        "support_used": meta.get("support_used") == "true",
                        "outside": meta.get("outside") == "true",
                        "filaments": fil,
                    }
                )
        if out_dir and "Metadata/plate_1.png" in names:
            (out_dir / "plate_1.png").write_bytes(z.read("Metadata/plate_1.png"))
            info["thumbnail"] = "plate_1.png"
        if "Metadata/project_settings.config" in names:
            try:
                ps = json.loads(z.read("Metadata/project_settings.config"))
                info["printer_model"] = ps.get("printer_model")
                info["printer_preset"] = ps.get("printer_settings_id")
                info["process_preset"] = ps.get("print_settings_id")
                info["filament_presets"] = ps.get("filament_settings_id")
            except (json.JSONDecodeError, UnicodeDecodeError):
                pass
    return info
