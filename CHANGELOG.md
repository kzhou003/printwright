# Changelog

## 0.1.0 - 2026-11-27

First public version.

* Spec-first loop: requirements chat, frozen spec, CAD generation, automatic build/check/repair, renders, visual
  review, Bambu Studio slicing.
* Three design modes: balanced (default), creative, precision.
* CAD backends: CadQuery (STEP + STL), organic SDF modelling, OpenSCAD (optional).
* Geometry checks: printer fit (per nozzle), watertightness, wall thickness, overhangs, bed contact, holes from
  cross-sections, PARAMS vs spec, interference in the assembled pose.
* Providers: Claude Code CLI, OpenAI Responses API, Anthropic API.
* Bambu Studio CLI slicing with flattened presets, multi-filament dual-nozzle support (X2D), project and sliced 3MF.
* Local web app with 3D viewer, CLI, Blender / Fusion / Bambu Studio hand-off.
