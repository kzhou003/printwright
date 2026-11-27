# Architecture

## Pieces

```
src/printwright/
  modes.py          the three design modes: backends, spec gates, check thresholds, slice defaults, LLM focus
  spec.py           Spec model (pydantic), lint rules per mode, markdown rendering, digest
  project.py        on-disk project store (plain JSON + files)
  pipeline.py       spec_turn, set_mode, freeze, generate (codegen -> build -> check -> repair -> render -> review),
                    rebuild, import_script, slice_iteration
  llm/              provider-neutral interface + claude-code / openai / anthropic / fake; prompts.py holds all prompts
  cad/guard.py      static checks on model scripts
  cad/runner.py     runs one script in a child process; exports parts (print pose) and assembly; interference
  cad/build.py      starts the runner with a timeout and a scrubbed environment; OpenSCAD backend
  cad/organic.py    signed-distance-field modelling for creative mode
  analysis/mesh.py  printability measurements on STL
  analysis/acceptance.py  printer + mode + spec checks -> report with ready_to_slice gate
  render/           Blender Workbench renders (+ .blend scene), matplotlib fallback, contact sheet
  bambu/profiles.py index and flatten Bambu Studio presets; printer geometry
  bambu/slicer.py   slice plan from spec + mode, flattened presets, Bambu Studio CLI, 3MF parsing
  server.py, jobs.py, static/   local web app (FastAPI + plain JS + three.js)
  cli.py            Typer CLI
```

## Data on disk

```
~/Printwright/
  settings.json
  projects/<id>/
    project.json            title, chat, spec, iteration + slice summaries
    spec.md
    iterations/v001/
      prompt.md             exact system + user prompt sent for generation
      attempt_N.py          every script the model returned
      model.py              the final script
      build/                manifest.json, build.log, parts/*.stl|step (print pose), assembly/*.stl, assembly.step
      report.json           checks + per-part analysis
      renders/              iso/front/right/top.png, sheet.png, scene.blend
      review.json           visual review
      iteration.json        summary
    slices/s001/            presets/*.json (flattened), <name>.3mf, <name>.gcode.3mf, result.json, slice.json,
                            slice.log, plate_1.png
```

## The generate loop

1. Prompt = mode focus + mode rules + printer facts from Bambu Studio + FDM rules + backend contract; user turn = the
   frozen spec JSON, and for revisions the previous script, its check report, the visual review and your feedback.
2. The reply's code block is written to `model.py` and built by `cad/build.py`:
   guard → child process (`python -m printwright.cad.runner`) → `manifest.json`.
3. Build errors (with a trimmed traceback) go back to the model. So do part-name mismatches.
4. `analysis/acceptance.evaluate` measures every part. Failing checks that are blocking, or that come from the spec,
   go back to the model. Precision mode keeps repairing until everything passes or attempts run out; balanced and
   creative stop after one repair round for non-blocking spec checks.
5. Render the assembled pose, then the visual review (one 2×2 sheet image) produces `review.json`. Reviews never
   trigger automatic changes; you decide with *Revise* / *Revise with these findings*.

## Measurements

| Check | How |
|---|---|
| Printer fit | part bbox (with a 90° turn allowed) vs printable area/height; multi-material parts vs the area both nozzles reach |
| Watertight / bodies | trimesh topology |
| Wall thickness | 3000 surface samples, ray cast inward along -normal, 5th percentile vs mode thresholds |
| Overhang | downward faces steeper than 45° from vertical, excluding the bed layer; six orientations compared |
| Bed contact / tipping | area of faces on z=0; height / equivalent-diameter ratio |
| Holes | planar cross-sections, interior rings, circularity ≥ 0.85, area-equivalent diameter |
| Interference | manifold3d boolean intersection of tessellated parts in the assembled pose (exact on meshes; OpenCascade's B-rep intersection is unreliable with coplanar contact) |
| PARAMS vs spec | every spec dimension whose name is a PARAMS key |

## Slicing notes (Bambu Studio CLI)

* Presets are flattened (inherits resolved) and written next to the output, so every slice is reproducible.
* On multi-nozzle printers each filament preset gets `filament_extruder_variant` = the printer's
  `printer_extruder_variant`; without it the second filament fails to slice.
* `curr_bed_type` is set from the spec; each filament's `<plate>_temp` must be non-zero for that plate.
* `--load-filament-ids` assigns each part file to a filament slot; `--assemble` merges parts into one
  multi-material object for layout `assembled`.
* Two runs per slice: without `--slice` for the editable project 3MF, with `--slice 0` for the `.gcode.3mf`.
