"""Desk-edge cable clip (balanced mode).

The whole part is one 2D profile (in XZ) extruded along Y, so it prints lying on its side with no overhangs:
a C-clamp that slides onto the desk edge, and on top a block with round cable channels that snap shut.
"""

import cadquery as cq

PARAMS = {
    "desk_thickness": 19.0,
    "clamp_clearance": 0.3,  # per side
    "clamp_depth": 22.0,
    "wall": 3.0,
    "width": 16.0,  # extrusion length = how wide the clip is along the desk edge
    "cable_d": 6.4,  # 6 mm cables + 0.4 mm
    "cable_count": 3,
    "mouth": 4.8,  # opening narrower than the cable so it snaps in
}
PRINT_POSE = {"clip": {"rotate": [90, 0, 0]}}


def build(p):
    w = p["wall"]
    gap = p["desk_thickness"] + 2 * p["clamp_clearance"]
    depth = p["clamp_depth"]
    d, n = p["cable_d"], int(p["cable_count"])
    pitch = d + w
    rack_len = n * pitch + w
    rack_h = d + 2 * w

    # C clamp: outer block minus the desk slot (open towards +X)
    clamp = cq.Workplane("XZ").rect(depth + w, gap + 2 * w, centered=False).extrude(p["width"])
    slot = cq.Workplane("XZ").center(w, w).rect(depth + 1, gap, centered=False).extrude(p["width"])
    body = clamp.cut(slot)

    # cable rack sitting on top of the clamp, channels run along Y (parallel to the desk edge)
    top = gap + 2 * w
    rack = cq.Workplane("XZ").center(0, top).rect(rack_len, rack_h, centered=False).extrude(p["width"])
    for i in range(n):
        cx = w + d / 2 + i * pitch
        cz = top + w + d / 2
        hole = cq.Workplane("XZ").center(cx, cz).circle(d / 2).extrude(p["width"])
        mouth = (
            cq.Workplane("XZ")
            .center(cx - p["mouth"] / 2, cz)
            .rect(p["mouth"], rack_h, centered=False)
            .extrude(p["width"])
        )
        rack = rack.cut(hole).cut(mouth)
    body = body.union(rack)
    return {"clip": body.translate((0, p["width"], 0))}
