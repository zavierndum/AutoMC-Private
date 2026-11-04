"""Library profiles, provenance, preflight, data loading and result type. No CoreForge or OpenMC needed."""
import hashlib
import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools.reactors.triga import BuildResult, MissingLibrary, libraries, resolve
from tools.reactors.triga import data

from tests.reactors.triga._packs import PUBLIC_PRESET, restricted  # noqa: E402

REFLECTOR = "patches/coreforge_reflector_excludes_shroud_dcfc111.patch"
DETECTORS = "patches/openmc_reactor_detectors_on_pnt3el_dcfc111.patch"
BARITE_ON = {"barite": True, "detector_geometry": True, "world_radius_cm": 300.0, "world_height_cm": 300.0,
             "barite_composition_basis": "atom"}


class Profiles(unittest.TestCase):
    def test_profile_choice(self):
        self.assertEqual(libraries.profile_for(resolve(preset=PUBLIC_PRESET, environment=BARITE_ON)), "pnt_3el+detectors")
        self.assertEqual(libraries.profile_for(resolve(preset=PUBLIC_PRESET)), "pnt_3el")

    def test_unknown_profile(self):
        with self.assertRaises(MissingLibrary):
            libraries.profile_paths("nope")

    @unittest.skipUnless((libraries.SNAPSHOTS_DIR / "CoreForge_dcfc111").is_dir(), "library snapshots not built (python library_snapshots/make_snapshots.py)")
    def test_provenance_of_the_public_profile(self):
        plain = libraries.provenance("pnt_3el")                       # the plain profile carries the reflector fix
        self.assertEqual([x["file"] for x in plain["patches"]], [REFLECTOR])
        self.assertEqual(plain["patches"][0]["sha256"], hashlib.sha256((libraries.SNAPSHOTS_DIR / REFLECTOR).read_bytes()).hexdigest())
        self.assertEqual(plain["snapshots"], ["CoreForge_dcfc111+fixes", "progression_problems_1f31371"])

    @unittest.skipUnless(restricted() is not None, "needs the restricted NETL facility pack")
    def test_provenance_of_the_pack_profile_records_commits_and_patch_checksum(self):
        p = libraries.provenance("pnt_3el+detectors")
        self.assertEqual(p["name"], "pnt_3el+detectors")
        self.assertIn("dcfc111", p["commits"]["CoreForge"])
        self.assertIn("1f31371", p["commits"]["progression_problems"])
        self.assertEqual([x["file"] for x in p["patches"]], [REFLECTOR, DETECTORS])
        files = [libraries.SNAPSHOTS_DIR / REFLECTOR, restricted().folder / DETECTORS]       # the detector patch lives in the pack
        for x, f in zip(p["patches"], files):
            self.assertEqual(x["sha256"], hashlib.sha256(f.read_bytes()).hexdigest())
        self.assertEqual(p["patch_sha256"], hashlib.sha256(b"".join(f.read_bytes() for f in files)).hexdigest())
        self.assertFalse((libraries.SNAPSHOTS_DIR / DETECTORS).exists())                       # and not among the public snapshots
        self.assertFalse((libraries.SNAPSHOTS_DIR / "CoreForge_dcfc111+detectors").exists())

    @unittest.skipUnless((libraries.SNAPSHOTS_DIR / "CoreForge_dcfc111+fixes").is_dir(), "library snapshots not built")
    def test_reflector_fix_is_in_both_coreforge_snapshots(self):
        line = "~(-rsr_cavity_outer_radius & +bottom_of_rsr_cavity) & ~shroud_region,"
        snapshots = [libraries.SNAPSHOTS_DIR / "CoreForge_dcfc111+fixes"]
        if restricted() is not None:
            snapshots.append(restricted().folder / "library_snapshots" / "CoreForge_dcfc111+detectors")
        for snap in snapshots:
            text = (snap / "coreforge/openmc_builder/triga/netl/reactor.py").read_text()
            self.assertIn(line, text, snap.name)
        pristine = (libraries.SNAPSHOTS_DIR / "CoreForge_dcfc111/coreforge/openmc_builder/triga/netl/reactor.py").read_text()
        self.assertNotIn(line, pristine)                              # the upstream snapshot stays as upstream has it
        self.assertIn("~(-rsr_cavity_outer_radius & +bottom_of_rsr_cavity),", pristine)

    def test_missing_snapshots_say_how_to_build_them(self):
        with tempfile.TemporaryDirectory() as empty, mock.patch.object(libraries, "SNAPSHOTS_DIR", Path(empty)):
            with self.assertRaises(MissingLibrary) as ctx:
                libraries.profile_paths("pnt_3el")
            self.assertIn("make_snapshots.py", str(ctx.exception))

    @unittest.skipUnless((libraries.SNAPSHOTS_DIR / "CoreForge_dcfc111").is_dir(), "library snapshots not built")
    def test_worker_pythonpath_order(self):
        parts = libraries.worker_pythonpath("pnt_3el", existing="/keep/me").split(os.pathsep)
        self.assertEqual(parts[0], str(libraries.SRC))
        self.assertTrue(parts[1].endswith("CoreForge_dcfc111+fixes"))
        self.assertTrue(parts[2].endswith("progression_problems_1f31371"))
        self.assertEqual(parts[-1], "/keep/me")


