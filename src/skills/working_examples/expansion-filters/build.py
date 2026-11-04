"""
Flux shapes as functional expansions instead of bins: Legendre polynomials along z, Zernike polynomials over a disc. Two layouts:

- `column`: a 10 x 10 cm column of UO2, 20 cm tall, mirrored on its sides and open at both ends, with a 1 cm slab of boron carbide
  across its middle; the axial flux as a Legendre expansion, and as a fine z mesh to compare with;
- `pellet`: a bare UO2 pellet (r = 0.5 cm, 2 cm tall, vacuum all round); the radial flux as a radial Zernike expansion, the flux over
  the disc as a full Zernike expansion, and a radial mesh to compare with.

Derived from: the OpenMC notebook "Functional Expansions" (openmc-notebooks, tag v0.15.0; both geometries, their materials and the
expansion orders). Written for AutoMC; README.md explains how a reconstruction is done.

    python build.py                          the defaults below; writes model.xml
    python build.py --layout pellet

Nothing here runs transport.
"""
import argparse

import openmc

# The choices of this example: edit them here, or give them on the command line.
OPTIONS = {"layout": "column", "size": "quick"}

SIZES = {"column": {"quick": {"particles": 2_000, "batches": 60, "inactive": 10},
                    "reference": {"particles": 20_000, "batches": 210, "inactive": 10}},
         "pellet": {"quick": {"particles": 10_000, "batches": 40, "inactive": 10},
                    "reference": {"particles": 100_000, "batches": 100, "inactive": 20}}}
COLUMN = {"side": 10.0, "zmin": -10.0, "zmax": 10.0, "absorber": 0.5, "order": 8, "mesh": 80}
PELLET = {"radius": 0.5, "zmin": -1.0, "zmax": 1.0, "order": 10, "rings": 25}


def uo2(enrichment):
    m = openmc.Material(name=f"UO2, {enrichment}% enriched")
    m.add_element("U", 1.0, enrichment=enrichment)
    m.add_nuclide("O16", 2.0)
    m.set_density("g/cm3", 10.0)
    return m


def column(settings):
    c = COLUMN
    fuel = uo2(4.5)
    b4c = openmc.Material(name="boron carbide")
    b4c.add_element("B", 4.0)
    b4c.add_element("C", 1.0)
    b4c.set_density("g/cm3", 2.5)
    sides = openmc.model.RectangularPrism(c["side"], c["side"], boundary_type="reflective")
    bottom, top = openmc.ZPlane(c["zmin"], boundary_type="vacuum"), openmc.ZPlane(c["zmax"], boundary_type="vacuum")
    lower, upper = openmc.ZPlane(-c["absorber"]), openmc.ZPlane(c["absorber"])
    cells = [openmc.Cell(name="fuel below", fill=fuel, region=-sides & +bottom & -lower),
             openmc.Cell(name="boron carbide", fill=b4c, region=-sides & +lower & -upper),
             openmc.Cell(name="fuel above", fill=fuel, region=-sides & +upper & -top)]
    h = c["side"] / 2
    settings.source = openmc.IndependentSource(space=openmc.stats.Box([-h, -h, c["zmin"]], [h, h, c["zmax"]]),
                                               constraints={"fissionable": True})
    legendre = openmc.Tally(name="axial flux, Legendre")
    legendre.filters = [openmc.SpatialLegendreFilter(c["order"], "z", c["zmin"], c["zmax"])]
    legendre.scores = ["flux"]
    zmesh = openmc.RegularMesh(name="z slices")
    zmesh.lower_left, zmesh.upper_right, zmesh.dimension = [-h, -h, c["zmin"]], [h, h, c["zmax"]], [1, 1, c["mesh"]]
    binned = openmc.Tally(name="axial flux, mesh")
    binned.filters = [openmc.MeshFilter(zmesh)]
    binned.scores = ["flux"]
    binned.estimator = "collision"          # expansion tallies are collision estimates; the same estimator makes the two comparable
    return cells, [fuel, b4c], [legendre, binned]


def pellet(settings):
    p = PELLET
    fuel = uo2(5.0)
    side = openmc.ZCylinder(r=p["radius"], boundary_type="vacuum")
    bottom, top = openmc.ZPlane(p["zmin"], boundary_type="vacuum"), openmc.ZPlane(p["zmax"], boundary_type="vacuum")
    cells = [openmc.Cell(name="pellet", fill=fuel, region=-side & +bottom & -top)]
    r = p["radius"]
    settings.source = openmc.IndependentSource(space=openmc.stats.Box([-r, -r, p["zmin"]], [r, r, p["zmax"]]),
                                               constraints={"fissionable": True})
    radial = openmc.Tally(name="radial flux, Zernike")
    radial.filters = [openmc.ZernikeRadialFilter(order=p["order"], x=0.0, y=0.0, r=r)]
    radial.scores = ["flux"]
    disc = openmc.Tally(name="flux over the disc, Zernike")
    disc.filters = [openmc.ZernikeFilter(order=p["order"], x=0.0, y=0.0, r=r)]
    disc.scores = ["flux"]
    rings = openmc.CylindricalMesh(r_grid=[r * i / p["rings"] for i in range(p["rings"] + 1)], z_grid=[p["zmin"], p["zmax"]],
                                   phi_grid=[0.0, 6.283185307179586])
    rings.name = "rings"
    binned = openmc.Tally(name="radial flux, mesh")
    binned.filters = [openmc.MeshFilter(rings)]
    binned.scores = ["flux"]
    binned.estimator = "collision"
    return cells, [fuel], [radial, disc, binned]


def build(layout="column", size="quick"):
    settings = openmc.Settings()
    settings.run_mode = "eigenvalue"
    for key, value in SIZES[layout][size].items():
        setattr(settings, key, value)
    settings.seed = 20261106
    cells, materials, tallies = (column if layout == "column" else pellet)(settings)
    return openmc.Model(geometry=openmc.Geometry(cells), materials=openmc.Materials(materials), settings=settings,
                        tallies=openmc.Tallies(tallies))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--layout", choices=["column", "pellet"], default=OPTIONS["layout"])
    parser.add_argument("--size", choices=["quick", "reference"], default=OPTIONS["size"])
    a = parser.parse_args()
    build(a.layout, a.size).export_to_model_xml()


if __name__ == "__main__":
    main()
