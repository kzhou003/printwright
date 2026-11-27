"""Runs INSIDE Blender (``blender -b --factory-startup -P blender_scene.py -- job.json``).

Imports the STL parts, colours them, renders four technical views with the Workbench engine (fast,
deterministic, shows edges and cavities well) and saves the scene as a .blend for manual inspection.
Only the Python standard library and bpy are available here.
"""

import json
import math
import sys

import bpy  # type: ignore[import-not-found]
from mathutils import Vector  # type: ignore[import-not-found]


def hex_rgba(h):
    h = h.lstrip("#")
    if len(h) != 6:
        return (0.8, 0.8, 0.8, 1.0)

    def lin(c):  # sRGB -> linear, Blender colours are linear
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (lin(int(h[i : i + 2], 16) / 255) for i in (0, 2, 4))
    return (r, g, b, 1.0)


def import_stl(path):
    before = set(bpy.data.objects)
    if hasattr(bpy.ops.wm, "stl_import"):
        bpy.ops.wm.stl_import(filepath=path)
    else:  # Blender < 4.1
        bpy.ops.import_mesh.stl(filepath=path)
    new = [o for o in bpy.data.objects if o not in before]
    return new[0]


def look_at(cam, target):
    d = Vector(target) - cam.location
    cam.rotation_euler = d.to_track_quat("-Z", "Y").to_euler()


def main():
    with open(sys.argv[sys.argv.index("--") + 1]) as f:
        job = json.load(f)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    sc = bpy.context.scene
    objs = []
    for part in job["parts"]:
        o = import_stl(part["stl"])
        o.name = part["name"]
        rgba = hex_rgba(part.get("color", "#cccccc"))
        o.color = rgba
        mat = bpy.data.materials.new(part["name"])
        mat.diffuse_color = rgba
        mat.use_nodes = True
        bsdf = mat.node_tree.nodes.get("Principled BSDF")
        if bsdf:
            bsdf.inputs["Base Color"].default_value = rgba
            bsdf.inputs["Roughness"].default_value = 0.55
        o.data.materials.append(mat)
        objs.append(o)

    lo = Vector(
        min(min((o.matrix_world @ Vector(c))[i] for c in o.bound_box) for o in objs) for i in range(3)
    )
    hi = Vector(
        max(max((o.matrix_world @ Vector(c))[i] for c in o.bound_box) for o in objs) for i in range(3)
    )
    center = (lo + hi) / 2
    size = hi - lo
    radius = max(size.length / 2, 1.0)

    if job.get("bed", True):
        bpy.ops.mesh.primitive_plane_add(size=1, location=(center.x, center.y, lo.z - 0.01))
        bed = bpy.context.active_object
        bed.name = "bed"
        bed.scale = (max(size.x, size.y) * 1.8 + 10, max(size.x, size.y) * 1.8 + 10, 1)
        bed.color = (0.80, 0.81, 0.84, 1)

    sc.render.engine = "BLENDER_WORKBENCH"
    sh = sc.display.shading
    sh.light = "STUDIO"
    sh.color_type = "OBJECT"
    sh.show_cavity = True
    sh.cavity_type = "BOTH"
    sh.show_object_outline = True
    sh.show_shadows = False
    sc.view_settings.view_transform = "Standard"  # no tone mapping: colours as specified
    if sc.world is None:
        sc.world = bpy.data.worlds.new("World")
    sc.world.color = (0.94, 0.94, 0.95)
    res = int(job.get("resolution", 900))
    sc.render.resolution_x = sc.render.resolution_y = res
    sc.render.film_transparent = False
    sc.render.image_settings.file_format = "PNG"

    cam = bpy.data.objects.new("camera", bpy.data.cameras.new("camera"))
    sc.collection.objects.link(cam)
    sc.camera = cam
    views = {
        "iso": ("PERSP", Vector((1.0, -1.25, 0.9))),
        "front": ("ORTHO", Vector((0, -1, 0))),
        "right": ("ORTHO", Vector((1, 0, 0))),
        "top": ("ORTHO", Vector((0, 0, 1))),
    }
    out = job["out_dir"]
    for name, (kind, direction) in views.items():
        cam.data.type = kind
        cam.data.clip_end = radius * 50
        if kind == "ORTHO":
            cam.data.ortho_scale = max(size.x, size.y, size.z) * 1.25 + 2
            cam.location = center + direction.normalized() * radius * 4
            if name == "top":
                cam.rotation_euler = (0, 0, 0)
            else:
                look_at(cam, center)
        else:
            cam.data.lens = 50
            dist = radius / math.tan(cam.data.angle / 2) * 1.15
            cam.location = center + direction.normalized() * dist
            look_at(cam, center)
        sc.render.filepath = f"{out}/{name}.png"
        bpy.ops.render.render(write_still=True)

    if job.get("blend"):
        bpy.ops.wm.save_as_mainfile(filepath=job["blend"])


main()
