"""
A 1 m section of a PWR pin cut into ten axial zones, each with its own fuel, gap, cladding and water cells and its own water material, so that
openmc.lib can change one zone's temperature or density while OpenMC runs. The heat each fuel zone receives (fission-q-recoverable)
is tallied. run.py runs it twice in one OpenMC session: as built (fuel 800 K, water 0.70 g/cm3 everywhere), then after the changes a
thermal-hydraulics code would hand back (FEEDBACK: hotter fuel, water denser at the bottom than at the top).

Derived from: the OpenMC notebook "Using the C/C++ API" (openmc-notebooks, tag v0.15.0; the pin, its ten zones, the materials and
temperatures, the heat tally, and the calls that change temperatures and densities; its pin is 3 m tall, this one 1 m, so that the
fission source converges in the batches run). Written for AutoMC; README.md explains the coupling.

    python build.py                          the defaults below; writes model.xml
    python run.py                            the two states in one session: a script run, approved by the user

Nothing here runs transport.
"""
import argparse

import openmc

# The choices of this example: edit them here, or give them on the command line. run.py reads them from here too.
OPTIONS = {"size": "quick"}

# 1 m, not the notebook's 3 m: in a 3 m pin the ratio of the first two axial modes is about 0.98, so an axial tilt of the fission
# source dies out only after a few hundred batches; at 1 m it is about 0.88 and 30 inactive batches are enough
PITCH, HEIGHT, ZONES = 1.25984, 100.0, 10
R_FUEL, R_GAP, R_CLAD = 0.39218, 0.40005, 0.45720
TEMPERATURES = {"fuel": 800.0, "gap": 700.0, "cladding": 600.0, "water": 500.0}       # K, as built
WATER_DENSITY = 0.70                                                                  # g/cm3, as built, every zone
# What run.py applies before its second state: the fuel 100 K hotter; the water heated along the channel, denser at the bottom
# (zone 1) than at the top (zone 10) with the same average
FEEDBACK = {"fuel_K": 900.0, "water_bottom": 0.74, "water_top": 0.66}
SIZES = {"quick": {"particles": 2_000, "batches": 70, "inactive": 30},
         "reference": {"particles": 20_000, "batches": 230, "inactive": 30}}


def zone_heights():
    """(bottom, top) of each zone, zone 1 at the bottom."""
    dz = HEIGHT / ZONES
    return [(i * dz, (i + 1) * dz) for i in range(ZONES)]


def feedback_densities():
    """The water density of each zone after the feedback, linear in height from bottom to top."""
    lo, hi = FEEDBACK["water_bottom"], FEEDBACK["water_top"]
    return [lo + (hi - lo) * i / (ZONES - 1) for i in range(ZONES)]


def build(size="quick"):
    uo2 = openmc.Material(name="UO2, 2.4 wt%")
    uo2.set_density("g/cm3", 10.29769)
    uo2.add_element("U", 1.0, enrichment=2.4)
    uo2.add_element("O", 2.0)
    helium = openmc.Material(name="helium")
    helium.set_density("g/cm3", 0.001598)
    helium.add_element("He", 1.0)
    zircaloy = openmc.Material(name="Zircaloy-4")
    zircaloy.set_density("g/cm3", 6.55)
    for element, w in (("Sn", 0.014), ("Fe", 0.00165), ("Cr", 0.001), ("Zr", 0.98335)):
        zircaloy.add_element(element, w, "wo")
    waters = []
    for i in range(ZONES):                          # one material per zone: openmc.lib changes densities by material
        w = openmc.Material(name=f"water zone {i + 1}")
        w.set_density("g/cm3", WATER_DENSITY)
        w.add_element("H", 2.0)
        w.add_element("O", 1.0)
        w.add_s_alpha_beta("c_H_in_H2O")
        waters.append(w)

    fuel_r, gap_r, clad_r = (openmc.ZCylinder(r=r) for r in (R_FUEL, R_GAP, R_CLAD))
    box = openmc.model.RectangularPrism(PITCH, PITCH, boundary_type="reflective")
    planes = [openmc.ZPlane(z0=z) for z in [b for b, _ in zone_heights()] + [HEIGHT]]
    planes[0].boundary_type = planes[-1].boundary_type = "vacuum"                         # the pin's ends leak
    cells = []
    for i in range(ZONES):
        slab = +planes[i] & -planes[i + 1]
        for name, fill, region in (("fuel", uo2, -fuel_r), ("gap", helium, +fuel_r & -gap_r), ("cladding", zircaloy, +gap_r & -clad_r),
                                   ("water", waters[i], +clad_r & -box)):
            cell = openmc.Cell(name=f"{name} zone {i + 1}", fill=fill, region=region & slab)
            cell.temperature = TEMPERATURES[name]
            cells.append(cell)

    settings = openmc.Settings()
    settings.run_mode = "eigenvalue"
    for key, value in SIZES[size].items():
        setattr(settings, key, value)
    settings.seed = 20261118
    # data loaded for every temperature in the range, so a cell can be moved to any of them while OpenMC runs
    settings.temperature = {"method": "interpolation", "range": [250.0, 1200.0]}
    settings.source = openmc.IndependentSource(space=openmc.stats.Box([-R_FUEL, -R_FUEL, 0.0], [R_FUEL, R_FUEL, HEIGHT]),
                                               constraints={"fissionable": True})
    heat = openmc.Tally(name="heat by zone")
    heat.filters = [openmc.CellFilter([c for c in cells if c.name.startswith("fuel")])]
    heat.scores = ["fission-q-recoverable"]
    return openmc.Model(geometry=openmc.Geometry(cells), materials=openmc.Materials([uo2, helium, zircaloy] + waters),
                        settings=settings, tallies=openmc.Tallies([heat]))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--size", choices=list(SIZES), default=OPTIONS["size"])
    a = parser.parse_args()
    build(a.size).export_to_model_xml()


if __name__ == "__main__":
    main()
