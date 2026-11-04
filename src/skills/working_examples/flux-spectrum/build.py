"""
The neutron flux spectrum of a PWR pin cell, in the fuel and in the water, on two energy grids: 500 equal-lethargy bins from 10 micro-eV
to 20 MeV, and the SHEM-361 group structure (fine around the low resonances, made for light-water reactor lattices).

Derived from: the OpenMC notebook "Tally a Flux Spectrum" (openmc-notebooks, tag v0.15.0; its model, OpenMC's built-in PWR pin cell,
and its two energy grids). Written for AutoMC; README.md explains spectra per unit energy and per unit lethargy.

    python build.py                          the defaults below; writes model.xml
    python build.py --size reference

Nothing here runs transport.
"""
import argparse

import numpy as np
import openmc
import openmc.examples
import openmc.mgxs

# The choices of this example: edit them here, or give them on the command line.
OPTIONS = {"bins": 500, "size": "quick"}

SIZES = {"quick": {"particles": 5_000, "batches": 40, "inactive": 10},
         "reference": {"particles": 50_000, "batches": 110, "inactive": 10}}


def build(bins=500, size="quick"):
    model = openmc.examples.pwr_pin_cell()          # 2.4% UO2 pin, zircaloy, hot borated water, mirrored sides (OpenMC's own)
    for key, value in SIZES[size].items():
        setattr(model.settings, key, value)
    model.settings.seed = 20261107
    cells = {c.name: c for c in model.geometry.get_all_cells().values()}
    where = openmc.CellFilter([cells["Fuel"], cells["Water"]])
    even = openmc.Tally(name="spectrum, equal lethargy")
    even.filters = [where, openmc.EnergyFilter(np.logspace(-5, np.log10(20.0e6), bins + 1))]
    even.scores = ["flux"]
    shem = openmc.Tally(name="spectrum, SHEM-361")
    shem.filters = [where, openmc.EnergyFilter(openmc.mgxs.GROUP_STRUCTURES["SHEM-361"])]
    shem.scores = ["flux"]
    model.tallies = openmc.Tallies([even, shem])
    return model


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--bins", type=int, default=OPTIONS["bins"], help="equal-lethargy bins")
    parser.add_argument("--size", choices=list(SIZES), default=OPTIONS["size"])
    a = parser.parse_args()
    build(a.bins, a.size).export_to_model_xml()


if __name__ == "__main__":
    main()
