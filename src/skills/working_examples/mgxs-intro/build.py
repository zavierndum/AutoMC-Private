"""
An infinite homogeneous mixture of fuel, water and zirconium, with the tallies of four two-group cross sections (total, absorption,
scattering, nu-fission) made by openmc.mgxs. post.py makes the same MGXS objects, loads them from the statepoint and reads them.

Derived from: the OpenMC notebook "MGXS Part I: Introduction" (openmc-notebooks, tag v0.15.0; the mixture, the groups and the cross
section types). Written for AutoMC; README.md explains how an MGXS object turns tallies into cross sections.

    python build.py                          the defaults below; writes model.xml
    python build.py --size reference

Nothing here runs transport.
"""
import argparse

import openmc
import openmc.mgxs as mgxs

# The choices of this example: edit them here, or give them on the command line.
OPTIONS = {"size": "quick"}

GROUP_EDGES = [0.0, 0.625, 20.0e6]                 # eV: thermal and fast
SIZES = {"quick": {"particles": 2_500, "batches": 50, "inactive": 10},
         "reference": {"particles": 25_000, "batches": 210, "inactive": 10}}
TYPES = {"total": lambda d, g: mgxs.TotalXS(domain=d, energy_groups=g),
         "absorption": lambda d, g: mgxs.AbsorptionXS(domain=d, energy_groups=g),
         "scatter": lambda d, g: mgxs.ScatterXS(domain=d, energy_groups=g),
         "nu-fission": lambda d, g: mgxs.FissionXS(domain=d, energy_groups=g, nu=True)}


def cross_sections(cell, edges=GROUP_EDGES):
    """The MGXS objects of this example for `cell`; build.py adds their tallies, post.py loads them from the statepoint."""
    groups = mgxs.EnergyGroups(group_edges=edges)
    return {name: make(cell, groups) for name, make in TYPES.items()}


def build(size="quick"):
    mix = openmc.Material(name="homogeneous mixture")
    for nuclide, n in (("H1", 0.028999667), ("O16", 0.01450188), ("U235", 0.000114142), ("U238", 0.006886019),
                       ("Zr90", 0.002116053)):
        mix.add_nuclide(nuclide, n)                 # relative amounts; the mass density sets the total
    mix.set_density("g/cm3", 5.0)
    mix.add_s_alpha_beta("c_H_in_H2O")
    sides = openmc.model.RectangularPrism(1.26, 1.26, boundary_type="reflective")
    cell = openmc.Cell(name="medium", fill=mix, region=-sides)       # mirrored sides and no ends: an infinite medium

    settings = openmc.Settings()
    settings.run_mode = "eigenvalue"
    for key, value in SIZES[size].items():
        setattr(settings, key, value)
    settings.seed = 20261112
    settings.source = openmc.IndependentSource(space=openmc.stats.Box([-0.63, -0.63, -1.0], [0.63, 0.63, 1.0]),
                                               constraints={"fissionable": True})
    tallies = openmc.Tallies()
    for xs in cross_sections(cell).values():
        tallies += xs.tallies.values()              # each MGXS object brings the tallies it needs
    return openmc.Model(geometry=openmc.Geometry([cell]), materials=openmc.Materials([mix]), settings=settings, tallies=tallies)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--size", choices=list(SIZES), default=OPTIONS["size"])
    a = parser.parse_args()
    build(a.size).export_to_model_xml()


if __name__ == "__main__":
    main()
