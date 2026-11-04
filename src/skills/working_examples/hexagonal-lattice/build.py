"""
A hexagonal lattice of two kinds of pins in water, inside a hexagonal prism: how rings, positions and orientations work.

Derived from: the OpenMC notebook "Modeling Hexagonal Lattices" (openmc-notebooks, tag v0.15.0; the lattice's layout and materials).
Written for AutoMC; README.md explains how a hexagonal lattice is filled.

    python build.py                          the defaults below; writes model.xml (geometry only: run_mode plot)
    python build.py --orientation y --boundary cylinder

Nothing here runs transport.
"""
import argparse

import openmc

# The choices of this example: edit them here, or give them on the command line.
OPTIONS = {"orientation": "x", "boundary": "hexagon", "rings": 4}

PITCH = 1.25                       # cm between neighbouring pin centres
R_SMALL, R_LARGE = 0.25, 0.50      # cm


def pin(fuel, water, radius, name):
    """A universe: a fuel cylinder of `radius` and water everywhere around it."""
    surface = openmc.ZCylinder(r=radius)
    return openmc.Universe(name=name, cells=[openmc.Cell(fill=fuel, region=-surface), openmc.Cell(fill=water, region=+surface)])


def rings_of(n_rings, small, large):
    """Ring lists from the outermost inwards, as OpenMC wants them: ring i (counting the centre as ring 0) has 6*i positions, the first
    of which gets the large pin, so that a straight line of large pins runs from the centre outwards; the centre is a large pin."""
    rings = []
    for i in range(n_rings - 1, 0, -1):
        rings.append([large] + [small] * (6 * i - 1))
    rings.append([large])
    return rings


def build(orientation="x", boundary="hexagon", rings=4):
    u235 = openmc.Material(name="U-235 fuel")
    u235.add_nuclide("U235", 1.0)
    u235.set_density("g/cm3", 10.0)
    u238 = openmc.Material(name="U-238 fuel")
    u238.add_nuclide("U238", 1.0)
    u238.set_density("g/cm3", 10.0)
    water = openmc.Material(name="water")
    water.add_nuclide("H1", 2.0)
    water.add_nuclide("O16", 1.0)
    water.set_density("g/cm3", 1.0)

    small = pin(u235, water, R_SMALL, "small pin (U-235)")
    large = pin(u238, water, R_LARGE, "large pin (U-238)")
    lattice = openmc.HexLattice(name="pin lattice")
    lattice.center = (0.0, 0.0)
    lattice.pitch = (PITCH,)
    lattice.orientation = orientation
    lattice.outer = openmc.Universe(name="water outside the lattice", cells=[openmc.Cell(fill=water)])
    lattice.universes = rings_of(rings, small, large)

    if boundary == "hexagon":
        # a hexagonal prism just around the outer ring; its edge runs parallel to the lattice rows of this orientation
        region = -openmc.model.HexagonalPrism(edge_length=rings * PITCH, orientation=orientation, boundary_type="vacuum")
    else:
        region = -openmc.ZCylinder(r=(rings + 0.5) * PITCH, boundary_type="vacuum")
    assembly = openmc.Cell(name="assembly", fill=lattice, region=region)

    settings = openmc.Settings()
    settings.run_mode = "plot"
    return openmc.Model(geometry=openmc.Geometry([assembly]), materials=openmc.Materials([u235, u238, water]), settings=settings)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--orientation", choices=["x", "y"], default=OPTIONS["orientation"])
    parser.add_argument("--boundary", choices=["hexagon", "cylinder"], default=OPTIONS["boundary"])
    parser.add_argument("--rings", type=int, default=OPTIONS["rings"])
    a = parser.parse_args()
    build(a.orientation, a.boundary, a.rings).export_to_model_xml()


if __name__ == "__main__":
    main()
