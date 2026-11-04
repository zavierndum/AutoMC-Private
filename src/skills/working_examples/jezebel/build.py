"""
Jezebel: a bare sphere of delta-phase plutonium alloy, the critical benchmark PU-MET-FAST-001 of the ICSBEP handbook.

Derived from: OpenMC's script example examples/jezebel (MIT licence) and the ICSBEP benchmark specification. Written for AutoMC;
README.md explains the benchmark and how the answer is judged.

    python build.py                      the defaults below; writes model.xml
    python build.py --size reference

Nothing here runs transport: the run is planned and approved separately.
"""
import argparse

import openmc

# The choices of this example: edit them here, or give them on the command line.
OPTIONS = {"size": "quick"}

RADIUS_CM = 6.3849                  # the benchmark's simplified sphere
# atom densities of the benchmark's homogenised alloy (atoms per barn-cm); gallium as the natural element
ATOM_DENSITIES = {"Pu239": 3.7047e-2, "Pu240": 1.7512e-3, "Pu241": 1.1674e-4}
GALLIUM = 1.3752e-3
SIZES = {"quick": {"particles": 2_000, "batches": 60, "inactive": 20},
         "reference": {"particles": 20_000, "batches": 220, "inactive": 20}}


def build(size="quick"):
    alloy = openmc.Material(name="plutonium-gallium alloy")
    for nuclide, density in ATOM_DENSITIES.items():
        alloy.add_nuclide(nuclide, density)
    alloy.add_element("Ga", GALLIUM)
    alloy.set_density("sum")                       # the total density is the sum of the atom densities given

    surface = openmc.Sphere(r=RADIUS_CM, boundary_type="vacuum")
    core = openmc.Cell(name="plutonium sphere", fill=alloy, region=-surface)

    settings = openmc.Settings()
    settings.run_mode = "eigenvalue"
    settings.particles = SIZES[size]["particles"]
    settings.batches = SIZES[size]["batches"]
    settings.inactive = SIZES[size]["inactive"]
    settings.seed = 20261101
    # start the fission source spread through the sphere, not at one point
    lo, hi = [-RADIUS_CM / 2] * 3, [RADIUS_CM / 2] * 3
    settings.source = openmc.IndependentSource(space=openmc.stats.Box(lo, hi), constraints={"fissionable": True})
    # the Shannon entropy of the fission source shows when the inactive batches have done their job
    entropy = openmc.RegularMesh()
    entropy.lower_left, entropy.upper_right, entropy.dimension = [-RADIUS_CM] * 3, [RADIUS_CM] * 3, [8, 8, 8]
    settings.entropy_mesh = entropy

    return openmc.Model(geometry=openmc.Geometry([core]), materials=openmc.Materials([alloy]), settings=settings)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--size", choices=list(SIZES), default=OPTIONS["size"])
    build(parser.parse_args().size).export_to_model_xml()


if __name__ == "__main__":
    main()
