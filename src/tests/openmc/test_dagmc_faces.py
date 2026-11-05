"""
The model check's reading of CAD files (tools/openmc/_author_child.py): where two DAGMC volumes touch, the file must hold one face
with a volume on each side. The comparison is tested on made-up triangles (two unit cubes side by side, merged and not); the reader
and the check on the cad-geometry example's files in the data cache, skipped when they are not fetched: the tokamak is not merged,
the teapot and the pin are.
"""
import importlib.util
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

try:
    import numpy as np
except ImportError:                                              # Python without the automc environment: every test skips
    np = None

from tools.openmc import _author_child as child

HAVE_H5PY = importlib.util.find_spec("h5py") is not None
HAVE_OPENMC = importlib.util.find_spec("openmc") is not None and os.name == "posix" and shutil.which("openmc") is not None
QUADS = {"x0": (0, 4, 6, 2), "x1": (1, 3, 7, 5), "y0": (0, 1, 5, 4), "y1": (2, 6, 7, 3), "z0": (0, 2, 3, 1), "z1": (4, 5, 7, 6)}


def _cache():
    return Path(os.environ.get("AUTOMC_DATA_CACHE") or Path.home() / ".cache" / "automc" / "data").expanduser()


def cube(x0, offset):
    """A unit cube from x = x0: its 8 corners (corner i + 2j + 4k at (x0 + i, j, k)) and, for each face, its two triangles as rows
    of points numbered from offset."""
    points = [(x0 + i, j, k) for k in (0, 1) for j in (0, 1) for i in (0, 1)]
    faces = {f: [(offset + a, offset + b, offset + c), (offset + a, offset + c, offset + d)] for f, (a, b, c, d) in QUADS.items()}
    return points, faces


def surfaces(*groups):
    """(triangles, surface of each) from (surface, [triangle rows]) groups."""
    rows = [(s, t) for s, ts in groups for t in ts]
    return np.array([t for _, t in rows]), np.array([s for s, _ in rows])


@unittest.skipUnless(np is not None, "needs NumPy (the automc environment)")
class Comparison(unittest.TestCase):
    A, B = 10, 11                                                # the two volumes

    def setUp(self):
        pa, self.fa = cube(0.0, 0)
        pb, self.fb = cube(1.0, 8)
        self.points = np.array(pa + pb, dtype=float)
        self.points[8:] += 1e-6                                  # written apart, as a CAD export does: still the same points
        self.outer_a = [t for f, ts in self.fa.items() if f != "x1" for t in ts]
        self.outer_b = [t for f, ts in self.fb.items() if f != "x0" for t in ts]

    def test_two_copies_of_the_face_between_two_volumes_are_found(self):
        b_face = [t[::-1] for t in self.fb["x0"]]                # B's own points, its own winding
        tris, surf = surfaces((1, self.outer_a + self.fa["x1"]), (2, self.outer_b + b_face))
        pairs, shared = child.duplicate_faces(self.points, tris, surf, {1: (self.A, 0), 2: (self.B, 0)})
        self.assertEqual(shared, 0)
        self.assertEqual(list(pairs), [(self.A, self.B)])
        n, area = pairs[(self.A, self.B)]
        self.assertEqual(n, 2)
        self.assertAlmostEqual(area, 1.0, places=5)
        text = child.not_merged("two.h5m", pairs, {self.A: (1, "steel"), self.B: (2, None)})
        self.assertIn("touching volumes are not merged: 1 pair of volumes", text)
        self.assertIn("steel (volume 1) and volume 2: 2 triangles, 1 cm2", text)

    def test_one_face_shared_by_both_volumes_is_merged(self):
        tris, surf = surfaces((1, self.outer_a), (2, self.fa["x1"]), (3, self.outer_b))
        pairs, shared = child.duplicate_faces(self.points, tris, surf, {1: (self.A, 0), 2: (self.A, self.B), 3: (self.B, 0)})
        self.assertEqual((pairs, shared), ({}, 1))

    def test_copies_within_one_volume_and_faces_faceted_apart_are_not_counted(self):
        # the same face in two surfaces of A is not a pair of volumes; B's copy cut along the other diagonal is no longer the same
        # triangles, which this comparison cannot see (the finding says so)
        other_diagonal = [(8, 12, 10), (12, 14, 10)]                  # A's copy is cut from (1, 0, 0) to (1, 1, 1)
        tris, surf = surfaces((1, self.outer_a + self.fa["x1"]), (4, self.fa["x1"]), (2, self.outer_b + other_diagonal))
        pairs, _ = child.duplicate_faces(self.points, tris, surf, {1: (self.A, 0), 4: (self.A, 0), 2: (self.B, 0)})
        self.assertEqual(pairs, {})

    def test_no_triangles(self):
        self.assertEqual(child.duplicate_faces(np.zeros((0, 3)), np.zeros((0, 3), dtype=int), np.zeros(0, dtype=int), {}), ({}, 0))


