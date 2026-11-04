"""
A CANDU 37-element fuel bundle in its pressure tube and calandria tube: k-eff, and the flux in each of the 37 pins by ring.

Derived from: the OpenMC notebook "Modeling a CANDU Bundle" (openmc-notebooks, tag v0.15.0; the bundle's radii, pin rings and
materials). Written for AutoMC; README.md explains the two layouts.

    python build.py                           the defaults below; writes model.xml
    python build.py --layout channel          the notebook's geometry: the channel alone, vacuum around it

Nothing here runs transport: the run is planned and approved separately.
"""
import argparse
import math

import openmc

# The choices of this example: edit them here, or give them on the command line.
OPTIONS = {"layout": "lattice", "size": "quick"}

R_FUEL, R_CLAD = 0.6122, 0.6540                         # cm, pellet and sheath
RING_RADII = [0.0, 1.4885, 2.8755, 4.3305]              # cm, from the bundle axis to each ring's pin centres
RING_PINS = [1, 6, 12, 18]                              # 37 pins
RING_OFFSET_DEG = [0.0, 0.0, 15.0, 0.0]                 # the third ring is turned by half a pin spacing
PRESSURE_TUBE = (5.16890, 5.60320)                      # cm, inner and outer radius
CALANDRIA_TUBE = (6.44780, 6.58750)
LATTICE_PITCH = 28.575                                  # cm, the square lattice of channels in the calandria
SIZES = {"quick": {"particles": 2_000, "batches": 60, "inactive": 20},
         "reference": {"particles": 20_000, "batches": 220, "inactive": 20}}


def materials():
    fuel = openmc.Material(name="natural UO2")
    fuel.add_element("U", 1.0)
    fuel.add_element("O", 2.0)
    fuel.set_density("g/cm3", 10.0)
    zircaloy = openmc.Material(name="zircaloy")
    zircaloy.add_element("Zr", 1.0)
    zircaloy.set_density("g/cm3", 6.0)
    d2o = openmc.Material(name="heavy water")
    d2o.add_nuclide("H2", 2.0)
    d2o.add_nuclide("O16", 1.0)
    d2o.set_density("g/cm3", 1.1)
    d2o.add_s_alpha_beta("c_D_in_D2O")
    return fuel, zircaloy, d2o


def bundle(fuel, zircaloy, coolant):
    """The 37 pins in coolant: one universe. Each pin is its own cell (named by ring and position) filled with the pin universe."""
    pellet = openmc.ZCylinder(r=R_FUEL)
    pin = openmc.Universe(name="pin", cells=[openmc.Cell(name="fuel", fill=fuel, region=-pellet),
                                             openmc.Cell(name="sheath", fill=zircaloy, region=+pellet)])
    # the coolant is split into annuli between the rings, so each annulus only has to exclude the pins it holds
    bounds = [openmc.ZCylinder(r=(a + b) / 2) for a, b in zip(RING_RADII[:-1], RING_RADII[1:])]
    coolant_cells = []
    for i in range(len(RING_RADII)):
        region = (-bounds[0] if i == 0 else +bounds[-1] if i == len(RING_RADII) - 1 else +bounds[i - 1] & -bounds[i])
        coolant_cells.append(openmc.Cell(name=f"coolant around ring {i}", fill=coolant, region=region))
    pins = []
    for ring, (r, n, offset) in enumerate(zip(RING_RADII, RING_PINS, RING_OFFSET_DEG)):
        for k in range(n):
            angle = math.radians(offset + 360.0 * k / n)
            x, y = r * math.cos(angle), r * math.sin(angle)
            outline = openmc.ZCylinder(x0=x, y0=y, r=R_CLAD)
            coolant_cells[ring].region &= +outline
            cell = openmc.Cell(name=f"pin ring {ring} position {k}", fill=pin, region=-outline)
            cell.translation = (x, y, 0.0)
            pins.append(cell)
    return openmc.Universe(name="37-element bundle", cells=coolant_cells + pins)


def build(layout="lattice", size="quick"):
    fuel, zircaloy, d2o = materials()
    inner_bundle = bundle(fuel, zircaloy, d2o)
    pt_in, pt_out = (openmc.ZCylinder(r=r) for r in PRESSURE_TUBE)
    ct_in = openmc.ZCylinder(r=CALANDRIA_TUBE[0])
    cells = [openmc.Cell(name="bundle", fill=inner_bundle, region=-pt_in),
             openmc.Cell(name="pressure tube", fill=zircaloy, region=+pt_in & -pt_out),
             openmc.Cell(name="gas annulus", region=+pt_out & -ct_in)]
    if layout == "channel":
        ct_out = openmc.ZCylinder(r=CALANDRIA_TUBE[1], boundary_type="vacuum")
        cells.append(openmc.Cell(name="calandria tube", fill=zircaloy, region=+ct_in & -ct_out))
    else:
        ct_out = openmc.ZCylinder(r=CALANDRIA_TUBE[1])
        square = openmc.model.RectangularPrism(width=LATTICE_PITCH, height=LATTICE_PITCH, boundary_type="reflective")
        cells += [openmc.Cell(name="calandria tube", fill=zircaloy, region=+ct_in & -ct_out),
                  openmc.Cell(name="moderator", fill=d2o, region=+ct_out & -square)]

    settings = openmc.Settings()
    settings.run_mode = "eigenvalue"
    for key, value in SIZES[size].items():
        setattr(settings, key, value)
    settings.seed = 20261101
    edge = RING_RADII[-1] + R_CLAD
    settings.source = openmc.IndependentSource(space=openmc.stats.Box([-edge, -edge, -1.0], [edge, edge, 1.0]),
                                               constraints={"fissionable": True})

    per_pin = openmc.Tally(name="flux in each pin's fuel")
    fuel_cell = next(c for c in inner_bundle.get_all_cells().values() if c.name == "fuel")
    per_pin.filters = [openmc.DistribcellFilter(fuel_cell)]
    per_pin.scores = ["flux"]
    return openmc.Model(geometry=openmc.Geometry(cells), materials=openmc.Materials([fuel, zircaloy, d2o]), settings=settings,
                        tallies=openmc.Tallies([per_pin]))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--layout", choices=["lattice", "channel"], default=OPTIONS["layout"])
    parser.add_argument("--size", choices=list(SIZES), default=OPTIONS["size"])
    a = parser.parse_args()
    build(a.layout, a.size).export_to_model_xml()


if __name__ == "__main__":
    main()
