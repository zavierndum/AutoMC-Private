"""
A sodium iodide scintillation detector in aluminium, with an aluminium-oxide reflector and an iron back, seen by two photon sources: a
beam of 800 keV photons along its axis and a gram of Cs-137 just in front of it. A pulse-height tally gives the spectrum of energy
deposited in the crystal per photon, as the detector would record it before its own resolution blurs it (post.py adds that).

Derived from: the OpenMC notebook "Simulating the Signal of a Gamma Detector" (openmc-notebooks, tag v0.15.0; the detector's geometry
and materials, the two sources and their strengths, the tally and the resolution model). Written for AutoMC; README.md explains the
spectrum's features.

    python build.py                          the defaults below; writes model.xml
    python build.py --size reference

Nothing here runs transport.
"""
import argparse
import math

import openmc

# The choices of this example: edit them here, or give them on the command line.
OPTIONS = {"size": "quick"}

# Cs-137 and its daughter Ba-137m in equilibrium: per Cs-137 decay, 0.851 photons of 661.657 keV (94.4% of decays feed Ba-137m, and
# 90.1% of its transitions emit the gamma rather than a conversion electron); the barium X-rays near 32 keV are left out here. With a
# depletion chain that carries decay photon data, openmc.data.decay_photon_energy("Cs137") and ("Ba137_m1") give the full spectra.
CS_LINE_EV, CS_PHOTONS_PER_DECAY = 661_657.0, 0.851
CS_HALF_LIFE_S, CS_GRAMS = 30.17 * 365.25 * 86400, 1.0
BEAM_EV, BEAM_PER_S = 800_000.0, 1.0e12
SIZES = {"quick": {"particles": 20_000, "batches": 10}, "reference": {"particles": 400_000, "batches": 10}}


def build(size="quick"):
    aluminium = openmc.Material(name="aluminium")
    aluminium.add_element("Al", 1.0)
    aluminium.set_density("g/cm3", 2.7)
    nai = openmc.Material(name="sodium iodide")
    nai.add_element("Na", 1.0)
    nai.add_element("I", 1.0)
    nai.set_density("g/cm3", 3.667)
    oxide = openmc.Material(name="aluminium oxide")
    oxide.add_nuclide("O16", 0.4)
    oxide.add_element("Al", 0.6)
    oxide.set_density("g/cm3", 3.97)
    iron = openmc.Material(name="iron")
    iron.add_element("Fe", 1.0)
    iron.set_density("g/cm3", 7.874)

    # cylinders: the crystal (a 3-inch NaI), the reflector around it, the casing; planes along the axis from the front face
    r_crystal, r_reflector, r_casing = (openmc.ZCylinder(r=r) for r in (3.81, 3.86, 3.91))
    front, front_reflector, crystal_front, crystal_back, back = (openmc.ZPlane(z) for z in (0.0, 0.05, 0.10, 7.72, 8.00))
    world = openmc.Sphere(r=11.0, boundary_type="vacuum")
    crystal = openmc.Cell(name="NaI crystal", fill=nai, region=-r_crystal & +crystal_front & -crystal_back)
    casing = openmc.Cell(name="aluminium casing", fill=aluminium,
                         region=(+r_reflector & -r_casing & +front_reflector & -crystal_back) | (-r_casing & +front & -front_reflector))
    reflector = openmc.Cell(name="oxide reflector", fill=oxide,
                            region=(+r_crystal & -r_reflector & +front_reflector & -crystal_back) |
                                   (-r_reflector & +front_reflector & -crystal_front))
    back_cell = openmc.Cell(name="iron back", fill=iron, region=-r_casing & +crystal_back & -back)
    detector = -r_casing & +front & -back
    around = openmc.Cell(name="vacuum around", region=-world & ~detector)

    beam = openmc.IndependentSource(space=openmc.stats.Point((0.0, 0.0, -10.0)), angle=openmc.stats.Monodirectional([0.0, 0.0, 1.0]),
                                    energy=openmc.stats.Discrete([BEAM_EV], [1.0]), particle="photon", strength=BEAM_PER_S)
    decays = math.log(2) / CS_HALF_LIFE_S * CS_GRAMS / 137.0 * 6.02214076e23          # Cs-137 activity, Bq
    cs = openmc.IndependentSource(space=openmc.stats.Point((0.0, 0.0, -1.0)), angle=openmc.stats.Isotropic(),
                                  energy=openmc.stats.Discrete([CS_LINE_EV], [1.0]), particle="photon",
                                  strength=decays * CS_PHOTONS_PER_DECAY)
    settings = openmc.Settings()
    settings.run_mode = "fixed source"
    settings.photon_transport = True
    for key, value in SIZES[size].items():
        setattr(settings, key, value)
    settings.seed = 20261110
    settings.source = [beam, cs]                 # sampled in proportion to their strengths (photons per second)

    tally = openmc.Tally(name="pulse height")
    tally.filters = [openmc.CellFilter([crystal]), openmc.EnergyFilter([1000.0 * i for i in range(1001)])]   # 1 keV bins to 1 MeV
    tally.scores = ["pulse-height"]
    return openmc.Model(geometry=openmc.Geometry([crystal, casing, reflector, back_cell, around]),
                        materials=openmc.Materials([aluminium, nai, oxide, iron]), settings=settings, tallies=openmc.Tallies([tally]))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--size", choices=list(SIZES), default=OPTIONS["size"])
    a = parser.parse_args()
    build(a.size).export_to_model_xml()


if __name__ == "__main__":
    main()
