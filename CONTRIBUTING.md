# Contributing

Thanks for helping. Issues and pull requests are welcome.

## Setup

```bash
uv sync --all-extras
uv run pytest
uv run ruff check src tests && uv run ruff format --check src tests
```

Tests run offline with a fake LLM, matplotlib renders and a tiny fixture copy of Bambu Studio presets. Tests marked
`integration` use the real Bambu Studio / Blender and skip when they are not installed.

## Guidelines

* **Measure, don't trust.** New checks belong in `analysis/` and must be computed from geometry, never from what
  the model claims. Add a test that a wrong model fails the check.
* **Mode rules live in `modes.py`.** Thresholds, defaults and gates go there, not scattered through the code.
* **Printer facts come from Bambu Studio presets.** Don't hard-code build volumes or nozzle data.
* **Prompts live in `llm/prompts.py`.** Keep them short and specific; every rule should change behaviour.
* **No network or API keys in tests.** Use `FakeProvider` and the profile fixtures.
* Keep the web UI dependency-free (plain ES modules, vendored three.js).

## Adding a printer vendor

Bambu presets are read from `<Bambu Studio>/resources/profiles/BBL`. Other vendors that ship Bambu-Studio-format
presets can be added by indexing their folder in `ProfileIndex.discover`.

## Adding a CAD backend

Implement a builder in `cad/build.py` that writes the same `manifest.json` / `parts/` / `assembly/` layout, add it
to the relevant modes in `modes.py`, and add its contract to `llm/prompts.py`.
