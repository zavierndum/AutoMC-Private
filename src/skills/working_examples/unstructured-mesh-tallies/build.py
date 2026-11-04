"""
An unstructured (tetrahedral) mesh tally: OpenMC's PWR assembly example cut to 20 cm, with flux and heating scored on a tet mesh of four
of its pin channels (MOAB, from the data cache). The same four channels are also tallied on exact cylinders (one CylindricalMesh
each), so the tet mesh's answer can be checked against an independent one.

Derived from: the OpenMC notebook "Unstructured Mesh: Introduction" (openmc-notebooks, tag v0.15.0; the assembly, its 20 cm cut, the
vacuum sides, the pin mesh and the scores; the cylinder tallies are added here). The mesh's libMesh form is not used: this OpenMC
build has no libMesh. Written for AutoMC; README.md explains the mesh and the comparison.

    python build.py                          the defaults below; writes model.xml and copies the mesh file
    python build.py --size reference

Nothing here runs transport.
"""
import argparse
import hashlib
import os
import shutil
from pathlib import Path

import openmc
import openmc.examples

# The choices of this example: edit them here, or give them on the command line.
OPTIONS = {"size": "quick"}

DATA = {"mesh/pin_mesh_moab.h5m": "ea90636a00bb53ba4e937372a8001fc22d296bfbccf78453f3be4bb9888617e5"}
HALF_HEIGHT = 10.0                     # cm: the assembly is cut to z from -10 to +10, the mesh's height
PITCH = 1.26
# The mesh's four channels: the inner cylinder (r = 0.39218 cm, the fuel's radius) of these lattice positions, along a diagonal
CHANNELS = [{"name": "guide tube (centre)", "x": 0.0, "y": 0.0}, {"name": "fuel pin", "x": PITCH, "y": -PITCH},
            {"name": "fuel pin", "x": 2 * PITCH, "y": -2 * PITCH}, {"name": "guide tube", "x": 3 * PITCH, "y": -3 * PITCH}]
R_CHANNEL = 0.39218
SIZES = {"quick": {"particles": 2_000, "batches": 50, "inactive": 20},
         "reference": {"particles": 20_000, "batches": 150, "inactive": 20}}


def data_file(rel):
    """Copy a file of the data cache (AUTOMC_DATA_CACHE, else ~/.cache/automc/data) into the case, checked against its SHA-256;
    returns its name in the case."""
    path = Path(os.environ.get("AUTOMC_DATA_CACHE") or Path.home() / ".cache" / "automc" / "data").expanduser() / rel
    if not path.is_file():
        raise SystemExit(f"{rel} is not in the data cache ({path.parent}); fetch it with "
                         "`python -m automc data fetch unstructured-mesh-tallies`")
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


def build(size="quick"):
    model = openmc.examples.pwr_assembly()
    bottom = openmc.ZPlane(-HALF_HEIGHT, boundary_type="vacuum")
    top = openmc.ZPlane(HALF_HEIGHT, boundary_type="vacuum")
    for cell in model.geometry.get_all_cells().values():
        cell.region &= +bottom & -top
    for surface in model.geometry.get_all_surfaces().values():
        if isinstance(surface, (openmc.XPlane, openmc.YPlane)):
            surface.boundary_type = "vacuum"        # as the notebook: an assembly alone in vacuum, for illustration
    for key, value in SIZES[size].items():
        setattr(model.settings, key, value)
    model.settings.seed = 20261121
    model.settings.source = openmc.IndependentSource(space=openmc.stats.Box([-10.71, -10.71, -HALF_HEIGHT],
                                                                            [10.71, 10.71, HALF_HEIGHT]),
                                                     constraints={"fissionable": True})

    tets = openmc.UnstructuredMesh(data_file("mesh/pin_mesh_moab.h5m"), library="moab", name="pin channels")
    on_tets = openmc.Tally(name="tet mesh")
    on_tets.filters = [openmc.MeshFilter(tets)]
    on_tets.scores = ["flux", "heating"]
    on_tets.estimator = "tracklength"
    tallies = [on_tets]
    for i, ch in enumerate(CHANNELS):
        cyl = openmc.CylindricalMesh(r_grid=[0.0, R_CHANNEL], z_grid=[-HALF_HEIGHT, HALF_HEIGHT], origin=(ch["x"], ch["y"], 0.0),
                                     name=f"channel {i}")
        t = openmc.Tally(name=f"cylinder {i}")
        t.filters = [openmc.MeshFilter(cyl)]
        t.scores = ["flux", "heating"]
        tallies.append(t)
    model.tallies = openmc.Tallies(tallies)
    return model


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--size", choices=list(SIZES), default=OPTIONS["size"])
    a = parser.parse_args()
    build(a.size).export_to_model_xml()


if __name__ == "__main__":
    main()
