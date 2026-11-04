"""
The 1.6% PWR pin, 100 cm tall, mirrored on its four sides and open (vacuum) at both ends, with the tallies that the six-factor formula
needs: production, absorption, the thermal parts, and the leakage through the ends. post.py combines them by tally arithmetic.

Derived from: the OpenMC notebook "Tally Arithmetic" (openmc-notebooks, tag v0.15.0; its pin, energy cut, tallies and factors). Written
for AutoMC; README.md explains the formula and why the ends are open here.

    python build.py                          the defaults below; writes model.xml
    python build.py --height 200 --size reference

Nothing here runs transport.
"""
import argparse

import openmc

# The choices of this example: edit them here, or give them on the command line.
OPTIONS = {"height_cm": 100.0, "size": "quick"}

R_FUEL, R_CLAD, PITCH = 0.39218, 0.45720, 1.26
THERMAL_EV = 0.625                                  # the thermal cut of two-group reactor physics
SIZES = {"quick": {"particles": 4_000, "batches": 50, "inactive": 10},
         "reference": {"particles": 40_000, "batches": 160, "inactive": 10}}


def pin_materials():
    """The notebooks' 1.6% pin (atom densities as relative amounts, mass densities as totals; cladding as Zr-90)."""
    fuel = openmc.Material(name="UO2, 1.6% enriched")
    for nuclide, n in (("U235", 3.7503e-4), ("U238", 2.2625e-2), ("O16", 4.6007e-2)):
        fuel.add_nuclide(nuclide, n)
    fuel.set_density("g/cm3", 10.31341)
    water = openmc.Material(name="borated water")
    for nuclide, n in (("H1", 4.9457e-2), ("O16", 2.4732e-2), ("B10", 8.0042e-6)):
        water.add_nuclide(nuclide, n)
    water.set_density("g/cm3", 0.740582)
    water.add_s_alpha_beta("c_H_in_H2O")
    clad = openmc.Material(name="zircaloy (as Zr-90)")
    clad.add_nuclide("Zr90", 1.0)
    clad.set_density("g/cm3", 6.55)
    return fuel, clad, water


def tally(name, scores, filters=(), nuclides=None):
    t = openmc.Tally(name=name)
    t.filters, t.scores = list(filters), scores
    if nuclides:
        t.nuclides = nuclides
    return t


def build(height_cm=100.0, size="quick"):
    fuel, clad, water = pin_materials()
    h, z = PITCH / 2, height_cm / 2
    sides = openmc.model.RectangularPrism(width=PITCH, height=PITCH, boundary_type="reflective")
    bottom, top = openmc.ZPlane(-z, boundary_type="vacuum"), openmc.ZPlane(z, boundary_type="vacuum")
    inside = -sides & +bottom & -top
    pellet, clad_out = openmc.ZCylinder(r=R_FUEL), openmc.ZCylinder(r=R_CLAD)
    fuel_cell = openmc.Cell(name="fuel", fill=fuel, region=-pellet & inside)
    clad_cell = openmc.Cell(name="cladding", fill=clad, region=+pellet & -clad_out & inside)
    water_cell = openmc.Cell(name="water", fill=water, region=+clad_out & inside)

    settings = openmc.Settings()
    settings.run_mode = "eigenvalue"
    for key, value in SIZES[size].items():
        setattr(settings, key, value)
    settings.seed = 20261104
    settings.source = openmc.IndependentSource(space=openmc.stats.Box([-R_FUEL, -R_FUEL, -z], [R_FUEL, R_FUEL, z]),
                                               constraints={"fissionable": True})

    groups = openmc.EnergyFilter([0.0, THERMAL_EV, 20.0e6])
    thermal = openmc.EnergyFilter([0.0, THERMAL_EV])
    fuel_only = openmc.CellFilter([fuel_cell])
    # one mesh cell exactly around the pin: its surface currents give what leaves through the ends
    box = openmc.RegularMesh(name="the pin's outline")
    box.lower_left, box.upper_right, box.dimension = [-h, -h, -z], [h, h, z], [1, 1, 1]
    surfaces = openmc.MeshSurfaceFilter(box)
    tallies = [
        tally("production", ["nu-fission"]),
        tally("absorption", ["absorption"]),
        tally("leakage", ["current"], [surfaces]),
        tally("thermal leakage", ["current"], [surfaces, thermal]),
        tally("thermal absorption", ["absorption"], [thermal]),
        tally("thermal production", ["nu-fission"], [thermal]),
        tally("fuel thermal absorption", ["absorption"], [thermal, fuel_only]),
        tally("flux by group", ["flux"], [openmc.CellFilter([fuel_cell, water_cell]), groups]),
        tally("fuel reaction rates", ["fission", "nu-fission", "scatter"], [fuel_only, groups], nuclides=["U235", "U238"]),
    ]
    return openmc.Model(geometry=openmc.Geometry([fuel_cell, clad_cell, water_cell]), materials=openmc.Materials([fuel, clad, water]),
                        settings=settings, tallies=openmc.Tallies(tallies))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--height", type=float, default=OPTIONS["height_cm"], help="pin height in cm (vacuum at both ends)")
    parser.add_argument("--size", choices=list(SIZES), default=OPTIONS["size"])
    a = parser.parse_args()
    build(a.height, a.size).export_to_model_xml()


if __name__ == "__main__":
    main()
