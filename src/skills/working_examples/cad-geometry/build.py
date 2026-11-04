"""
CAD geometry in OpenMC through DAGMC: a surface mesh made in a CAD program (an .h5m file) used as a universe, its volumes given
materials by name. Three models (OPTIONS["model"]):

- `pin`: a uranium cylinder in a water annulus, the CAD file as the whole geometry (eigenvalue). run_pin.py also runs its exact CSG
  twin (PIN_CSG), so the two can be compared;
- `teapot`: an iron teapot holding water, with a source only in the water (fixed source) and the flux tallied in each;
- `tokamak`: half a tokamak (the Paramak model, cut at y = 0), a 14 MeV ring source in its plasma, the blanket's flux tallied (fixed
  source). run_tokamak.py also runs it inside CSG boxes that mirror it at the cut, as a half and as a quarter.

The CAD files come from the data cache (`python -m automc data fetch cad-geometry`); build.py copies the one it needs into the case
and names it by a relative path, so every run folder carries it.

Derived from: the OpenMC notebook "DAGMC's CAD-Based Geometry in OpenMC" (openmc-notebooks, tag v0.15.0; the three CAD models, their
materials, the sources and the tallies; the pin's CSG twin and the mirrored half are added here). Written for AutoMC; README.md
explains each.

    python build.py                          the defaults below; writes model.xml and copies the CAD file
    python build.py --model tokamak

Nothing here runs transport.
"""
import argparse
import hashlib
import math
import os
import shutil
from pathlib import Path

import openmc

# The choices of this example: edit them here, or give them on the command line. run_pin.py and run_tokamak.py read them too.
OPTIONS = {"model": "pin", "size": "quick"}

# The files this needs from the data cache (src/skills/working_examples/data.json), with their SHA-256
DATA = {"cad/fuel_pin.h5m": "e65ec96039d68a03cda5f885cd4999dbf7722205d7b6fa88153eeddfae71933f",
        "cad/teapot.h5m": "66754a4a9a06a6add7cee8262765bddc7a8f7ed8cb15f5321d7b94a0a6929b74",
        "cad/paramak_with_graveyard.h5m": "796c4773652144a9af31cc98479648846bfdd57328da3d441e159a618c1ea1e6"}
FILES = {"pin": "cad/fuel_pin.h5m", "teapot": "cad/teapot.h5m", "tokamak": "cad/paramak_with_graveyard.h5m"}
# The CAD pin, measured by point queries (2026-11-02): fuel r = 9 cm, water to r = 11 cm, both from z = -20 to +20 cm, nothing else
PIN_CSG = {"fuel_r": 9.0, "water_r": 11.0, "half_height": 20.0}
SIZES = {"pin": {"quick": {"particles": 5_000, "batches": 50, "inactive": 10},
                 "reference": {"particles": 50_000, "batches": 220, "inactive": 20}},
         "teapot": {"quick": {"particles": 5_000, "batches": 10}, "reference": {"particles": 50_000, "batches": 20}},
         "tokamak": {"quick": {"particles": 5_000, "batches": 10}, "reference": {"particles": 50_000, "batches": 20}}}
# The tokamak's materials: one per CAD volume tag (name, element or formula, density g/cm3)
TOKAMAK = [("pf_coil_1", "Cu", 8.96), ("pf_coil_2", "Cu", 8.96), ("pf_coil_3", "Cu", 8.96), ("pf_coil_4", "Cu", 8.96),
           ("plasma", "H", 1e-5), ("inboard_tf_coils", "Cu", 8.96), ("center_column_shield", "W", 19.3), ("firstwall", "Fe", 7.7),
           ("blanket", "Pb842Li158", 19.0), ("blanket_rear_wall", "Fe", 7.7), ("divertor_upper", "W", 19.3),
           ("divertor_lower", "W", 19.3), ("pf_coil_case_1", "Fe", 8.96), ("pf_coil_case_2", "Fe", 8.96),
           ("pf_coil_case_3", "Fe", 8.96), ("pf_coil_case_4", "Fe", 8.96)]
TOKAMAK_EXTENT = 1061.0                 # cm: the graveyard's half width in x and z; in y it runs from -768.5 to 1353.5


def data_file(rel):
    """Copy a file of the data cache (AUTOMC_DATA_CACHE, else ~/.cache/automc/data) into the case, checked against its SHA-256;
    returns its name in the case."""
    path = Path(os.environ.get("AUTOMC_DATA_CACHE") or Path.home() / ".cache" / "automc" / "data").expanduser() / rel
    if not path.is_file():
        raise SystemExit(f"{rel} is not in the data cache ({path.parent}); fetch it with `python -m automc data fetch cad-geometry`")
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    if h.hexdigest() != DATA[rel]:
        raise SystemExit(f"{path} is not the file this example was checked with; fetch it again with `python -m automc data fetch "
                         f"{rel}`")
    name = Path(rel).name
    if not Path(name).is_file():
        shutil.copyfile(path, name)
    return name


def water_material(material_id=None):
    water = openmc.Material(material_id=material_id, name="water")
    water.add_nuclide("H1", 2.0)
    water.add_nuclide("O16", 1.0)
    water.set_density("g/cm3", 1.0)
    water.add_s_alpha_beta("c_H_in_H2O")
    return water


def pin_materials():
    fuel = openmc.Material(name="fuel")
    fuel.add_nuclide("U235", 1.0)
    fuel.set_density("g/cm3", 11.0)
    # the CAD file tags the pin's water volume "41": a numeric tag is matched to a material's id, a name to its name
    return fuel, water_material(41)


