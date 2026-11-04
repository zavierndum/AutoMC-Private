"""
A CAD model with a tally on its own tetrahedral mesh: a steel piping manifold (DAGMC) with a 5 MeV neutron point source at the mouth
of its single pipe, and the flux scored on a tet mesh of the manifold (MOAB) in two energy groups, below and above 1 MeV. post.py reads
the mesh's elements back, checks the flux against the physics, and writes it out for viewing: a VTK file and a point-cloud picture.

Derived from: the OpenMC notebook "Unstructured Mesh: Tallies with CAD and Point Cloud Visualization" (openmc-notebooks, tag v0.15.0;
the manifold, its materials, the source, the mesh tally and its energy groups, the point cloud). Written for AutoMC; README.md
explains each step.

    python build.py                          the defaults below; writes model.xml and copies the CAD and mesh files
    python build.py --size reference

Nothing here runs transport.
"""
import argparse
import hashlib
import os
import shutil
from pathlib import Path

import openmc

# The choices of this example: edit them here, or give them on the command line.
OPTIONS = {"size": "quick"}

DATA = {"cad/manifold.h5m": "d027cf46ddc755232754eaeeec72699f15172599d50d979c096b6af2d2cd0890",
        "mesh/manifold_mesh.h5m": "671c6a01278947d558131d2c07aedf604d9e6576c762d5c33fee74253ed80664"}
SOURCE_XYZ = (0.0, 0.0, 0.0)            # the mouth of the single pipe, in air
GROUPS = [0.0, 1.0e6, 5.0e6]            # eV: below and above 1 MeV
SIZES = {"quick": {"particles": 2_000, "batches": 20}, "reference": {"particles": 10_000, "batches": 100}}


def data_file(rel):
    """Copy a file of the data cache (AUTOMC_DATA_CACHE, else ~/.cache/automc/data) into the case, checked against its SHA-256;
    returns its name in the case."""
    path = Path(os.environ.get("AUTOMC_DATA_CACHE") or Path.home() / ".cache" / "automc" / "data").expanduser() / rel
    if not path.is_file():
        raise SystemExit(f"{rel} is not in the data cache ({path.parent}); fetch it with "
                         "`python -m automc data fetch cad-mesh-visualisation`")
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
    air = openmc.Material(name="air")                # the names the CAD file's volumes carry
    air.set_density("g/cm3", 0.001205)
    for element, f in (("N", 0.784431), ("O", 0.210748), ("Ar", 0.0046)):
        air.add_element(element, f)
    steel = openmc.Material(name="steel")
    steel.set_density("g/cm3", 8.0)
    for element, f in (("Si", 0.010048), ("S", 0.00023), ("Fe", 0.669), ("Ni", 0.12), ("Mo", 0.025)):
        steel.add_element(element, f)
    steel.add_nuclide("P31", 0.00023)
    steel.add_nuclide("Mn55", 0.011014)

    settings = openmc.Settings()
    settings.run_mode = "fixed source"
    for key, value in SIZES[size].items():
        setattr(settings, key, value)
    settings.seed = 20261122
    settings.source = openmc.IndependentSource(space=openmc.stats.Point(SOURCE_XYZ), energy=openmc.stats.Discrete([5.0e6], [1.0]))

    mesh = openmc.UnstructuredMesh(data_file("mesh/manifold_mesh.h5m"), library="moab", name="manifold mesh")
    flux = openmc.Tally(name="flux on the mesh")
    flux.filters = [openmc.MeshFilter(mesh), openmc.EnergyFilter(GROUPS)]
    flux.scores = ["flux"]
    flux.estimator = "tracklength"
    return openmc.Model(geometry=openmc.Geometry(openmc.DAGMCUniverse(data_file("cad/manifold.h5m"))),
                        materials=openmc.Materials([air, steel]), settings=settings, tallies=openmc.Tallies([flux]))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--size", choices=list(SIZES), default=OPTIONS["size"])
    a = parser.parse_args()
    build(a.size).export_to_model_xml()


if __name__ == "__main__":
    main()
