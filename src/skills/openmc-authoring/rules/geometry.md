# Geometry: surfaces, regions and cells

Derived from: the OpenMC 0.15.3 user's guide, geometry page (MIT). Written for AutoMC; API facts checked in the installed openmc
package (2026-11-04).

## Surfaces and half-spaces

1. **Lengths are in cm.** Surfaces are planes (`openmc.XPlane`, `openmc.YPlane`, `openmc.ZPlane`, `openmc.Plane`), spheres
   (`openmc.Sphere`), cylinders along an axis (`openmc.XCylinder`, `openmc.YCylinder`, `openmc.ZCylinder`), cones, tori and general
   quadrics.
2. **`-s` is the negative side, `+s` the positive one.** Inside a sphere or cylinder is `-s`; for `openmc.XPlane(x0=5)`, `-s` is
   x < 5. Regions combine with `&` (intersection), `|` (union) and `~` (complement).
3. **Ready-made shapes** in `openmc.model` give a region directly: `openmc.model.RectangularPrism` (a square or rectangle along an
   axis, `working_examples/pincell`), `openmc.model.RectangularParallelepiped` (a box), `openmc.model.RightCircularCylinder`,
   `openmc.model.HexagonalPrism`, `openmc.model.CylinderSector`, `openmc.model.Polygon`.

## Cells

4. **A cell is a region and a fill:** an `openmc.Material`, nothing (void), an `openmc.Universe` or a lattice. A cell's fill can be
   moved with `openmc.Cell.translation` and turned with `openmc.Cell.rotation`.
5. **Every point of the model must belong to exactly one cell.** A gap loses particles (the run stops once at least 10 are lost and
   at least one in a million of those run); an overlap is worse, because transport runs on and tracks particles in the wrong
   material. Build regions from
   the neighbours' surfaces, `region & ~neighbour` where a shape cuts into another, and let the model check and the plots find what
   remains (`rules/plots.md`).
6. **Name every cell.** Overlap messages, plots and tallies report cells; a name saves a lookup.

## Boundaries

7. **The outside needs a boundary condition** on the outermost surfaces, through `boundary_type`: `"vacuum"` (particles leave),
   `"reflective"`, `"white"` (reflected isotropically), `"periodic"` (paired surfaces) or `"transmission"` (the default, an
   ordinary surface). In a fixed-source model whose outside reflects everywhere no particle can leave, only be absorbed; the model
   check flags it.
8. **Reflection stands in for symmetry.** A pin or an assembly with reflective sides is an infinite lattice of itself
   (`working_examples/pincell`); a slab with reflective sides is infinitely wide (`working_examples/slab-fluence`). Say which
   symmetry you used and what it assumes.
9. **A sector of a model needs reflecting planes at its cuts**, or it leaks through them. The published CAD tokamak half leaked 81% of
   its neutrons; mirrored by reflecting planes it leaked 18% (`working_examples/cad-geometry`).
10. **The root universe** is what `openmc.Geometry` is made from; in a model built from cells it is created for you.
