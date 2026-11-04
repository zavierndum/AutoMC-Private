"""
A PWR fuel pin to deplete: 4.25% UO2 in zircaloy and water, mirrored on its four sides, 1 cm of it (the fuel's volume is set, which
depletion needs to turn reaction rates into atoms). run.py burns it at 174 W for six 30-day steps.

Derived from: the OpenMC notebook "Pincell Depletion" (openmc-notebooks, tag v0.15.0; the pin, the power and the steps) and OpenMC's
script example `examples/pincell_depletion` (MIT; its teaching chain, chain_simple.xml, ships with this example). Written for AutoMC;
README.md explains depletion and the chain.

    python build.py                          writes model.xml (the fresh pin)
    python run.py                            the depletion: a script run, approved by the user (a transport run at every step)

Nothing here runs transport.
"""
import argparse
import math

import openmc

# The choices of this example: edit them here, or give them on the command line. run.py reads them from here too.
OPTIONS = {"power_W": 174.0, "steps_days": [30, 30, 30, 30, 30, 30], "integrator": "predictor", "size": "quick"}

R_FUEL, R_CLAD, PITCH = 0.42, 0.45, 1.24          # cm; no gap
SIZES = {"quick": {"particles": 1_000, "batches": 40, "inactive": 10},
         "reference": {"particles": 10_000, "batches": 110, "inactive": 10}}


def build(size="quick"):
    fuel = openmc.Material(name="UO2, 4.25% enriched")
    fuel.add_element("U", 1.0, percent_type="ao", enrichment=4.25)
    fuel.add_element("O", 2.0)
    fuel.set_density("g/cm3", 10.4)
    fuel.depletable = True
    fuel.volume = math.pi * R_FUEL ** 2                 # cm3 for 1 cm of pin: the power is then per cm too (174 W/cm)
    clad = openmc.Material(name="zircaloy (as zirconium)")
    clad.add_element("Zr", 1.0)
    clad.set_density("g/cm3", 6.0)
    water = openmc.Material(name="water")
    water.add_element("H", 2.0)
    water.add_element("O", 1.0)
    water.set_density("g/cm3", 1.0)
    water.add_s_alpha_beta("c_H_in_H2O")
    pin = openmc.model.pin([openmc.ZCylinder(r=R_FUEL), openmc.ZCylinder(r=R_CLAD)], [fuel, clad, water])
    box = openmc.model.RectangularPrism(PITCH, PITCH, boundary_type="reflective")
    root = openmc.Cell(name="pin cell", fill=pin, region=-box)

    settings = openmc.Settings()
    settings.run_mode = "eigenvalue"
    for key, value in SIZES[size].items():
        setattr(settings, key, value)
    settings.seed = 20261109
    settings.source = openmc.IndependentSource(space=openmc.stats.Box([-R_FUEL, -R_FUEL, -1.0], [R_FUEL, R_FUEL, 1.0]),
                                               constraints={"fissionable": True})
    return openmc.Model(geometry=openmc.Geometry([root]), materials=openmc.Materials([fuel, clad, water]), settings=settings)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--size", choices=list(SIZES), default=OPTIONS["size"])
    a = parser.parse_args()
    build(a.size).export_to_model_xml()


if __name__ == "__main__":
    main()
