"""
A concrete-walled room with a 2.5 MeV neutron point source inside it, a corridor and a maze-like gap beyond its walls, air all round:
deep penetration through 2 m of concrete, the problem weight windows are for. A mesh tally of the neutron flux covers the whole
building; run.py runs it twice in one process, first without variance reduction, then with weight windows made from the first run.

Derived from: the OpenMC notebook "Variance Reduction - Weight Windows" (openmc-notebooks, develop bb39f25; the building, materials,
source and procedure). Written for AutoMC; README.md explains weight windows and the figure of merit.

    python build.py                          writes model.xml
    python run.py                            both runs: a script run, approved by the user (it steps OpenMC through openmc.lib)

Nothing here runs transport.
"""
import argparse

import openmc

# The choices of this example: edit them here, or give them on the command line. run.py reads them from here too.
OPTIONS = {"mesh": 100, "size": "quick"}

# widths along x, depths along y, heights along z (cm), outside to outside, as in the notebook
WIDTHS = [100, 200, 500, 250, 200, 200, 100]
DEPTHS = [100, 200, 700, 600, 200, 100]
HEIGHTS = [100, 500, 100]
# particles per batch: the analog run, then the weight-window run (fewer: each history splits and costs more)
SIZES = {"quick": {"batches": 5, "analog": 2_000, "windows": 200}, "reference": {"batches": 5, "analog": 20_000, "windows": 2_000}}


def planes(kind, widths):
    edges = [0.0]
    for w in widths:
        edges.append(edges[-1] + w)
    make = {"x": openmc.XPlane, "y": openmc.YPlane, "z": openmc.ZPlane}[kind]
    return [make(e, boundary_type="vacuum" if i in (0, len(edges) - 1) else "transmission") for i, e in enumerate(edges)]


def build(mesh=100, size="quick"):
    air = openmc.Material(name="air")
    for el, f in (("N", 0.784431), ("O", 0.210748), ("Ar", 0.0046)):
        air.add_element(el, f)
    air.set_density("g/cm3", 0.001205)
    concrete = openmc.Material(name="concrete")
    for el, f in (("H", 0.168759), ("C", 0.001416), ("O", 0.562524), ("Na", 0.011838), ("Mg", 0.0014), ("Al", 0.021354),
                  ("Si", 0.204115), ("K", 0.005656), ("Ca", 0.018674), ("Fe", 0.00426)):
        concrete.add_element(el, f)              # atom fractions, as the notebook gives them
    concrete.set_density("g/cm3", 2.3)

    x, y, z = planes("x", WIDTHS), planes("y", DEPTHS), planes("z", HEIGHTS)
    box = lambda i0, i1, j0, j1, k0, k1: +x[i0] & -x[i1] & +y[j0] & -y[j1] & +z[k0] & -z[k1]
    regions = {   # (fill, region): x planes 0-7, y planes 0-6, z planes 0-3
        "air, left":        (air, box(0, 1, 1, 5, 0, 3)),
        "air, right":       (air, box(6, 7, 1, 5, 0, 3)),
        "air, top":         (air, box(0, 7, 5, 6, 0, 3)),
        "air, bottom":      (air, box(0, 7, 0, 1, 0, 3)),
        "wall, left":       (concrete, box(1, 2, 2, 4, 1, 2)),
        "wall, right":      (concrete, box(5, 6, 2, 5, 1, 2)),
        "wall, top":        (concrete, box(1, 4, 4, 5, 1, 2)),
        "wall, bottom":     (concrete, box(1, 6, 1, 2, 1, 2)),
        "wall, middle":     (concrete, box(3, 4, 3, 4, 1, 2)),
        "room":             (air, box(2, 3, 2, 4, 1, 2)),
        "gap":              (air, box(3, 4, 2, 3, 1, 2)),
        "corridor":         (air, box(4, 5, 2, 5, 1, 2)),
        "roof":             (concrete, box(1, 6, 1, 5, 0, 1)),
        "floor":            (concrete, box(1, 6, 1, 5, 2, 3)),
    }
    cells = [openmc.Cell(name=name, fill=fill, region=region) for name, (fill, region) in regions.items()]
    geometry = openmc.Geometry(cells)

    s = SIZES[size]
    settings = openmc.Settings()
    settings.run_mode = "fixed source"
    settings.particles, settings.batches = s["analog"], s["batches"]
    settings.seed = 20261111
    centre = (WIDTHS[0] + WIDTHS[1] + WIDTHS[2] * 0.5, DEPTHS[0] + DEPTHS[1] + DEPTHS[2] * 0.75, HEIGHTS[0] + HEIGHTS[1] * 0.5)
    settings.source = openmc.IndependentSource(space=openmc.stats.Point(centre), angle=openmc.stats.Isotropic(),
                                               energy=openmc.stats.Discrete([2.5e6], [1.0]), particle="neutron")
    settings.output = {"tallies": False}           # a large mesh: no text copy of the tallies

    grid = openmc.RegularMesh.from_domain(geometry, dimension=(mesh, mesh, 1))
    flux = openmc.Tally(name="neutron flux")
    flux.filters = [openmc.MeshFilter(grid), openmc.ParticleFilter(["neutron"])]
    flux.scores = ["flux"]
    return openmc.Model(geometry=geometry, materials=openmc.Materials([air, concrete]), settings=settings,
                        tallies=openmc.Tallies([flux]))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--mesh", type=int, default=OPTIONS["mesh"], help="mesh cells along x and along y")
    parser.add_argument("--size", choices=list(SIZES), default=OPTIONS["size"])
    a = parser.parse_args()
    build(a.mesh, a.size).export_to_model_xml()


if __name__ == "__main__":
    main()
