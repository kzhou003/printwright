"""608 bearing pillow block (precision mode).

A 608 bearing (8 x 22 x 7 mm) is held in a bore with a rear retaining lip that clears the inner race, and the
block bolts down with two M4 screws. Printed with the bore axis vertical so the bore comes out round.
"""

import cadquery as cq

PARAMS = {
    "bearing_od": 22.0,
    "bearing_width": 7.0,
    "bore_clearance": 0.075,  # per side: snug slip fit for a printed bore
    "hole_comp": 0.15,  # vertical holes print undersize by about this much on diameter
    "bore_d": 22.3,  # = bearing_od + 2 * bore_clearance + hole_comp
    "lip_d": 16.0,  # clears the 608 inner race (~12.1 mm) and keeps the outer race from passing
    "lip_t": 2.0,
    "axis_height": 18.0,
    "boss_d": 32.0,
    "base_length": 56.0,
    "base_t": 6.0,
    "m4_clearance_d": 4.5,  # ISO 273 medium
    "m4_spacing": 42.0,
}
PRINT_POSE = {"block": {"rotate": [-90, 0, 0]}}  # bore axis (Y) becomes vertical


def build(p):
    depth = p["bearing_width"] + p["lip_t"]
    profile = (
        cq.Workplane("XZ")
        .center(0, p["base_t"] / 2)
        .rect(p["base_length"], p["base_t"])
        .extrude(-depth)
        .union(cq.Workplane("XZ").center(0, p["axis_height"]).circle(p["boss_d"] / 2).extrude(-depth))
        .union(
            cq.Workplane("XZ")
            .center(0, p["axis_height"] / 2)
            .rect(p["boss_d"], p["axis_height"])
            .extrude(-depth)
        )
    )
    bore = cq.Workplane("XZ").center(0, p["axis_height"]).circle(p["bore_d"] / 2).extrude(-p["bearing_width"])
    lip_hole = cq.Workplane("XZ").center(0, p["axis_height"]).circle(p["lip_d"] / 2).extrude(-depth)
    bolts = (
        cq.Workplane("XY")
        .pushPoints([(-p["m4_spacing"] / 2, depth / 2), (p["m4_spacing"] / 2, depth / 2)])
        .circle(p["m4_clearance_d"] / 2)
        .extrude(p["base_t"])
    )
    block = profile.cut(bore).cut(lip_hole).cut(bolts)
    return {"block": block}
