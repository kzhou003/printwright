"""User settings and discovery of external tools (Bambu Studio, Blender, Fusion, OpenSCAD, Claude Code).

Settings live in ``$PRINTWRIGHT_HOME/settings.json`` (default ``~/Printwright``). Every tool path can be
overridden there or through an environment variable, so nothing here is hard-wired to one machine.
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

ProviderName = Literal["claude-code", "openai", "anthropic", "fake"]


def home() -> Path:
    return Path(os.environ.get("PRINTWRIGHT_HOME", "~/Printwright")).expanduser()


class Settings(BaseModel):
    """Everything a user may want to change. Unknown keys in settings.json are ignored."""

    provider: ProviderName = "claude-code"

    # Claude Code (uses your existing `claude` login; no API key needed)
    claude_code_bin: str = "claude"
    claude_code_model: str | None = Field(None, description="e.g. 'opus' or 'sonnet'; None = CLI default")
    claude_code_effort: str | None = Field(None, description="low | medium | high | xhigh | max")
    claude_code_max_budget_usd: float | None = Field(
        5.0, description="Per-call spend cap passed to claude -p"
    )

    # OpenAI (needs OPENAI_API_KEY)
    openai_model: str = "gpt-5.1"
    openai_reasoning_effort: str | None = "medium"

    # Anthropic API (needs ANTHROPIC_API_KEY or an `ant auth login` profile)
    anthropic_model: str = "claude-opus-5-5"
    anthropic_effort: str = "high"

    # Pipeline behaviour
    cad_backend: Literal["cadquery", "openscad"] = "cadquery"
    max_repair_attempts: int = Field(3, ge=0, le=8)
    vision_review: bool = True
    renderer: Literal["auto", "blender", "matplotlib"] = "auto"
    build_timeout_s: int = 180

    # External tool overrides (None = auto-detect)
    bambu_studio_path: str | None = None
    bambu_profiles_dir: str | None = None
    bambu_user_dir: str | None = None
    blender_path: str | None = None
    openscad_path: str | None = None
    fusion_app: str | None = None

    @classmethod
    def path(cls) -> Path:
        return home() / "settings.json"

    @classmethod
    def load(cls) -> Settings:
        p = cls.path()
        data = {}
        if p.exists():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                data = {}
        known = {k: v for k, v in data.items() if k in cls.model_fields}
        env_provider = os.environ.get("PRINTWRIGHT_PROVIDER")
        if env_provider:
            known["provider"] = env_provider
        return cls(**known)

    def save(self) -> None:
        p = self.path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(self.model_dump_json(indent=2), encoding="utf-8")


# --------------------------------------------------------------------------- tool discovery

IS_MAC = sys.platform == "darwin"
IS_WIN = sys.platform.startswith("win")


@lru_cache(maxsize=1)
def mac_prefers_arm64() -> bool:
    """True when this Python runs under Rosetta on Apple Silicon.

    Launching a universal binary from an x86_64 process runs its x86_64 slice under Rosetta, which is
    several times slower for slicing and rendering; ``arch -arm64`` picks the native slice.
    """
    if not IS_MAC or platform.machine() != "x86_64":
        return False
    try:
        out = subprocess.run(["sysctl", "-n", "hw.optional.arm64"], capture_output=True, text=True, timeout=5)
        return out.stdout.strip() == "1"
    except (OSError, subprocess.SubprocessError):
        return False


def native_cmd(cmd: list[str]) -> list[str]:
    return ["arch", "-arm64", *cmd] if mac_prefers_arm64() else cmd


def _first_existing(cands: list[str | Path | None]) -> Path | None:
    for c in cands:
        if c and Path(c).expanduser().exists():
            return Path(c).expanduser()
    return None


def _which(*names: str) -> Path | None:
    for n in names:
        w = shutil.which(n)
        if w:
            return Path(w)
    return None


def find_bambu_studio(s: Settings) -> Path | None:
    env = os.environ.get("PRINTWRIGHT_BAMBU_STUDIO")
    cands: list[str | Path | None] = [s.bambu_studio_path, env]
    if IS_MAC:
        cands += [
            "/Applications/BambuStudio.app/Contents/MacOS/BambuStudio",
            "~/Applications/BambuStudio.app/Contents/MacOS/BambuStudio",
        ]
    elif IS_WIN:
        pf = os.environ.get("PROGRAMFILES", r"C:\Program Files")
        cands += [Path(pf) / "Bambu Studio" / "bambu-studio.exe"]
    return _first_existing(cands) or _which("bambu-studio", "BambuStudio")


def find_bambu_profiles(s: Settings, exe: Path | None) -> Path | None:
    """The `profiles` directory that ships with Bambu Studio (contains BBL.json and BBL/)."""
    cands: list[str | Path | None] = [s.bambu_profiles_dir, os.environ.get("PRINTWRIGHT_BAMBU_PROFILES")]
    if exe:
        cands += [
            exe.parent.parent / "Resources" / "profiles",  # macOS .app bundle
            exe.parent / "resources" / "profiles",  # Windows / Linux
            exe.parent.parent / "resources" / "profiles",
            exe.parent.parent / "share" / "BambuStudio" / "profiles",
        ]
    return _first_existing(cands)


def find_bambu_user_dir(s: Settings) -> Path | None:
    """Bambu Studio's data dir. Its system/ copy is the newest profile set (updated over the network)."""
    cands: list[str | Path | None] = [s.bambu_user_dir]
    if IS_MAC:
        cands.append("~/Library/Application Support/BambuStudio")
    elif IS_WIN:
        cands.append(Path(os.environ.get("APPDATA", "")) / "BambuStudio")
    else:
        cands.append("~/.config/BambuStudio")
    return _first_existing(cands)


