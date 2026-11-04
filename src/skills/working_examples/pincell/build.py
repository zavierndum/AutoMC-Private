"""
A PWR fuel pin in its square of water, reflected on all four sides: k-infinity and the U-235 reaction rates in the fuel.

Derived from: the OpenMC notebook "Modeling a Pin-Cell" (openmc-notebooks, tag v0.15.0; the problem's dimensions and compositions).
Written for AutoMC; README.md explains the physics.

    python build.py                      the defaults below; writes model.xml
    python build.py --enrichment 4.5 --size reference

Nothing here runs transport: the run is planned and approved separately.
"""
import argparse

import openmc

# The choices of this example: edit them here, or give them on the command line.
OPTIONS = {"enrichment": 3.0, "size": "quick"}

# cm: fuel pellet, cladding inner and outer radius, lattice pitch (a typical 17x17 PWR pin)
R_FUEL, R_CLAD_IN, R_CLAD_OUT, PITCH = 0.39, 0.40, 0.46, 1.26
SIZES = {"quick": {"particles": 1_000, "batches": 60, "inactive": 20},
         "reference": {"particles": 20_000, "batches": 220, "inactive": 20}}


def materials(enrichment):
    """UO2 with U-235 at `enrichment` percent of the uranium atoms; zirconium cladding; light water with bound hydrogen."""
    fuel = openmc.Material(name="uranium dioxide")
    fuel.add_nuclide("U235", enrichment / 100.0)
    fuel.add_nuclide("U238", 1.0 - enrichment / 100.0)
    fuel.add_nuclide("O16", 2.0)
    fuel.set_density("g/cm3", 10.0)
    clad = openmc.Material(name="zirconium")
    clad.add_element("Zr", 1.0)
    clad.set_density("g/cm3", 6.6)
    water = openmc.Material(name="light water")
    water.add_nuclide("H1", 2.0)
    water.add_element("O", 1.0)
    water.set_density("g/cm3", 1.0)
    water.add_s_alpha_beta("c_H_in_H2O")
    return fuel, clad, water


def build(enrichment=3.0, size="quick"):
    fuel, clad, water = materials(enrichment)
    pellet = openmc.ZCylinder(r=R_FUEL)
    clad_in = openmc.ZCylinder(r=R_CLAD_IN)
    clad_out = openmc.ZCylinder(r=R_CLAD_OUT)
    square = openmc.model.RectangularPrism(width=PITCH, height=PITCH, boundary_type="reflective")
    cells = [openmc.Cell(name="fuel", fill=fuel, region=-pellet),
             openmc.Cell(name="gap", region=+pellet & -clad_in),                      # helium gap, left empty
             openmc.Cell(name="cladding", fill=clad, region=+clad_in & -clad_out),
             openmc.Cell(name="moderator", fill=water, region=+clad_out & -square)]

    settings = openmc.Settings()
    settings.run_mode = "eigenvalue"
    for key, value in SIZES[size].items():
        setattr(settings, key, value)
    settings.seed = 20261101
    settings.source = openmc.IndependentSource(space=openmc.stats.Box([-R_FUEL, -R_FUEL, -1.0], [R_FUEL, R_FUEL, 1.0]),
                                               constraints={"fissionable": True})

    rates = openmc.Tally(name="U-235 in the fuel")
    rates.filters = [openmc.CellFilter([cells[0]])]
    rates.nuclides = ["U235"]
    rates.scores = ["total", "fission", "absorption", "(n,gamma)"]
    return openmc.Model(geometry=openmc.Geometry(cells), materials=openmc.Materials([fuel, clad, water]), settings=settings,
                        tallies=openmc.Tallies([rates]))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--enrichment", type=float, default=OPTIONS["enrichment"], help="U-235 atom percent of the uranium")
    parser.add_argument("--size", choices=list(SIZES), default=OPTIONS["size"])
    a = parser.parse_args()
    build(a.enrichment, a.size).export_to_model_xml()


if __name__ == "__main__":
    main()
