"""Hand files to desktop apps: Bambu Studio, Autodesk Fusion, Blender, the file manager.

Fusion has no command-line or headless mode, so we only open STEP files in it (it imports them as
editable bodies). Blender opens the saved .blend scene, or imports STLs when there is none.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from printwright.config import IS_MAC, IS_WIN, Settings, find_bambu_studio, find_blender, find_fusion


class AppError(RuntimeError):
    pass


def _spawn(cmd: list[str]) -> None:
    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


def open_in(app: str, path: Path, settings: Settings | None = None) -> str:
    s = settings or Settings.load()
    path = path.resolve()
    if not path.exists():
        raise AppError(f"{path} does not exist")
    if app == "bambu":
        exe = find_bambu_studio(s)
        if not exe:
            raise AppError("Bambu Studio not found")
        if IS_MAC and ".app/" in str(exe):
            _spawn(["open", "-a", str(exe).split(".app/")[0] + ".app", str(path)])
        else:
            _spawn([str(exe), str(path)])
        return "Bambu Studio"
    if app == "fusion":
        fusion = find_fusion(s)
        if not fusion:
            raise AppError("Autodesk Fusion not found")
        if path.suffix.lower() not in (".step", ".stp", ".stl", ".3mf"):
            raise AppError("Fusion import needs a .step/.stl/.3mf file")
        if IS_MAC:
            _spawn(["open", "-a", fusion, str(path)])
        elif IS_WIN:
            os.startfile(str(path))  # type: ignore[attr-defined]
        return "Autodesk Fusion"
    if app == "blender":
        exe = find_blender(s)
        if not exe:
            raise AppError("Blender not found")
        if path.suffix == ".blend":
            _spawn([str(exe), str(path)])
        else:
            expr = (
                "import bpy\nfor o in list(bpy.data.objects): bpy.data.objects.remove(o)\n"
                f"bpy.ops.wm.stl_import(filepath={str(path)!r})"
            )
            _spawn([str(exe), "--python-expr", expr])
        return "Blender"
    if app == "reveal":
        if IS_MAC:
            _spawn(["open", "-R", str(path)])
        elif IS_WIN:
            _spawn(["explorer", "/select,", str(path)])
        else:
            _spawn(["xdg-open", str(path.parent)])
        return "file manager"
    raise AppError(f"unknown app {app!r}")


def available(settings: Settings | None = None) -> dict:
    s = settings or Settings.load()
    return {
        "bambu": bool(find_bambu_studio(s)),
        "fusion": bool(find_fusion(s)),
        "blender": bool(find_blender(s)),
        "reveal": sys.platform in ("darwin", "win32") or bool(os.environ.get("DISPLAY")),
    }
