"""
A 17 x 17 assembly of the 1.6% pins, mirrored on its left and top sides and open on its right and bottom (a corner of a core, neutrons
leaking out of two faces), with a pin-by-pin mesh tally, a distributed-cell tally and a precision trigger: the results post.py reads as
pandas DataFrames.

Derived from: the OpenMC notebook "Pandas Dataframes" (openmc-notebooks, tag v0.15.0; the assembly, its boundaries, tallies and
trigger). Written for AutoMC; README.md explains the readings.

    python build.py                          the defaults below; writes model.xml
    python build.py --size reference

Nothing here runs transport.
"""
import argparse

import openmc

# The choices of this example: edit them here, or give them on the command line.
OPTIONS = {"size": "quick"}

R_FUEL, R_CLAD, PITCH, N = 0.39218, 0.45720, 1.26, 17
THERMAL_EV = 0.625
# batches: the minimum; the trigger adds batches (up to max_batches) until the pin fission rates reach the relative error asked
SIZES = {"quick": {"particles": 4_000, "batches": 30, "inactive": 10, "max_batches": 60, "rel_err": 0.10},
         "reference": {"particles": 20_000, "batches": 60, "inactive": 10, "max_batches": 200, "rel_err": 0.02}}


def pin_materials():
    """The notebooks' 1.6% pin (atom densities as relative amounts, mass densities as totals; cladding as Zr-90)."""
    fuel = openmc.Material(name="UO2, 1.6% enriched")
    for nuclide, n in (("U235", 3.7503e-4), ("U238", 2.2625e-2), ("O16", 4.6007e-2)):
        fuel.add_nuclide(nuclide, n)
    fuel.set_density("g/cm3", 10.31341)
    water = openmc.Material(name="borated water")
    for nuclide, n in (("H1", 4.9457e-2), ("O16", 2.4732e-2), ("B10", 8.0042e-6)):
        water.add_nuclide(nuclide, n)
    water.set_density("g/cm3", 0.740582)
    water.add_s_alpha_beta("c_H_in_H2O")
    clad = openmc.Material(name="zircaloy (as Zr-90)")
    clad.add_nuclide("Zr90", 1.0)
    clad.set_density("g/cm3", 6.55)
    return fuel, clad, water


def build(size="quick"):
    s = SIZES[size]
    fuel, clad, water = pin_materials()
    pellet, clad_out = openmc.ZCylinder(r=R_FUEL), openmc.ZCylinder(r=R_CLAD)
    fuel_cell = openmc.Cell(name="fuel", fill=fuel, region=-pellet)
    pin = openmc.Universe(name="1.6% pin", cells=[fuel_cell, openmc.Cell(name="cladding", fill=clad, region=+pellet & -clad_out),
                                                  openmc.Cell(name="water", fill=water, region=+clad_out)])
    half = N * PITCH / 2
    lattice = openmc.RectLattice(name="17 x 17 assembly")
    lattice.lower_left, lattice.pitch = (-half, -half), (PITCH, PITCH)
    lattice.universes = [[pin] * N for _ in range(N)]
    # left and top mirrored (the assembly continues there), right and bottom open (the edge of the core)
    left, right = openmc.XPlane(-half, boundary_type="reflective"), openmc.XPlane(half, boundary_type="vacuum")
    bottom, top = openmc.YPlane(-half, boundary_type="vacuum"), openmc.YPlane(half, boundary_type="reflective")
    low, high = openmc.ZPlane(-half, boundary_type="reflective"), openmc.ZPlane(half, boundary_type="reflective")
    root = openmc.Cell(name="assembly", fill=lattice, region=+left & -right & +bottom & -top & +low & -high)

    settings = openmc.Settings()
    settings.run_mode = "eigenvalue"
    settings.particles, settings.batches, settings.inactive = s["particles"], s["batches"], s["inactive"]
    settings.trigger_active, settings.trigger_max_batches, settings.trigger_batch_interval = True, s["max_batches"], 5
    settings.seed = 20261105
    settings.source = openmc.IndependentSource(space=openmc.stats.Box([-half, -half, -1.0], [half, half, 1.0]),
                                               constraints={"fissionable": True})

    grid = openmc.RegularMesh(name="one cell per pin")
    grid.dimension, grid.lower_left, grid.upper_right = [N, N], [-half, -half], [half, half]
    pins = openmc.Tally(name="pin rates")
    pins.filters = [openmc.MeshFilter(grid), openmc.EnergyFilter([0.0, THERMAL_EV, 20.0e6])]
    pins.scores = ["fission", "nu-fission"]
    trigger = openmc.Trigger("rel_err", s["rel_err"])
    trigger.scores = ["fission"]
    pins.triggers = [trigger]
    each = openmc.Tally(name="fuel absorption in each pin")
    each.filters = [openmc.DistribcellFilter(fuel_cell)]
    each.scores = ["absorption"]
    return openmc.Model(geometry=openmc.Geometry([root]), materials=openmc.Materials([fuel, clad, water]), settings=settings,
                        tallies=openmc.Tallies([pins, each]))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--size", choices=list(SIZES), default=OPTIONS["size"])
    a = parser.parse_args()
    build(a.size).export_to_model_xml()


if __name__ == "__main__":
    main()
