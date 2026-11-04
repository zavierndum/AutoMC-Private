"""
The 1.6%-enriched PWR pin used in several OpenMC notebooks, mirrored on all six faces, with a fine 2-D mesh tally of flux and fission:
a model whose statepoint post.py reads, to show how tallies and the source bank are reached from Python.

Derived from: the OpenMC notebook "Post Processing" (openmc-notebooks, tag v0.15.0; its pin, compositions and mesh). Written for
AutoMC; README.md explains each step of the reading.

    python build.py                          the defaults below; writes model.xml
    python build.py --mesh 50 --size reference

Nothing here runs transport.
"""
import argparse

import openmc

# The choices of this example: edit them here, or give them on the command line.
OPTIONS = {"mesh": 100, "size": "quick"}

R_FUEL, R_CLAD, PITCH = 0.39218, 0.45720, 1.26          # cm; no gap
SIZES = {"quick": {"particles": 2_000, "batches": 40, "inactive": 10},
         "reference": {"particles": 20_000, "batches": 110, "inactive": 10}}


def pin_materials():
    """The notebooks' 1.6% pin. The numbers are atom densities (atoms per barn-cm) used as relative amounts; the mass densities
    set the totals. The cladding is plain Zr-90, as in the notebooks."""
    fuel = openmc.Material(name="UO2, 1.6% enriched")
    for nuclide, n in (("U235", 3.7503e-4), ("U238", 2.2625e-2), ("O16", 4.6007e-2)):
        fuel.add_nuclide(nuclide, n)
    fuel.set_density("g/cm3", 10.31341)
    water = openmc.Material(name="borated water")
    for nuclide, n in (("H1", 4.9457e-2), ("O16", 2.4732e-2), ("B10", 8.0042e-6)):
        water.add_nuclide(nuclide, n)
    water.set_density("g/cm3", 0.740582)
    water.add_s_alpha_beta("c_H_in_H2O")                  # bound hydrogen (the notebook left it out)
    clad = openmc.Material(name="zircaloy (as Zr-90)")
    clad.add_nuclide("Zr90", 1.0)
    clad.set_density("g/cm3", 6.55)
    return fuel, clad, water


def build(mesh=100, size="quick"):
    fuel, clad, water = pin_materials()
    pellet, clad_out = openmc.ZCylinder(r=R_FUEL), openmc.ZCylinder(r=R_CLAD)
    h = PITCH / 2
    cube = openmc.model.RectangularParallelepiped(-h, h, -h, h, -h, h, boundary_type="reflective")
    cells = [openmc.Cell(name="fuel", fill=fuel, region=-pellet & -cube),
             openmc.Cell(name="cladding", fill=clad, region=+pellet & -clad_out & -cube),
             openmc.Cell(name="water", fill=water, region=+clad_out & -cube)]

    settings = openmc.Settings()
    settings.run_mode = "eigenvalue"
    for key, value in SIZES[size].items():
        setattr(settings, key, value)
    settings.seed = 20261103
    settings.source = openmc.IndependentSource(space=openmc.stats.Box([-R_FUEL, -R_FUEL, -h], [R_FUEL, R_FUEL, h]),
                                               constraints={"fissionable": True})
    # the source bank of the last batch goes into the statepoint (OpenMC's default), so post.py can look at it

    grid = openmc.RegularMesh(name="pin-cell grid")
    grid.dimension, grid.lower_left, grid.upper_right = [mesh, mesh], [-h, -h], [h, h]
    tally = openmc.Tally(name="flux and fission on the mesh")
    tally.filters = [openmc.MeshFilter(grid)]
    tally.scores = ["flux", "fission"]
    return openmc.Model(geometry=openmc.Geometry(cells), materials=openmc.Materials([fuel, clad, water]), settings=settings,
                        tallies=openmc.Tallies([tally]))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--mesh", type=int, default=OPTIONS["mesh"], help="mesh cells along x and y")
    parser.add_argument("--size", choices=list(SIZES), default=OPTIONS["size"])
    a = parser.parse_args()
    build(a.mesh, a.size).export_to_model_xml()


if __name__ == "__main__":
    main()
