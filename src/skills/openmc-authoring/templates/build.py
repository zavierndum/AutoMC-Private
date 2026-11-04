"""
TEMPLATE: the shape of every AutoMC build script. It builds a small complete model (a water sphere with a point source inside, a
flux tally in the water) so that it runs as it is; replace each part with the user's problem and keep the structure:

    the choices a user may change in OPTIONS, the dimensions as named constants, the run sizes in SIZES,
    one function per part of the model, build() putting them together, main() reading the command line.

    python build.py                       writes model.xml (the quick size)
    python build.py --size reference

AutoMC's own template. A build script never runs transport: the run is planned and approved separately (OpenMCRunStartTool).
"""
import argparse

import openmc

# The choices a user may change: edit them here, or give them on the command line.
OPTIONS = {"energy_ev": 2.0e6, "size": "quick"}

R_WATER, R_WORLD = 10.0, 50.0             # cm: the water sphere and the vacuum around it
SIZES = {"quick": {"particles": 5_000, "batches": 10},
         "reference": {"particles": 100_000, "batches": 20}}


def materials():
    """Each material with its fractions (atom or weight, said), its density, and its S(a,b) table where atoms are bound."""
    water = openmc.Material(name="water")
    water.add_element("H", 2.0)
    water.add_element("O", 1.0)
    water.set_density("g/cm3", 1.0)
    water.add_s_alpha_beta("c_H_in_H2O")
    return {"water": water}


def geometry(mats):
    """Surfaces, then cells: every point in exactly one cell, a vacuum boundary outside."""
    inner = openmc.Sphere(r=R_WATER)
    outer = openmc.Sphere(r=R_WORLD, boundary_type="vacuum")
    water = openmc.Cell(name="water", fill=mats["water"], region=-inner)
    around = openmc.Cell(name="vacuum around", region=+inner & -outer)
    return openmc.Geometry([water, around])


def settings(energy_ev, size):
    run = openmc.Settings()
    run.run_mode = "fixed source"
    run.particles = SIZES[size]["particles"]
    run.batches = SIZES[size]["batches"]
    run.seed = 1
    run.source = openmc.IndependentSource(space=openmc.stats.Point((0.0, 0.0, 0.0)),
                                          energy=openmc.stats.Discrete([energy_ev], [1.0]))     # eV; strength 1: per source particle
    return run


def tallies(geom):
    water = next(c for c in geom.get_all_cells().values() if c.name == "water")
    flux = openmc.Tally(name="flux in the water")
    flux.filters = [openmc.CellFilter([water])]
    flux.scores = ["flux"]                                    # cm per source particle: divide by the volume for a flux
    return openmc.Tallies([flux])


def build(energy_ev=OPTIONS["energy_ev"], size=OPTIONS["size"]):
    mats = materials()
    geom = geometry(mats)
    return openmc.Model(geometry=geom, materials=openmc.Materials(mats.values()), settings=settings(energy_ev, size),
                        tallies=tallies(geom))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--energy-ev", type=float, default=OPTIONS["energy_ev"], help="source energy, eV")
    parser.add_argument("--size", choices=list(SIZES), default=OPTIONS["size"])
    a = parser.parse_args()
    build(a.energy_ev, a.size).export_to_model_xml()


if __name__ == "__main__":
    main()
