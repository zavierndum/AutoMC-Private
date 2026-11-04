"""
The 1.6% PWR pin with eight-group cross sections in each of its cells: transport, fission, nu-fission, the nu-scattering matrix and
the fission spectrum chi, by nuclide in the fuel, with tally triggers on their precision; and a two-group fission cross section
tallied directly, to check condensation against.

Derived from: the OpenMC notebook "MGXS Part II: Advanced Features" (openmc-notebooks, tag v0.15.0; the pin, the 8- and 2-group
structures, the cross section types and the trigger). Written for AutoMC; README.md explains each feature.

    python build.py                          the defaults below; writes model.xml
    python build.py --size reference

Nothing here runs transport.
"""
import argparse

import openmc
import openmc.mgxs as mgxs

# The choices of this example: edit them here, or give them on the command line.
OPTIONS = {"size": "quick"}

R_FUEL, R_CLAD, PITCH = 0.39218, 0.45720, 1.26
FINE = [0.0, 0.058, 0.14, 0.28, 0.625, 4.0, 5.53e3, 821.0e3, 20.0e6]      # eV, 8 groups
COARSE = [0.0, 0.625, 20.0e6]
# batches: the minimum; the triggers add batches (up to max_batches) until every MGXS tally reaches the standard deviation asked
SIZES = {"quick": {"particles": 5_000, "batches": 30, "inactive": 10, "max_batches": 60, "std_dev": 5e-2},
         "reference": {"particles": 10_000, "batches": 50, "inactive": 10, "max_batches": 200, "std_dev": 1e-2}}


def pin_materials():
    """The notebooks' 1.6% pin (atom densities as relative amounts, mass densities as totals; cladding as Zr-90)."""
    fuel = openmc.Material(name="UO2, 1.6% enriched")
    for nuclide, n in (("U235", 3.7503e-4), ("U238", 2.2625e-2), ("O16", 4.6007e-2)):
        fuel.add_nuclide(nuclide, n)
    fuel.set_density("g/cm3", 10.31341)
    water = openmc.Material(name="water")
    for nuclide, n in (("H1", 4.9457e-2), ("O16", 2.4732e-2)):
        water.add_nuclide(nuclide, n)
    water.set_density("g/cm3", 0.740582)
    water.add_s_alpha_beta("c_H_in_H2O")
    clad = openmc.Material(name="zircaloy (as Zr-90)")
    clad.add_nuclide("Zr90", 1.0)
    clad.set_density("g/cm3", 6.55)
    return fuel, clad, water


def cross_sections(cells, trigger=None):
    """{cell name: {type: MGXS}} for the cells; build.py adds their tallies, post.py loads them from the statepoint."""
    fine, coarse = mgxs.EnergyGroups(FINE), mgxs.EnergyGroups(COARSE)
    out = {}
    for cell in cells:
        fuel = cell.name == "fuel"
        lib = {"transport": mgxs.TransportXS(domain=cell, energy_groups=fine),
               "fission": mgxs.FissionXS(domain=cell, energy_groups=fine, by_nuclide=fuel),
               "nu-fission": mgxs.FissionXS(domain=cell, energy_groups=fine, nu=True, by_nuclide=fuel),
               "nu-scatter matrix": mgxs.ScatterMatrixXS(domain=cell, energy_groups=fine, nu=True)}
        if fuel:
            lib["chi"] = mgxs.Chi(domain=cell, energy_groups=fine)
            lib["fission, 2 groups"] = mgxs.FissionXS(domain=cell, energy_groups=coarse)
        for xs in lib.values():
            if trigger is not None:
                xs.tally_trigger = trigger
        out[cell.name] = lib
    return out


def build(size="quick"):
    s = SIZES[size]
    fuel, clad, water = pin_materials()
    pellet, clad_out = openmc.ZCylinder(r=R_FUEL), openmc.ZCylinder(r=R_CLAD)
    h = PITCH / 2
    cube = openmc.model.RectangularParallelepiped(-h, h, -h, h, -h, h, boundary_type="reflective")
    cells = [openmc.Cell(name="fuel", fill=fuel, region=-pellet & -cube),
             openmc.Cell(name="cladding", fill=clad, region=+pellet & -clad_out & -cube),
             openmc.Cell(name="water", fill=water, region=+clad_out & -cube)]

    settings = openmc.Settings()
    settings.run_mode = "eigenvalue"
    settings.particles, settings.batches, settings.inactive = s["particles"], s["batches"], s["inactive"]
    settings.trigger_active, settings.trigger_max_batches, settings.trigger_batch_interval = True, s["max_batches"], 5
    settings.seed = 20261113
    settings.source = openmc.IndependentSource(space=openmc.stats.Box([-R_FUEL, -R_FUEL, -h], [R_FUEL, R_FUEL, h]),
                                               constraints={"fissionable": True})
    tallies = openmc.Tallies()
    for lib in cross_sections(cells, trigger=openmc.Trigger("std_dev", s["std_dev"])).values():
        for xs in lib.values():
            tallies += xs.tallies.values()
    return openmc.Model(geometry=openmc.Geometry(cells), materials=openmc.Materials([fuel, clad, water]), settings=settings,
                        tallies=tallies)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--size", choices=list(SIZES), default=OPTIONS["size"])
    a = parser.parse_args()
    build(a.size).export_to_model_xml()


if __name__ == "__main__":
    main()
