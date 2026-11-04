"""
A 2 MeV neutron beam through a water slab and then a concrete slab: the fluence profile along the beam.

Derived from: the AutoMC developers' toy problem (a beam through water and concrete). Written for AutoMC; README.md explains the physics.

    python build.py                      the defaults below; writes model.xml
    python build.py --size reference

Nothing here runs transport: the run is planned and approved separately.
"""
import argparse

import openmc

# The choices of this example: edit them here, or give them on the command line.
OPTIONS = {"water_cm": 10.0, "concrete_cm": 20.0, "energy_MeV": 2.0, "bins": 20, "size": "quick"}

SIZES = {"quick": {"particles": 5_000, "batches": 5}, "reference": {"particles": 200_000, "batches": 10}}
HALF_WIDTH = 50.0          # cm; the sides reflect, so the slabs act as if they were infinite across the beam

# Ordinary concrete by weight fraction (the toy problem's composition), 2.3 g/cm3
CONCRETE = {"H": 0.010, "O": 0.529, "Si": 0.337, "Ca": 0.044, "Al": 0.034, "Fe": 0.012, "Na": 0.017, "K": 0.013, "Mg": 0.002}


def materials():
    water = openmc.Material(name="water")
    water.add_nuclide("H1", 2.0)
    water.add_nuclide("O16", 1.0)
    water.set_density("g/cm3", 1.0)
    water.add_s_alpha_beta("c_H_in_H2O")
    concrete = openmc.Material(name="concrete")
    for element, fraction in CONCRETE.items():
        concrete.add_element(element, fraction, percent_type="wo")
    concrete.set_density("g/cm3", 2.3)
    return water, concrete


def build(water_cm=10.0, concrete_cm=20.0, energy_MeV=2.0, bins=20, size="quick"):
    water, concrete = materials()
    front = openmc.XPlane(0.0, boundary_type="vacuum")
    interface = openmc.XPlane(water_cm)
    back = openmc.XPlane(water_cm + concrete_cm, boundary_type="vacuum")
    sides = (+openmc.YPlane(-HALF_WIDTH, boundary_type="reflective") & -openmc.YPlane(HALF_WIDTH, boundary_type="reflective")
             & +openmc.ZPlane(-HALF_WIDTH, boundary_type="reflective") & -openmc.ZPlane(HALF_WIDTH, boundary_type="reflective"))
    water_slab = openmc.Cell(name="water slab", fill=water, region=+front & -interface & sides)
    concrete_slab = openmc.Cell(name="concrete slab", fill=concrete, region=+interface & -back & sides)

    settings = openmc.Settings()
    settings.run_mode = "fixed source"
    settings.particles = SIZES[size]["particles"]
    settings.batches = SIZES[size]["batches"]
    settings.seed = 20261101
    # a pencil beam just inside the front face, heading along +x; with reflecting sides it stands for a broad, uniform beam
    settings.source = openmc.IndependentSource(space=openmc.stats.Point((1.0e-3, 0.0, 0.0)),
                                               angle=openmc.stats.Monodirectional((1.0, 0.0, 0.0)),
                                               energy=openmc.stats.Discrete([energy_MeV * 1.0e6], [1.0]))

    mesh = openmc.RegularMesh(name="along the beam")
    mesh.dimension = [bins, 1, 1]
    mesh.lower_left = [0.0, -HALF_WIDTH, -HALF_WIDTH]
    mesh.upper_right = [water_cm + concrete_cm, HALF_WIDTH, HALF_WIDTH]
    profile = openmc.Tally(name="fluence profile")
    profile.filters = [openmc.MeshFilter(mesh)]
    profile.scores = ["flux"]
    fast = openmc.Tally(name="fast fluence profile")        # above 0.1 MeV: the part that is still close to the beam's energy
    fast.filters = [openmc.MeshFilter(mesh), openmc.EnergyFilter([1.0e5, 2.0e7])]
    fast.scores = ["flux"]

    return openmc.Model(geometry=openmc.Geometry([water_slab, concrete_slab]), materials=openmc.Materials([water, concrete]),
                        settings=settings, tallies=openmc.Tallies([profile, fast]))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--water-cm", type=float, default=OPTIONS["water_cm"])
    parser.add_argument("--concrete-cm", type=float, default=OPTIONS["concrete_cm"])
    parser.add_argument("--energy-MeV", type=float, default=OPTIONS["energy_MeV"])
    parser.add_argument("--bins", type=int, default=OPTIONS["bins"])
    parser.add_argument("--size", choices=list(SIZES), default=OPTIONS["size"])
    a = parser.parse_args()
    build(a.water_cm, a.concrete_cm, a.energy_MeV, a.bins, a.size).export_to_model_xml()


if __name__ == "__main__":
    main()
