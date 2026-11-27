# Writing model scripts

Every backend follows the same idea: named parameters, one function that returns named parts, parts placed where
they sit in the assembly.

## CadQuery (balanced, precision)

```python
import cadquery as cq

PARAMS = {
    "base_length": 52.0,
    "m3_hole_d": 3.55,          # 3.4 nominal + 0.15 vertical-hole compensation
    "hole_spacing": 45.0,
}
PRINT_POSE = {"clamp": {"rotate": [180, 0, 0]}}   # optional: how each part lies on the bed

def build(p):
    base = (cq.Workplane("XY").box(p["base_length"], 20, 4, centered=(True, True, False))
            .faces(">Z").workplane()
            .pushPoints([(-p["hole_spacing"] / 2, 0), (p["hole_spacing"] / 2, 0)])
            .hole(p["m3_hole_d"]))
    return {"base": base}
```

* Units are millimetres, Z is up.
* `PARAMS` keys should equal the spec's dimension names: printwright compares them and the acceptance checks can
  reference them (`parameter` checks).
* Return parts in their **assembled** position. printwright checks overlap there, then applies `PRINT_POSE`, drops
  each part onto z=0 and centres it for printing.
* Don't export or open files; the runner writes `parts/<name>.step|stl`, `assembly/<name>.stl` and
  `assembly.step`.

## Organic (creative)

```python
from printwright.cad.organic import sphere, ellipsoid, rounded_cone, half_space_above, mesh

PARAMS = {"height": 60.0, "voxel": 0.35}

def build(p):
    s = p["height"] / 60
    body = ellipsoid((14, 11, 18), (0, 0, 18))
    head = sphere(12, (0, 0, 41))
    ear = rounded_cone((6, 0, 49), (9, 0, 58), 3.5, 2.0)
    cat = body.smooth_union(head, 5).smooth_union(ear, 2.5).smooth_union(ear.mirror_x(), 2.5)
    cat = (cat & half_space_above(2.0)).translate((0, 0, -2.0)).scale(s)
    return {"body": mesh(cat, voxel=p["voxel"])}
```

Primitives: `sphere`, `ellipsoid`, `capsule`, `rounded_cone`, `rounded_box`, `cylinder`, `torus`,
`half_space_above`, `half_space_below`.
Operators: `|`, `&`, `-`, `smooth_union(o, k)`, `smooth_subtract(o, k)`, `smooth_intersect(o, k)`, `round(r)`,
`shell(t)`, `translate`, `rotate(axis, deg, center)`, `scale`, `mirror_x`, `mirror_y`.

`mesh()` runs marching cubes and always returns a closed mesh. Smaller `voxel` = finer surface and slower build;
0.3–0.5 mm suits a 0.4 mm nozzle.

Multi-colour figurines: return `mesh_parts({"body": body, "belly": belly, "eyes": eyes}, voxel=0.35)`. The
first part is the whole outline (including where the inlays go); later parts are inlays that may poke out through
the surface. `mesh_parts` trims each inlay to the outline and cuts it out of the parts before it with exact mesh
booleans, so the colours meet flush with zero overlap and every part stays watertight after STL export. Don't
intersect inlays with the body yourself: coincident surfaces are exactly what makes booleans produce slivers.
Set the spec's print layout to `assembled`.

## OpenSCAD (optional)

Declare parts with `// @parts: base, lid`, read the variable `PART`, and model each part in print orientation.
OpenSCAD produces meshes only (no STEP).
