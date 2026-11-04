"""
Neutron flux and effective dose in a water sphere from an isotropic point source.

Derived from: the AutoMC developers' toy problem (a point source in water) and a user's question of 2026-11-01 (a 2.5 cm water sphere
15 cm from a point source). Written for AutoMC; README.md explains the physics and how to read the answer.

    python build.py                          the defaults below; writes model.xml
    python build.py --layout centre          the toy: the source at the centre of a 50 cm water sphere
    python build.py --fill void --size reference

Nothing here runs transport: the run is planned and approved separately.
"""
import argparse

import openmc

# The choices of this example: edit them here, or give them on the command line.
OPTIONS = {"layout": "offset", "fill": "water", "energy": "watt", "size": "quick"}

# offset: a small target sphere at a distance from the source, in vacuum. centre: the source inside a large sphere.
LAYOUTS = {
    "offset": {"target_radius": 2.5, "target_x": 15.0, "world_radius": 30.0},
    "centre": {"target_radius": 50.0, "target_x": 0.0, "world_radius": None},
}
# histories per layout: quick for checking a build, reference for an answer. The offset sphere catches about 0.7% of the source
# neutrons, so it needs many; inside the large sphere every neutron counts, and each one takes many collisions.
SIZES = {"offset": {"quick": {"particles": 20_000, "batches": 5}, "reference": {"particles": 1_000_000, "batches": 10}},
         "centre": {"quick": {"particles": 2_000, "batches": 5}, "reference": {"particles": 100_000, "batches": 10}}}


def water():
    h2o = openmc.Material(name="water")
    h2o.add_nuclide("H1", 2.0)
    h2o.add_nuclide("O16", 1.0)
    h2o.set_density("g/cm3", 1.0)
    h2o.add_s_alpha_beta("c_H_in_H2O")          # bound hydrogen: matters once neutrons slow down
    return h2o


def source_energy(choice):
    """watt: a U-235 thermal-fission spectrum (OpenMC's default Watt parameters); a number: a single energy in MeV."""
    if choice == "watt":
        return openmc.stats.Watt(a=0.988e6, b=2.249e-6)
    return openmc.stats.Discrete([float(choice) * 1.0e6], [1.0])


def build(layout="offset", fill="water", energy="watt", size="quick"):
    shape = LAYOUTS[layout]
    target_material = water() if fill == "water" else None
    a, x0 = shape["target_radius"], shape["target_x"]

    if shape["world_radius"] is None:
        # the target is the whole problem: neutrons that leave it are gone
        surface = openmc.Sphere(x0=x0, r=a, boundary_type="vacuum")
        target = openmc.Cell(name="target sphere", fill=target_material, region=-surface)
        cells = [target]
    else:
        surface = openmc.Sphere(x0=x0, r=a)
        world = openmc.Sphere(r=shape["world_radius"], boundary_type="vacuum")
        target = openmc.Cell(name="target sphere", fill=target_material, region=-surface)
        around = openmc.Cell(name="vacuum around the target", region=+surface & -world)
        cells = [target, around]

    settings = openmc.Settings()
    settings.run_mode = "fixed source"
    settings.particles = SIZES[layout][size]["particles"]
    settings.batches = SIZES[layout][size]["batches"]
    settings.seed = 20261101
    settings.source = openmc.IndependentSource(space=openmc.stats.Point((0.0, 0.0, 0.0)), angle=openmc.stats.Isotropic(),
                                               energy=source_energy(energy), particle="neutron")

    # A cell tally of flux is the track length in the cell per source neutron (cm per source neutron); folding it with the ICRP-116
    # fluence-to-effective-dose coefficients (pSv cm2, the AP irradiation geometry) gives pSv cm3 per source neutron.
    in_target = openmc.CellFilter([target])
    flux = openmc.Tally(name="track length")
    flux.filters = [in_target]
    flux.scores = ["flux"]
    energies, coefficients = openmc.data.dose_coefficients(particle="neutron", geometry="AP")
    dose = openmc.Tally(name="effective dose")
    dose.filters = [in_target, openmc.EnergyFunctionFilter(energies, coefficients)]
    dose.scores = ["flux"]

    # OpenMC wants at least one material defined, even when every cell is void
    materials = openmc.Materials([target_material or water()])
    return openmc.Model(geometry=openmc.Geometry(cells), materials=materials, settings=settings, tallies=openmc.Tallies([flux, dose]))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--layout", choices=list(LAYOUTS), default=OPTIONS["layout"])
    parser.add_argument("--fill", choices=["water", "void"], default=OPTIONS["fill"])
    parser.add_argument("--energy", default=OPTIONS["energy"], help="watt, or one energy in MeV such as 2.0")
    parser.add_argument("--size", choices=["quick", "reference"], default=OPTIONS["size"])
    a = parser.parse_args()
    build(a.layout, a.fill, a.energy, a.size).export_to_model_xml()


if __name__ == "__main__":
    main()
