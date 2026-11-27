"""Read Bambu Studio's own printer / process / filament presets.

Bambu Studio presets are JSON files that form inheritance chains (``"inherits": "<parent name>"``). The
command-line slicer wants each preset fully resolved, so we index every preset by its ``name`` and flatten
the chain ourselves. Three sources are indexed, later ones overriding earlier ones by name:

1. the profiles bundled with the app (``.../Resources/profiles/BBL``),
2. the copy Bambu Studio keeps up to date in its data dir (``.../BambuStudio/system/BBL``),
3. the user's own presets (``.../BambuStudio/user/<id>/{machine,process,filament}``).

Nothing about a specific printer is hard-coded: build volume, nozzle count and extruder types are read from
the machine preset, so a new Bambu printer works as soon as Bambu Studio knows about it.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from pathlib import Path

from printwright.config import Settings, find_bambu_profiles, find_bambu_studio, find_bambu_user_dir

KINDS = ("machine", "process", "filament")


class ProfileError(RuntimeError):
    pass


@dataclass
class Preset:
    kind: str
    name: str
    path: Path
    data: dict
    source: str  # bundled | system | user

    @property
    def selectable(self) -> bool:
        """Leaf presets the GUI shows. Templates/@base presets have instantiation=false."""
        if self.source == "user":
            return True
        return str(self.data.get("instantiation", "false")).lower() == "true"


@dataclass
class ProfileIndex:
    presets: dict[tuple[str, str], Preset] = field(default_factory=dict)
    roots: list[str] = field(default_factory=list)

    # ----------------------------------------------------------------- loading
    def add_tree(self, root: Path, source: str) -> int:
        n = 0
        for kind in KINDS:
            d = root / kind
            if not d.is_dir():
                continue
            for f in d.rglob("*.json"):
                try:
                    data = json.loads(f.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                name = data.get("name")
                if not isinstance(name, str):
                    continue
                self.presets[(kind, name)] = Preset(kind, name, f, data, source)
                n += 1
        if n:
            self.roots.append(f"{source}:{root}")
        return n

    @classmethod
    def discover(cls, settings: Settings | None = None, vendor: str = "BBL") -> ProfileIndex:
        s = settings or Settings.load()
        idx = cls()
        bundled = find_bambu_profiles(s, find_bambu_studio(s))
        if bundled:
            idx.add_tree(bundled / vendor, "bundled")
        data_dir = find_bambu_user_dir(s)
        if data_dir:
            idx.add_tree(data_dir / "system" / vendor, "system")
            user_root = data_dir / "user"
            if user_root.is_dir():
                for uid in sorted(p for p in user_root.iterdir() if p.is_dir()):
                    idx.add_tree(uid, "user")
        if not idx.presets:
            raise ProfileError(
                "No Bambu Studio presets found. Install Bambu Studio or set bambu_profiles_dir in settings.json."
            )
        return idx

    # ----------------------------------------------------------------- queries
    def get(self, kind: str, name: str) -> Preset:
        try:
            return self.presets[(kind, name)]
        except KeyError:
            raise ProfileError(f"{kind} preset not found: {name!r}") from None

    def flatten(self, kind: str, name: str) -> dict:
        """Resolve the inherits chain into one self-contained preset (child keys win)."""
        chain: list[dict] = []
        seen: set[str] = set()
        cur: str | None = name
        while cur:
            if cur in seen:
                raise ProfileError(f"inheritance cycle at {cur!r}")
            seen.add(cur)
            p = self.get(kind, cur)
            chain.append(p.data)
            cur = p.data.get("inherits") or None
        out: dict = {}
        for d in reversed(chain):
            out.update(copy.deepcopy(d))
        out.pop("inherits", None)
        out["name"] = name
        return out

    def machines(self, printer_model: str | None = None) -> list[str]:
        out = []
        for (kind, name), p in self.presets.items():
            if kind != "machine" or not p.selectable:
                continue
            model = p.data.get("printer_model") or self.flatten("machine", name).get("printer_model")
            if printer_model and model != printer_model:
                continue
            out.append(name)
        return sorted(out)

    def printer_models(self) -> list[str]:
        models = set()
        for (kind, _name), p in self.presets.items():
            if kind == "machine" and p.selectable:
                m = p.data.get("printer_model")
                if m:
                    models.add(m)
        return sorted(models)

    def compatible(self, kind: str, machine: str) -> list[str]:
        """Process or filament presets whose compatible_printers lists this machine preset."""
        out = []
        for (k, name), p in self.presets.items():
            if k != kind or not p.selectable:
                continue
            comp = p.data.get("compatible_printers")
            if comp is None:
                comp = self.flatten(kind, name).get("compatible_printers", [])
            if machine in comp:
                out.append(name)
        return sorted(out)

    def resolve_filament(self, base_name: str, machine: str) -> str:
        """Map a human filament name ('Bambu PLA Basic') to the preset for this machine.

        Accepts an exact preset name too. Preference: '<name> @BBL <model> <d> nozzle', then any compatible
        preset whose name starts with '<name> @'.
        """
        if ("filament", base_name) in self.presets and machine in self.flatten("filament", base_name).get(
            "compatible_printers", []
        ):
            return base_name
        compatible = self.compatible("filament", machine)
        nozzle = machine.rsplit(" ", 2)[-2] if machine.endswith(" nozzle") else ""
        prefix = base_name.strip() + " @"
        cands = [c for c in compatible if c.startswith(prefix)]
        if not cands:
            raise ProfileError(
                f"No filament preset named {base_name!r} is compatible with {machine!r}. "
                f"Try one of: {', '.join(sorted({c.split(' @')[0] for c in compatible})[:30])}"
            )
        cands.sort(key=lambda c: (f" {nozzle} nozzle" not in c, len(c)))
        return cands[0]

    def filament_families(self, machine: str) -> list[str]:
        return sorted({c.split(" @")[0] for c in self.compatible("filament", machine)})


# --------------------------------------------------------------------------- printer geometry


def _parse_area(points) -> tuple[float, float, float, float]:
    """['0x0','256x0','256x256','0x256'] or ['0x0,256x0,...'] -> (xmin, ymin, xmax, ymax)."""
    if isinstance(points, str):
        points = points.split(",")
    if len(points) == 1 and "," in points[0]:
        points = points[0].split(",")
    xs, ys = [], []
    for pt in points:
        x, y = pt.strip().split("x")
        xs.append(float(x))
        ys.append(float(y))
    return min(xs), min(ys), max(xs), max(ys)


@dataclass
class Extruder:
    index: int  # 1-based, as Bambu Studio numbers them
    kind: str  # Direct Drive | Bowden
    nozzle: float
    area: tuple[float, float, float, float]
    height: float

    @property
    def size(self) -> tuple[float, float, float]:
        x0, y0, x1, y1 = self.area
        return (x1 - x0, y1 - y0, self.height)


@dataclass
class PrinterInfo:
    machine_preset: str
    model: str
    area: tuple[float, float, float, float]
    height: float
    extruders: list[Extruder]
    line_width: float = 0.42

    @property
    def size(self) -> tuple[float, float, float]:
        x0, y0, x1, y1 = self.area
        return (x1 - x0, y1 - y0, self.height)

    def summary(self) -> str:
        sx, sy, sz = self.size
        lines = [f"{self.model} (preset '{self.machine_preset}'): build volume {sx:g} x {sy:g} x {sz:g} mm."]
        if len(self.extruders) > 1:
            for e in self.extruders:
                ex, ey, ez = e.size
                lines.append(
                    f"  Nozzle {e.index} ({e.kind}, {e.nozzle:g} mm): printable {ex:g} x {ey:g} x {ez:g} mm "
                    f"(x {e.area[0]:g}..{e.area[2]:g})."
                )
            lines.append("  Parts printed in two materials must fit the region both nozzles can reach.")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "machine_preset": self.machine_preset,
            "model": self.model,
            "size": self.size,
            "area": self.area,
            "extruders": [
                {"index": e.index, "kind": e.kind, "nozzle": e.nozzle, "size": e.size, "area": e.area}
                for e in self.extruders
            ],
        }


def printer_info(idx: ProfileIndex, machine: str) -> PrinterInfo:
    m = idx.flatten("machine", machine)
    area = _parse_area(m.get("printable_area", ["0x0", "256x0", "256x256", "0x256"]))
    height = float(m.get("printable_height", 250))
    nozzles = [float(x) for x in m.get("nozzle_diameter", ["0.4"])]
    kinds = m.get("extruder_type") or ["Direct Drive"] * len(nozzles)
    ex_areas = m.get("extruder_printable_area") or []
    ex_heights = m.get("extruder_printable_height") or []
    extruders = []
    for i, d in enumerate(nozzles):
        a = _parse_area(ex_areas[i]) if i < len(ex_areas) else area
        h = float(ex_heights[i]) if i < len(ex_heights) else height
        extruders.append(Extruder(i + 1, kinds[i] if i < len(kinds) else "Direct Drive", d, a, h))
    return PrinterInfo(machine, m.get("printer_model", machine), area, height, extruders)


def machine_for(idx: ProfileIndex, printer_model: str, nozzle: float) -> str:
    """'Bambu Lab X2D' + 0.4 -> 'Bambu Lab X2D 0.4 nozzle'."""
    want = f"{printer_model} {nozzle:g} nozzle"
    if ("machine", want) in idx.presets:
        return want
    options = idx.machines(printer_model)
    if not options:
        raise ProfileError(
            f"Bambu Studio has no presets for printer {printer_model!r}. "
            f"Known printers: {', '.join(idx.printer_models())}"
        )
    raise ProfileError(f"No {nozzle:g} mm nozzle preset for {printer_model}. Options: {', '.join(options)}")