def find_blender(s: Settings) -> Path | None:
    cands: list[str | Path | None] = [s.blender_path, os.environ.get("PRINTWRIGHT_BLENDER")]
    if IS_MAC:
        cands += [
            "/Applications/Blender.app/Contents/MacOS/Blender",
            "~/Applications/Blender.app/Contents/MacOS/Blender",
        ]
    elif IS_WIN:
        root = Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "Blender Foundation"
        if root.exists():
            cands += sorted(root.glob("Blender*/blender.exe"), reverse=True)
    return _first_existing(cands) or _which("blender")


def find_openscad(s: Settings) -> Path | None:
    cands: list[str | Path | None] = [s.openscad_path, os.environ.get("PRINTWRIGHT_OPENSCAD")]
    if IS_MAC:
        cands.append("/Applications/OpenSCAD.app/Contents/MacOS/OpenSCAD")
    elif IS_WIN:
        cands.append(Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "OpenSCAD" / "openscad.exe")
    return _first_existing(cands) or _which("openscad")


def find_fusion(s: Settings) -> str | None:
    """Fusion has no headless CLI; we only hand files to it. Returns an app name/path or None."""
    if s.fusion_app:
        return s.fusion_app
    if IS_MAC:
        for name in ("Autodesk Fusion", "Autodesk Fusion 360"):
            for root in (Path("/Applications"), Path.home() / "Applications"):  # Fusion installs per user
                if (root / f"{name}.app").exists():
                    return name
        return None
    if IS_WIN:
        return "shell"  # rely on the .step file association
    return None


def find_claude(s: Settings) -> Path | None:
    p = Path(s.claude_code_bin).expanduser()
    if p.is_absolute() and p.exists():
        return p
    return _which(s.claude_code_bin)


def bambu_version(exe: Path | None) -> str | None:
    if not exe:
        return None
    if IS_MAC:
        plist = exe.parent.parent / "Info.plist"
        if plist.exists():
            import plistlib

            try:
                return plistlib.loads(plist.read_bytes()).get("CFBundleShortVersionString")
            except Exception:
                return None
    return None


def doctor(s: Settings | None = None) -> dict:
    """A snapshot of what is installed, for the UI header and `printwright doctor`."""
    s = s or Settings.load()
    bambu = find_bambu_studio(s)
    profiles = find_bambu_profiles(s, bambu)
    blender = find_blender(s)
    claude = find_claude(s)
    has_openai = bool(os.environ.get("OPENAI_API_KEY"))
    try:
        import openai  # noqa: F401

        openai_sdk = True
    except ImportError:
        openai_sdk = False
    try:
        import anthropic  # noqa: F401

        anthropic_sdk = True
    except ImportError:
        anthropic_sdk = False
    return {
        "home": str(home()),
        "python": platform.python_version(),
        "platform": f"{sys.platform}-{platform.machine()}",
        "provider": s.provider,
        "bambu_studio": {
            "path": str(bambu) if bambu else None,
            "version": bambu_version(bambu),
            "profiles": str(profiles) if profiles else None,
        },
        "blender": str(blender) if blender else None,
        "openscad": str(find_openscad(s) or "") or None,
        "fusion": find_fusion(s),
        "providers": {
            "claude-code": {"available": bool(claude), "path": str(claude) if claude else None},
            "openai": {"available": has_openai and openai_sdk, "sdk": openai_sdk, "key": has_openai},
            "anthropic": {
                "available": anthropic_sdk,
                "sdk": anthropic_sdk,
                "key": bool(os.environ.get("ANTHROPIC_API_KEY")),
            },
        },
    }