class NuclearData(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_good_file_is_fingerprinted(self):
        f = self.dir / "cross_sections.xml"
        f.write_text('<?xml version="1.0"?><cross_sections></cross_sections>')
        info = libraries.nuclear_data_info(environ={"OPENMC_CROSS_SECTIONS": str(f)})
        self.assertEqual(info["library"], "endfb-viii.0")
        self.assertEqual(info["path"], str(f))
        self.assertEqual(info["bytes"], f.stat().st_size)
        self.assertEqual(info["sha256"], hashlib.sha256(f.read_bytes()).hexdigest())

    def test_problems_are_missing_library_with_advice(self):
        wrong = self.dir / "wrong.xml"
        wrong.write_text("<nothing/>")
        for env, needle in [({}, "not set"), ({"OPENMC_CROSS_SECTIONS": str(self.dir / "absent.xml")}, "not a file"),
                            ({"OPENMC_CROSS_SECTIONS": str(wrong)}, "does not look like")]:
            with self.subTest(env=env):
                with self.assertRaises(MissingLibrary) as ctx:
                    libraries.nuclear_data_info(environ=env)
                self.assertIn(needle, str(ctx.exception))


class PackData(unittest.TestCase):
    def test_missing_fuel_file_is_reported(self):
        with mock.patch.dict(data._cache, clear=True):
            with self.assertRaises(MissingLibrary) as ctx:
                data.depleted_module(Path("/no/such/file.py"))
            self.assertIn("fuel data not found", str(ctx.exception))

    def test_fresh_fuel_needs_no_pack(self):
        self.assertIsNone(data.fuel_module_path(resolve(preset=PUBLIC_PRESET)))
        self.assertIsNone(data.barite(resolve(preset=PUBLIC_PRESET)))

    @unittest.skipUnless(restricted() is not None and importlib.util.find_spec("openmc") is not None,
                         "needs the restricted NETL facility pack and OpenMC in this interpreter")
    def test_real_module_loads(self):
        dep = data.depleted_module(data.fuel_module_path(resolve(preset="netl_boc505")))
        self.assertGreater(len(dep.GRID_LOCATION_TO_MCNP_MATERIAL), 100)
        self.assertIn("D-03", dep.GRID_LOCATION_TO_MCNP_MATERIAL)
        self.assertIn("G-24", dep.GRID_LOCATION_TO_MCNP_MATERIAL)

    @unittest.skipUnless(restricted() is not None, "needs the restricted NETL facility pack")
    def test_barite_comes_from_the_pack_and_is_well_formed(self):
        b = data.barite(resolve(preset="netl_boc505"))
        self.assertEqual(b["name"], data.BARITE_MATERIAL)
        self.assertGreater(b["density_gcc"], 0)
        self.assertAlmostEqual(sum(v for _, v in b["composition"]), 1.0, places=6)     # the reference numbers sum to one (A1)


class ResultType(unittest.TestCase):
    def test_round_trip_and_strictness(self):
        d = dict(result_version=1, triga_version="x", spec_version=1, built_at="t", code="openmc", facility="netl", name="n", preset="p",
                 spec={}, spec_hash="h", profile={}, nuclear_data={}, assumptions=[], files=[], summary={}, warnings=[], outdir="/o")
        r = BuildResult.from_dict(d)
        self.assertFalse(r.reused)
        self.assertEqual(BuildResult.from_dict(r.to_dict()), r)
        with self.assertRaises(ValueError):
            BuildResult.from_dict({**d, "bogus": 1})


if __name__ == "__main__":
    unittest.main()
