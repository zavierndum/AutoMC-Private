"""
A 17 x 17 PWR assembly (264 fuel pins of 1.6% UO2 and 25 water-filled guide tubes, mirrored on every side), run in continuous
energy with an openmc.mgxs.Library of two-group cross sections per material. run.py then writes the library as an MG cross section
file and runs the same assembly in multigroup mode with it, so the two eigenvalues can be compared.

Derived from: the OpenMC notebooks "MGXS Part III: Libraries" and "Multigroup Mode Part II: MGXS Library Generation with OpenMC"
(openmc-notebooks, tag v0.15.0; the assembly, the library's types, domains and Legendre order, the CE-against-MG comparison), with the
library form of "MDGXS Part II". Written for AutoMC; README.md explains each step.

    python build.py                          writes model.xml (the CE model with the library's tallies)
    python run.py                            CE run, library, MG run: a script run, approved by the user

Nothing here runs transport.
"""
import argparse

import numpy as np
import openmc
import openmc.mgxs as mgxs

# The choices of this example: edit them here, or give them on the command line. run.py reads them from here too.
OPTIONS = {"legendre_order": 3, "size": "quick"}

R_FUEL, R_CLAD, PITCH, N = 0.39218, 0.45720, 1.26, 17
R_TUBE_IN, R_TUBE_OUT = 0.50419, 0.54610          # guide tube radii (cm), the notebook's
# the 25 guide-tube positions of a 17 x 17 Westinghouse assembly (row, column), the centre one the instrument tube
TUBES = [(2, 5), (2, 8), (2, 11), (3, 3), (3, 13), (5, 2), (5, 5), (5, 8), (5, 11), (5, 14), (8, 2), (8, 5), (8, 8), (8, 11), (8, 14),
         (11, 2), (11, 5), (11, 8), (11, 11), (11, 14), (13, 3), (13, 13), (14, 5), (14, 8), (14, 11)]
SIZES = {"quick": {"particles": 3_000, "batches": 60, "inactive": 20},
         "reference": {"particles": 20_000, "batches": 220, "inactive": 20}}
TYPES = ["total", "absorption", "nu-fission", "fission", "nu-scatter matrix", "multiplicity matrix", "chi"]


def library(geometry, legendre_order=3):
    """The two-group MGXS library over the materials of `geometry`; build.py adds its tallies, run.py loads it."""
    lib = mgxs.Library(geometry)
    lib.energy_groups = mgxs.EnergyGroups([0.0, 0.625, 20.0e6])
    lib.mgxs_types = TYPES
    lib.domain_type = "material"
    lib.domains = list(geometry.get_all_materials().values())
    lib.by_nuclide = False
    lib.legendre_order = legendre_order
    lib.correction = None                          # keep the full P3 scattering, not a transport-corrected P0
    lib.check_library_for_openmc_mgxs()
    lib.build_library()
    return lib


def build_with_library(legendre_order=3, size="quick"):
    fuel = openmc.Material(name="fuel")
    for nuclide, n in (("U235", 3.7503e-4), ("U238", 2.2625e-2), ("O16", 4.6007e-2)):
        fuel.add_nuclide(nuclide, n)
    fuel.set_density("g/cm3", 10.31341)
    zircaloy = openmc.Material(name="zircaloy")
    zircaloy.add_nuclide("Zr90", 1.0)
    zircaloy.set_density("g/cm3", 6.55)
    water = openmc.Material(name="water")
    for nuclide, n in (("H1", 4.9457e-2), ("O16", 2.4732e-2), ("B10", 8.0042e-6)):
        water.add_nuclide(nuclide, n)
    water.set_density("g/cm3", 0.740582)
    water.add_s_alpha_beta("c_H_in_H2O")

    r_fuel, r_clad = openmc.ZCylinder(r=R_FUEL), openmc.ZCylinder(r=R_CLAD)
    pin = openmc.Universe(name="fuel pin", cells=[openmc.Cell(fill=fuel, region=-r_fuel),
                                                   openmc.Cell(fill=zircaloy, region=+r_fuel & -r_clad),
                                                   openmc.Cell(fill=water, region=+r_clad)])
    t_in, t_out = openmc.ZCylinder(r=R_TUBE_IN), openmc.ZCylinder(r=R_TUBE_OUT)
    tube = openmc.Universe(name="guide tube", cells=[openmc.Cell(fill=water, region=-t_in),
                                                     openmc.Cell(fill=zircaloy, region=+t_in & -t_out),
                                                     openmc.Cell(fill=water, region=+t_out)])
    half = N * PITCH / 2
    lattice = openmc.RectLattice(name="17 x 17 assembly")
    lattice.lower_left, lattice.pitch = (-half, -half), (PITCH, PITCH)
    grid = np.full((N, N), pin, dtype=object)
    for r, c in TUBES:
        grid[r, c] = tube
    lattice.universes = grid
    box = openmc.model.RectangularParallelepiped(-half, half, -half, half, -10.0, 10.0, boundary_type="reflective")
    geometry = openmc.Geometry([openmc.Cell(name="assembly", fill=lattice, region=-box)])

    settings = openmc.Settings()
    settings.run_mode = "eigenvalue"
    for key, value in SIZES[size].items():
        setattr(settings, key, value)
    settings.seed = 20261115
    settings.source = openmc.IndependentSource(space=openmc.stats.Box([-half, -half, -10.0], [half, half, 10.0]),
                                               constraints={"fissionable": True})
    settings.output = {"tallies": False}
    lib = library(geometry, legendre_order)
    tallies = openmc.Tallies()
    lib.add_to_tallies_file(tallies, merge=True)
    model = openmc.Model(geometry=geometry, materials=openmc.Materials([fuel, zircaloy, water]), settings=settings, tallies=tallies)
    return model, lib


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--legendre-order", type=int, default=OPTIONS["legendre_order"])
    parser.add_argument("--size", choices=list(SIZES), default=OPTIONS["size"])
    a = parser.parse_args()
    build_with_library(a.legendre_order, a.size)[0].export_to_model_xml()


if __name__ == "__main__":
    main()
