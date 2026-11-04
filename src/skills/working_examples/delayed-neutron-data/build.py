"""
An infinite homogeneous mixture with U-235, U-238 and Pu-239, and the tallies of the delayed-neutron data a kinetics code needs, made
by openmc.mgxs: the delayed fraction beta and the precursor decay constants in six delayed groups, the delayed and prompt fission
spectra, and prompt, delayed and total nu-fission.

Derived from: the OpenMC notebook "MDGXS Part I: Introduction" (openmc-notebooks, tag v0.15.0; the mixture, the 100-group structure,
the six delayed groups and the cross section types; the library form of "MDGXS Part II" is shown in `mgxs-library-mg-mode`).
Written for AutoMC; README.md explains each quantity.

    python build.py                          the defaults below; writes model.xml
    python build.py --size reference

Nothing here runs transport.
"""
import argparse

import numpy as np
import openmc
import openmc.mgxs as mgxs

# The choices of this example: edit them here, or give them on the command line.
OPTIONS = {"size": "quick"}

DELAYED = [1, 2, 3, 4, 5, 6]
SIZES = {"quick": {"particles": 5_000, "batches": 50, "inactive": 10},
         "reference": {"particles": 50_000, "batches": 110, "inactive": 10}}


def cross_sections(cell):
    """The delayed-neutron MGXS objects for `cell`; build.py adds their tallies, post.py loads them from the statepoint."""
    fine = mgxs.EnergyGroups(group_edges=np.logspace(-3, 7.3, 101))             # 100 groups, 1 meV to 20 MeV
    one = mgxs.EnergyGroups(group_edges=[fine.group_edges[0], fine.group_edges[-1]])
    return {"beta": mgxs.Beta(domain=cell, energy_groups=one, delayed_groups=DELAYED, by_nuclide=True),
            "decay rate": mgxs.DecayRate(domain=cell, energy_groups=one, delayed_groups=DELAYED, by_nuclide=True),
            "chi prompt": mgxs.Chi(domain=cell, energy_groups=fine, prompt=True),
            "chi delayed": mgxs.ChiDelayed(domain=cell, energy_groups=fine),
            "prompt nu-fission": mgxs.FissionXS(domain=cell, energy_groups=one, nu=True, prompt=True),
            "delayed nu-fission": mgxs.DelayedNuFissionXS(domain=cell, energy_groups=one, delayed_groups=DELAYED),
            "nu-fission": mgxs.FissionXS(domain=cell, energy_groups=one, nu=True)}


def build(size="quick"):
    mix = openmc.Material(name="homogeneous mixture")
    for nuclide, n in (("H1", 0.03), ("O16", 0.015), ("U235", 0.0001), ("U238", 0.007), ("Pu239", 0.00003), ("Zr90", 0.002)):
        mix.add_nuclide(nuclide, n)                 # relative amounts; the mass density sets the total
    mix.set_density("g/cm3", 5.0)
    mix.add_s_alpha_beta("c_H_in_H2O")
    sides = openmc.model.RectangularPrism(1.26, 1.26, boundary_type="reflective")
    cell = openmc.Cell(name="medium", fill=mix, region=-sides)

    settings = openmc.Settings()
    settings.run_mode = "eigenvalue"
    for key, value in SIZES[size].items():
        setattr(settings, key, value)
    settings.seed = 20261114
    settings.source = openmc.IndependentSource(space=openmc.stats.Box([-0.63, -0.63, -1.0], [0.63, 0.63, 1.0]),
                                               constraints={"fissionable": True})
    tallies = openmc.Tallies()
    for xs in cross_sections(cell).values():
        tallies += xs.tallies.values()
    return openmc.Model(geometry=openmc.Geometry([cell]), materials=openmc.Materials([mix]), settings=settings, tallies=tallies)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--size", choices=list(SIZES), default=OPTIONS["size"])
    a = parser.parse_args()
    build(a.size).export_to_model_xml()


if __name__ == "__main__":
    main()
