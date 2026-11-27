"""Sitting cat figurine, two colours (creative mode). Orange body with a white belly patch inlaid flush."""

from printwright.cad.organic import capsule, ellipsoid, half_space_above, mesh_parts, rounded_cone, sphere

PARAMS = {"height": 60.0, "voxel": 0.4}


def build(p):
    # designed at 60 mm tall, then scaled
    body = ellipsoid((15, 12, 17), (0, 0, 17))
    haunch = ellipsoid((10, 9, 8), (0, 2, 8))
    head = sphere(12.5, (0, -2, 40))
    ear = rounded_cone((6.5, -2, 49), (9.5, -1, 59), 4.0, 1.2)
    paw = capsule((4.5, -9, 3), (4.5, -11, 3), 3.2)
    tail = capsule((10, 8, 3), (-6, 13, 3), 2.8)
    cat = (
        body.smooth_union(haunch, 4)
        .smooth_union(haunch.mirror_x(), 4)
        .smooth_union(head, 5)
        .smooth_union(ear, 2.5)
        .smooth_union(ear.mirror_x(), 2.5)
        .smooth_union(paw, 2)
        .smooth_union(paw.mirror_x(), 2)
        .smooth_union(tail, 2.5)
    )
    belly = ellipsoid((8, 5, 10), (0, -11, 16))  # pokes out of the front; mesh_parts trims it to the body
    flat = half_space_above(1.5)
    s = p["height"] / 60.0
    cat = (cat & flat).translate((0, 0, -1.5)).scale(s)
    belly = (belly & flat).translate((0, 0, -1.5)).scale(s)
    return mesh_parts({"body": cat, "belly": belly}, voxel=p["voxel"])