@unittest.skipUnless(np is not None and HAVE_H5PY, "needs NumPy and h5py (the automc environment)")
class Files(unittest.TestCase):
    """The cad-geometry example's three files, as published (their SHA-256 is in the example's build.py)."""

    def read(self, name):
        path = _cache() / "cad" / name
        if not path.is_file():
            self.skipTest(f"{path} is not fetched (python -m automc data fetch cad-geometry)")
        cad = child.read_dagmc(str(path))
        pairs, shared = child.duplicate_faces(*cad[:4])
        return cad, pairs, shared

    def test_the_tokamak_is_not_merged(self):
        (_, tris, _, sides, volumes), pairs, shared = self.read("paramak_with_graveyard.h5m")
        self.assertEqual((len(volumes), len(sides), shared), (19, 19, 0))
        self.assertEqual(len(pairs), 25)
        self.assertEqual(sum(n for n, _ in pairs.values()), 29_114)
        name = {h: m for h, (_, m) in volumes.items()}
        wall = next(v for k, v in pairs.items() if {name[k[0]], name[k[1]]} == {"firstwall", "blanket"})
        self.assertEqual(wall[0], 8_064)
        self.assertAlmostEqual(wall[1] / 1e4, 119, delta=0.5)             # m2
        self.assertIn("touching volumes are not merged: 25 pairs", child.not_merged("t.h5m", pairs, volumes))

    def test_the_teapot_and_the_pin_are_merged(self):
        for name, n_volumes, n_surfaces in (("teapot.h5m", 4, 112), ("fuel_pin.h5m", 4, 21)):
            with self.subTest(file=name):
                (_, _, _, sides, volumes), pairs, shared = self.read(name)
                self.assertEqual((len(volumes), len(sides)), (n_volumes, n_surfaces))
                self.assertEqual(pairs, {})
                self.assertGreater(shared, 0)


@unittest.skipUnless(HAVE_OPENMC, "needs OpenMC (WSL, the automc environment)")
class Check(unittest.TestCase):
    """The model check on the CAD pin: its file is read, and found merged."""

    def test_the_check_reads_a_merged_cad_file(self):
        pin = _cache() / "cad" / "fuel_pin.h5m"
        if not pin.is_file():
            self.skipTest(f"{pin} is not fetched")
        import openmc
        from tools.openmc import authoring
        folder = Path(tempfile.mkdtemp(prefix="automc_dagmc_check_"))
        self.addCleanup(shutil.rmtree, folder, True)
        shutil.copy(pin, folder / "fuel_pin.h5m")
        fuel = openmc.Material(name="fuel")
        fuel.add_nuclide("U235", 1.0)
        fuel.set_density("g/cm3", 11.0)
        water = openmc.Material(material_id=41, name="water")
        water.add_nuclide("H1", 2.0)
        water.add_nuclide("O16", 1.0)
        water.set_density("g/cm3", 1.0)
        settings = openmc.Settings(run_mode="fixed source", particles=1000, batches=5,
                                   source=openmc.IndependentSource(space=openmc.stats.Point()))
        openmc.Model(openmc.Geometry(openmc.DAGMCUniverse(str(folder / "fuel_pin.h5m"))), openmc.Materials([fuel, water]),
                     settings).export_to_model_xml(str(folder / "model.xml"))
        report, log = authoring._child("check", folder, 300)
        cad = [f for f in report.get("findings", []) if f["check"] == "CAD file"]
        self.assertEqual([f["severity"] for f in cad], ["info"], json.dumps(report)[:2000] + log[-2000:])
        self.assertTrue(cad[0]["message"].startswith("fuel_pin.h5m: 4 volumes and 21 surfaces"), cad[0]["message"])   # from the case
        self.assertIn("touching volumes are merged", cad[0]["message"])
        self.assertFalse([f for f in report["findings"] if f["check"] == "overlaps" and f["severity"] == "error"])


if __name__ == "__main__":
    unittest.main()
