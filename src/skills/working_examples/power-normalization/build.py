"""
A bare sphere of U-235 metal in a water reflector, with the tallies needed to turn OpenMC's per-source-neutron results into per-second
results at a known power: the fission rate in the fuel and the recoverable fission energy released.

Derived from: the OpenMC notebook "Tally Power Normalization" (openmc-notebooks, tag v0.15.0; the sphere, the reflector, the scores and
the 30 kW power). Written for AutoMC; README.md derives the normalization.

    python build.py                          the defaults below; writes model.xml
    python build.py --size reference

Nothing here runs transport.
"""
import argparse

import openmc

# The choices of this example: edit them here, or give them on the command line. post.py takes the power (--power-W).
OPTIONS = {"size": "quick"}

R_FUEL, R_WATER = 6.5, 15.0                        # cm
SIZES = {"quick": {"particles": 2_000, "batches": 60, "inactive": 10},
         "reference": {"particles": 20_000, "batches": 210, "inactive": 10}}


def build(size="quick"):
    u235 = openmc.Material(name="U-235 metal")
    u235.add_nuclide("U235", 1.0)
    u235.set_density("g/cm3", 10.0)
    water = openmc.Material(name="water")
    water.add_components({"H": 2.0, "O": 1.0})
    water.set_density("g/cm3", 1.0)
    water.add_s_alpha_beta("c_H_in_H2O")
    sphere, outside = openmc.Sphere(r=R_FUEL), openmc.Sphere(r=R_WATER, boundary_type="vacuum")
    fuel = openmc.Cell(name="fuel", fill=u235, region=-sphere)
    reflector = openmc.Cell(name="water reflector", fill=water, region=+sphere & -outside)

    settings = openmc.Settings()
    settings.run_mode = "eigenvalue"
    for key, value in SIZES[size].items():
        setattr(settings, key, value)
    settings.seed = 20261108
    settings.source = openmc.IndependentSource(space=openmc.stats.Point())

    fission = openmc.Tally(name="fission in the fuel")
    fission.filters = [openmc.CellFilter([fuel])]
    fission.scores = ["fission"]
    energy = openmc.Tally(name="energy released")
    energy.scores = ["fission-q-recoverable", "kappa-fission"]      # eV per source neutron, two ways of counting it
    return openmc.Model(geometry=openmc.Geometry([fuel, reflector]), materials=openmc.Materials([u235, water]), settings=settings,
                        tallies=openmc.Tallies([fission, energy]))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--size", choices=list(SIZES), default=OPTIONS["size"])
    a = parser.parse_args()
    build(a.size).export_to_model_xml()


if __name__ == "__main__":
    main()