def pin_csg(fuel, water):
    """The CAD pin as CSG: the same cylinders, with vacuum where the CAD model has empty space and its graveyard."""
    d = PIN_CSG
    fuel_r, water_r = openmc.ZCylinder(r=d["fuel_r"]), openmc.ZCylinder(r=d["water_r"], boundary_type="vacuum")
    top = openmc.ZPlane(d["half_height"], boundary_type="vacuum")
    bottom = openmc.ZPlane(-d["half_height"], boundary_type="vacuum")
    slab = +bottom & -top
    return openmc.Geometry([openmc.Cell(name="fuel", fill=fuel, region=-fuel_r & slab),
                            openmc.Cell(name="water", fill=water, region=+fuel_r & -water_r & slab)])


def tokamak_materials():
    out = []
    for name, what, density in TOKAMAK:
        m = openmc.Material(name=name)
        if what.isalpha():
            m.add_element(what, 1.0)
        else:
            m.add_elements_from_formula(what)
        m.set_density("g/cm3", density)
        out.append(m)
    return openmc.Materials(out)


def ring_source():
    """14 MeV fusion neutrons (a Muir spectrum) born on a ring of radius 250 cm in the plasma, over the half model's 180 degrees."""
    src = openmc.IndependentSource()
    src.space = openmc.stats.CylindricalIndependent(r=openmc.stats.Discrete([250.0], [1.0]),
                                                    phi=openmc.stats.Uniform(0.0, math.pi), z=openmc.stats.Discrete([0.0], [1.0]))
    src.angle = openmc.stats.Isotropic()
    src.energy = openmc.stats.muir(e0=14.0e6, m_rat=5.0, kt=20000.0)
    return src


def tokamak_geometry(cad, frame):
    """The CAD half tokamak as published (frame 'half'), or inside a CSG box: mirrored at its cut y = 0 ('mirrored'), or cut
    again at x = 0 and mirrored there too ('quarter')."""
    if frame == "half":
        return openmc.Geometry(cad)
    cad.auto_geom_ids = True                                    # CSG and CAD cells and surfaces must not share ids
    L = TOKAMAK_EXTENT
    x_lo = openmc.XPlane(0.0 if frame == "quarter" else -L, boundary_type="reflective" if frame == "quarter" else "vacuum")
    region = (+x_lo & -openmc.XPlane(L, boundary_type="vacuum") & +openmc.YPlane(0.0, boundary_type="reflective")
              & -openmc.YPlane(1353.5, boundary_type="vacuum") & +openmc.ZPlane(-L, boundary_type="vacuum")
              & -openmc.ZPlane(L, boundary_type="vacuum"))
    return openmc.Geometry([openmc.Cell(name=f"{frame} frame", fill=cad, region=region)])


def build(model="pin", size="quick", frame="half"):
    s = SIZES[model][size]
    settings = openmc.Settings()
    for key, value in s.items():
        setattr(settings, key, value)
    settings.seed = 20261120
    tallies = openmc.Tallies()
    if model == "pin":
        fuel, water = pin_materials()
        materials = openmc.Materials([fuel, water])
        geometry = openmc.Geometry(openmc.DAGMCUniverse(data_file(FILES["pin"]))) if frame != "csg" else pin_csg(fuel, water)
        settings.run_mode = "eigenvalue"
        settings.source = openmc.IndependentSource(space=openmc.stats.Box([-4.0] * 3, [4.0] * 3), constraints={"fissionable": True})
    elif model == "teapot":
        iron = openmc.Material(name="iron")
        for nuclide, f in (("Fe54", 0.0564555822608), ("Fe56", 0.919015287728), ("Fe57", 0.0216036861685),
                           ("Fe58", 0.00292544384231)):
            iron.add_nuclide(nuclide, f)
        iron.set_density("g/cm3", 7.874)
        water = water_material()
        materials = openmc.Materials([iron, water])
        cad = openmc.DAGMCUniverse(data_file(FILES["teapot"]))
        geometry = openmc.Geometry(cad)
        settings.run_mode = "fixed source"
        # 12 eV neutrons born only in the water: a box around it (measured by point queries), the points in other cells sampled again
        settings.source = openmc.IndependentSource(space=openmc.stats.Box([-10.0, -10.0, -9.0], [12.5, 10.0, 0.0]),
                                                   energy=openmc.stats.Discrete([12.0], [1.0]), constraints={"domains": [water]})
        for mat in (iron, water):
            t = openmc.Tally(name=f"flux in the {mat.name}")
            t.filters = [openmc.MaterialFilter([mat])]
            t.scores = ["flux"]
            tallies.append(t)
    else:
        materials = tokamak_materials()
        geometry = tokamak_geometry(openmc.DAGMCUniverse(data_file(FILES["tokamak"])), frame)
        settings.run_mode = "fixed source"
        settings.source = ring_source()
        blanket = next(m for m in materials if m.name == "blanket")
        t = openmc.Tally(name="blanket flux")
        t.filters = [openmc.MaterialFilter([blanket])]
        t.scores = ["flux"]
        tallies.append(t)
    return openmc.Model(geometry=geometry, materials=materials, settings=settings, tallies=tallies)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", choices=list(FILES), default=OPTIONS["model"])
    parser.add_argument("--size", choices=["quick", "reference"], default=OPTIONS["size"])
    a = parser.parse_args()
    build(a.model, a.size).export_to_model_xml()


if __name__ == "__main__":
    main()
