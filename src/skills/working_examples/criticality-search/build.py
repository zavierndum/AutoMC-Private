"""
A PWR fuel pin whose coolant carries dissolved boron, as a function of the boron concentration: the model that run.py searches over for
the concentration that makes the pin exactly critical.

Derived from: the OpenMC notebook "Criticality Search" (openmc-notebooks, tag v0.15.0; the pin, its materials and the search bracket).
Written for AutoMC; README.md explains the physics and the boron unit.

    python build.py                          writes model.xml at the OPTIONS boron, for plotting and checking
    python run.py                            the search itself: a script run, approved by the user (it runs transport many times)

Nothing here runs transport.
"""
import argparse

import openmc
import openmc.data

# The choices of this example: edit them here, or give them on the command line. run.py reads them from here too.
OPTIONS = {"boron_ppm": 1000.0, "bracket_ppm": [500.0, 2500.0], "tolerance": 0.01, "enrichment": 1.6, "size": "quick"}

R_FUEL, R_CLAD, PITCH = 0.39218, 0.45720, 1.26     # cm; the fuel-clad gap is not modelled
WATER_DENSITY = 0.741                               # g/cm3: water at about 300 C and 15.5 MPa, as in an operating PWR
SIZES = {"quick": {"particles": 1_000, "batches": 80, "inactive": 20},
         "reference": {"particles": 10_000, "batches": 220, "inactive": 20}}


def borated_water(boron_ppm):
    """Light water with natural boron at `boron_ppm` parts per million BY WEIGHT, the unit plant chemistry uses (the other atoms of
    boric acid are neglected). Weight fractions are declared as such ("wo")."""
    w_boron = boron_ppm * 1e-6
    a_h, a_o = openmc.data.atomic_weight("H"), openmc.data.atomic_weight("O")
    molecule = 2 * a_h + a_o
    water = openmc.Material(name=f"water, {boron_ppm:.0f} ppm boron")
    water.add_element("H", (1.0 - w_boron) * 2 * a_h / molecule, "wo")
    water.add_element("O", (1.0 - w_boron) * a_o / molecule, "wo")
    if w_boron > 0:
        water.add_element("B", w_boron, "wo")
    water.set_density("g/cm3", WATER_DENSITY)
    water.add_s_alpha_beta("c_H_in_H2O")
    return water


def build(boron_ppm=1000.0, enrichment=1.6, size="quick"):
    fuel = openmc.Material(name=f"UO2, {enrichment}% enriched")
    fuel.add_element("U", 1.0, enrichment=enrichment)          # enrichment: weight percent U-235
    fuel.add_element("O", 2.0)
    fuel.set_density("g/cm3", 10.31341)
    clad = openmc.Material(name="zircaloy (as zirconium)")
    clad.add_element("Zr", 1.0)
    clad.set_density("g/cm3", 6.55)
    water = borated_water(boron_ppm)

    pellet = openmc.ZCylinder(r=R_FUEL)
    clad_out = openmc.ZCylinder(r=R_CLAD)
    square = openmc.model.RectangularPrism(width=PITCH, height=PITCH, boundary_type="reflective")
    cells = [openmc.Cell(name="fuel", fill=fuel, region=-pellet),
             openmc.Cell(name="cladding", fill=clad, region=+pellet & -clad_out),
             openmc.Cell(name="borated water", fill=water, region=+clad_out & -square)]

    settings = openmc.Settings()
    settings.run_mode = "eigenvalue"
    for key, value in SIZES[size].items():
        setattr(settings, key, value)
    settings.seed = 20261102
    settings.source = openmc.IndependentSource(space=openmc.stats.Box([-R_FUEL, -R_FUEL, -1.0], [R_FUEL, R_FUEL, 1.0]),
                                               constraints={"fissionable": True})
    settings.output = {"tallies": False}
    return openmc.Model(geometry=openmc.Geometry(cells), materials=openmc.Materials([fuel, clad, water]), settings=settings)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--boron-ppm", type=float, default=OPTIONS["boron_ppm"])
    parser.add_argument("--enrichment", type=float, default=OPTIONS["enrichment"], help="weight percent U-235")
    parser.add_argument("--size", choices=list(SIZES), default=OPTIONS["size"])
    a = parser.parse_args()
    build(a.boron_ppm, a.enrichment, a.size).export_to_model_xml()


if __name__ == "__main__":
    main()
