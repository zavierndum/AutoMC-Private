"""
Contract tests for build_input: real builds in a worker process (about 12 s each, four of them), then the failure paths.

They check library-independent facts about the written model, never byte hashes across library versions. They need the WSL
automc environment, the library snapshots, the cross-section library and the restricted NETL facility pack (the reference preset);
otherwise they skip and say what is missing. Expected values of the reference preset are read from the pack. No transport is run here.
"""
import hashlib
import json
import math
import os
import shutil
import subprocess
import tempfile
import time
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest import mock

from tools.reactors.triga import (BuildTimeout, InvalidSpec, MissingLibrary, TrigaError, UnsupportedFeature, build_input, data, libraries,
                                  resolve)
from tools.reactors.triga.facilities import netl

from tests.reactors.triga._packs import REF, RESTRICTED_REASON, ref_port_lengths, restricted  # noqa: E402

_XS_FALLBACK = Path.home() / "endfb-viii.0-hdf5" / "cross_sections.xml"


def _why_skip():
    if os.name != "posix":
        return "needs Linux or WSL"
    try:
        import openmc  # noqa: F401
    except ImportError:
        return "OpenMC is not importable in this interpreter (use the WSL automc environment)"
    if restricted() is None:
        return RESTRICTED_REASON
    try:
        libraries.profile_paths("pnt_3el+detectors")
    except MissingLibrary as e:
        return str(e)
    if not os.environ.get("OPENMC_CROSS_SECTIONS") and not _XS_FALLBACK.is_file():
        return "OPENMC_CROSS_SECTIONS is not set and ~/endfb-viii.0-hdf5 was not found"
    return None


SKIP = _why_skip()


def setUpModule():
    if SKIP is None and not os.environ.get("OPENMC_CROSS_SECTIONS"):
        os.environ["OPENMC_CROSS_SECTIONS"] = str(_XS_FALLBACK)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def model_facts(outdir):
    root = ET.parse(Path(outdir) / "model.xml").getroot()
    materials = {m.get("name"): m for m in root.find("materials")}
    cells = {c.get("name") for c in root.find("geometry").iter("cell") if c.get("name")}
    return root, materials, cells


def leftovers(workspace):
    return sorted(p.name for p in Path(workspace).iterdir() if p.name.startswith("."))


@unittest.skipIf(SKIP, SKIP or "")
class BuildConfig0(unittest.TestCase):
    """One real default build, inspected from several angles, plus three variants."""

    @classmethod
    def setUpClass(cls):
        cls.ws = Path(tempfile.mkdtemp(prefix="triga_contract_"))
        cls.base = build_input("openmc", "netl", config=0, workspace=cls.ws)
        cls.s = cls.base.summary

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.ws, ignore_errors=True)

    # ---- what was written -----------------------------------------------------------------------------------------------
    def test_files_and_result_json(self):
        out = Path(self.base.outdir)
        self.assertEqual(out.parent.resolve(), self.ws.resolve())
        self.assertRegex(out.name, r"^triga-netl-c0-[0-9a-f]{8}$")
        for name in ("model.xml", "build.log", "result.json"):
            self.assertTrue((out / name).is_file(), name)
        self.assertEqual(json.loads((out / "result.json").read_text()), self.base.to_dict())
        for f in self.base.files:
            self.assertEqual(sha(out / f["path"]), f["sha256"], f["path"])
        self.assertEqual([f["path"] for f in self.base.files], ["model.xml", "build.log"])

    def test_no_temporary_folders_left_behind(self):
        self.assertEqual(leftovers(self.ws), [])

    def test_result_records_versions_provenance_and_assumptions(self):
        r = self.base
        self.assertEqual((r.code, r.facility, r.preset, r.reused), ("openmc", "netl", "netl_boc505@1", False))
        self.assertEqual(r.profile["name"], "pnt_3el+detectors")
        self.assertEqual(len(r.profile["patch_sha256"]), 64)
        self.assertEqual(r.nuclear_data["sha256"], sha(r.nuclear_data["path"]))
        self.assertEqual(r.nuclear_data["library"], "endfb-viii.0")
        self.assertEqual({a["id"] for a in r.assumptions}, {"A1", "A2", "A3", "A4", "A5", "A6", "A9", "A10", "A16"})
        self.assertEqual(r.spec["inserts"], {"pnt": None, "three_el": None, "three_el_empty_state": "water"})
        self.assertEqual(r.spec["rods"]["transient"], REF["rods"]["transient"])
        self.assertEqual(len(r.spec_hash), 64)
        self.assertGreater(r.build_seconds, 0)

    # ---- the model (the reference model's, library-independent facts) -------------------------------------------------------------------
    def test_every_mapped_location_holds_depleted_fuel(self):
        dep = data.depleted_module(data.fuel_module_path(resolve()))
        f = self.s["fuel"]
        n = len(dep.GRID_LOCATION_TO_MCNP_MATERIAL)
        self.assertEqual((f["mapped_locations"], f["mapped_locations_holding_fuel"], f["elements"], f["depleted_elements"]), (n, n, n, n))
        self.assertEqual(f["forced_locations"], ["D-03", "G-24"])
        self.assertEqual(f["unplaced_locations"], [])
        _, mats, _ = model_facts(self.base.outdir)
        expected = {f"depleted_fuel_m{mid}" for mid in dep.GRID_LOCATION_TO_MCNP_MATERIAL.values()}
        found = {n for n in mats if n and n.startswith("depleted_fuel_m")}
        self.assertEqual(found, expected)

    def test_config_zero_has_no_pnt_and_no_three_el(self):
        self.assertEqual(self.s["slots"], {"G-34": "NoneType", "E-11": "NoneType", "F-13": "NoneType", "F-14": "NoneType"})

    def test_rod_positions(self):
        for rod in netl.RODS:
            frac = REF["rods"][rod]
            expected = netl.INSERTED_Z_CM[rod] + frac * netl.ROD_TRAVEL_CM
            self.assertAlmostEqual(self.s["rods"][rod]["library_z_cm"], expected, places=6, msg=rod)
            self.assertEqual(self.s["rods"][rod]["fraction"], frac)

    def test_detector_geometry_and_barite_region(self):
        _, _, cells = model_facts(self.base.outdir)
        expected = {"beam_port_1_5_detector", "beam_port_2_detector_1", "beam_port_2_detector_2", "beam_port_2_detector_3",
                    "beam_port_2_sphere_detector_1", "beam_port_2_sphere_detector_2", "beam_port_2_sphere_detector_3",
                    "beam_port_2_water_fill", "beam_port_2_air_fill", "outside_pool_region"}
        self.assertTrue(expected <= cells, expected - cells)
        self.assertEqual(self.s["fill_cells"], {"beam_port_2_water_fill": 1, "beam_port_2_air_fill": 1, "outside_pool_region": 1})
        self.assertEqual(len(self.s["detector_cells"]), 7)

    def test_barite_material_as_the_reference_script_writes_it(self):
        _, mats, _ = model_facts(self.base.outdir)
        b = mats["barite_concrete"]
        self.assertEqual(float(b.find("density").get("value")), data.barite(resolve())["density_gcc"])     # the pack's value
        # preset @1 passes the numbers as atom fractions (assumption A1), so the entries are atom-percent based
        attrs = {k for n in b.findall("nuclide") for k in n.attrib}
        self.assertIn("ao", attrs)
        self.assertNotIn("wo", attrs)

    def test_settings_and_tallies(self):
        root, _, _ = model_facts(self.base.outdir)
        st = root.find("settings")
        self.assertEqual((st.findtext("particles"), st.findtext("batches"), st.findtext("inactive")), ("20000", "40", "10"))
        self.assertEqual(st.findtext("run_mode"), "eigenvalue")
        out = st.find("output")                                     # no tallies.out (2 GB with the detector tallies); summary.h5 is kept
        self.assertEqual((out.findtext("summary"), out.findtext("tallies")), ("true", "false"))
        self.assertFalse(self.s["settings"]["text_tally_output"])
        self.assertEqual(self.s["tallies"], 8)                      # the problem-5 generic tallies
        self.assertEqual(len(root.find("tallies").findall("tally")), 8)
        self.assertEqual(self.s["temperature_method"], "nearest")

    def test_beam_ports_start_at_the_tank_wall_and_do_not_cross_the_core(self):
        # The library places each port from its own length. A port lengthened without being moved would run through the core.
        ports = self.s["beam_ports"]
        length, length_15 = ref_port_lengths()
        self.assertEqual({k: p["length_cm"] for k, p in ports.items()}, {"1": length_15, "2": length, "3": length, "4": length})
        self.assertAlmostEqual(ports["3"]["translation_cm"][0], -(length / 2 + 26.43188), places=4)          # centre moves with the length
        self.assertAlmostEqual(ports["2"]["translation_cm"][0], 6.222 + math.cos(math.radians(150)) * length / 2, places=4)
        self.assertAlmostEqual(ports["4"]["translation_cm"][1], -22.871 - math.sin(math.radians(60)) * length / 2, places=4)
        for p in ports.values():
            self.assertEqual(p["translation_cm"][2], netl.BEAM_PORT_AXIAL_OFFSET_CM)

    def test_geometry_by_point_queries(self):
        import openmc
        model = openmc.Model.from_model_xml(str(Path(self.base.outdir) / "model.xml"))
        z = netl.BEAM_PORT_AXIAL_OFFSET_CM

        def in_port(x, y):
            return any("beam_port" in (getattr(o, "name", "") or "") for o in model.geometry.find((x, y, z)))

        # nothing inside the core (within 17 cm of the centre, at the port height) belongs to a beam port. The ports do pass through the
        # graphite reflector further out, which is correct, so the grid stops well inside the octagonal core.
        inside = [(x, y) for x in (-12, -6, 0, 6, 12) for y in (-12, -6, 0, 6, 12)]
        self.assertEqual([pt for pt in inside if in_port(*pt)], [])
        # but points along each port axis, well outside the core, do
        for key, (cx, cy, _) in ((k, p["translation_cm"]) for k, p in self.s["beam_ports"].items() if k != "1"):
            self.assertTrue(in_port(cx, cy), f"port {key} is not where the summary says")
        self.assertTrue(in_port(35.2425, 0.0), "port 1/5 is not where the summary says")

    def test_reusing_an_identical_request(self):
        mtime = (Path(self.base.outdir) / "model.xml").stat().st_mtime
        t = time.time()
        again = build_input("openmc", "netl", config=0, workspace=self.ws)
        self.assertTrue(again.reused)
        self.assertLess(time.time() - t, 3)
        self.assertEqual((Path(self.base.outdir) / "model.xml").stat().st_mtime, mtime)
        self.assertEqual(again.spec_hash, self.base.spec_hash)

    # ---- determinism and variants (each a fresh worker process) -------------------------------------------------------------
    def test_a_fresh_process_writes_an_identical_model(self):
        other = build_input("openmc", "netl", config=0, name="second-build", workspace=self.ws)
        self.assertNotEqual(other.outdir, self.base.outdir)
        self.assertEqual(sha(Path(other.outdir) / "model.xml"), sha(Path(self.base.outdir) / "model.xml"))
        self.assertEqual(other.spec_hash, self.base.spec_hash)

    def test_changing_only_the_transient_rod(self):
        other = build_input("openmc", "netl", rods={"TR": 0.0}, workspace=self.ws)
        a, b = dict(self.s), dict(other.summary)
        self.assertEqual(b["rods"]["transient"]["fraction"], 0.0)
        self.assertAlmostEqual(b["rods"]["transient"]["library_z_cm"], -73.025, places=6)
        for rod in ("regulating", "shim_1", "shim_2"):
            self.assertEqual(a["rods"][rod], b["rods"][rod])
        a.pop("rods"), b.pop("rods")
        self.assertEqual(a, b)                                      # cells, materials, fuel, detectors, settings all unchanged
        self.assertNotEqual(sha(Path(other.outdir) / "model.xml"), sha(Path(self.base.outdir) / "model.xml"))

    def test_every_rod_unit_builds_the_same_model(self):
        by_console = build_input("openmc", "netl", rods={"S1": {"value": 480, "unit": "console"}}, workspace=self.ws)
        by_fraction = build_input("openmc", "netl", rods={"S1": 0.5}, name="by-fraction", workspace=self.ws)
        self.assertEqual(by_console.spec_hash, by_fraction.spec_hash)
        self.assertEqual(sha(Path(by_console.outdir) / "model.xml"), sha(Path(by_fraction.outdir) / "model.xml"))
        self.assertAlmostEqual(by_console.summary["rods"]["shim_1"]["library_z_cm"], -76.518 + 19.05, places=6)

    def test_barite_basis_mass_changes_only_the_barite_entries(self):
        mass = build_input("openmc", "netl", environment={"barite_composition_basis": "mass"}, workspace=self.ws)
        _, mats, _ = model_facts(mass.outdir)
        attrs = {k for n in mats["barite_concrete"].findall("nuclide") for k in n.attrib}
        self.assertIn("wo", attrs)
        self.assertNotIn("ao", attrs)
        self.assertEqual(mass.summary["cells"], self.s["cells"])
        self.assertEqual(mass.summary["materials"], self.s["materials"])


def cli(*args):
    p = subprocess.run([__import__("sys").executable, "-m", "automc", "triga", *args], cwd=libraries.SRC, capture_output=True, text=True, timeout=300)
    return p.returncode, p.stdout, p.stderr


@unittest.skipIf(SKIP, SKIP or "")
class CommandLineBuildAndPreview(unittest.TestCase):
    """The same build driven from the command line: output, reuse, force, the log, and the previews."""

    @classmethod
    def setUpClass(cls):
        cls.ws = Path(tempfile.mkdtemp(prefix="triga_cli_"))
        cls.args = ["--config", "0", "--rod", "S1=600:console", "--name", "cli-case", "--workspace", str(cls.ws)]
        cls.code, cls.out, cls.err = cli("build", *cls.args, "--json")
        cls.first = json.loads(cls.out) if cls.code == 0 else None

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.ws, ignore_errors=True)

    def test_json_build(self):
        self.assertEqual(self.code, 0, self.err)
        r = self.first
        self.assertEqual((r["name"], r["reused"], r["spec"]["rods"]["shim_1"]), ("cli-case", False, 0.625))
        self.assertEqual(Path(r["outdir"]), (self.ws / "cli-case").resolve())
        self.assertIn("run_estimate", r["summary"])
        self.assertEqual(r["summary"]["run_estimate"]["histories"], 800_000)
        self.assertEqual(r["warnings"], [])                                    # an ordinary smoke build has nothing to warn about

    def test_build_log_has_a_header_and_a_footer(self):
        log = (Path(self.first["outdir"]) / "build.log").read_text()
        self.assertIn("triga ", log.splitlines()[0])
        self.assertIn(self.first["spec_hash"], log)
        self.assertIn("profile      pnt_3el+detectors", log)
        self.assertIn("---- worker output ----", log)
        self.assertRegex(log, r"---- worker exit code 0 after [\d.]+ s ----")
        self.assertIn("ACTUAL BP LENGTH", log)                                  # the worker's own output sits between them

    def test_human_output_and_reuse(self):
        code, out, err = cli("build", *self.args)
        self.assertEqual(code, 0, err)
        self.assertTrue(out.startswith("reused an identical build: cli-case"), out)
        n = len(data.depleted_module(data.fuel_module_path(resolve())).GRID_LOCATION_TO_MCNP_MATERIAL)
        for needle in ("folder", "model.xml", f"{n} of {n} mapped locations hold fuel (forced: D-03, G-24)", "shim_1 0.625 (600 console)",
                       "20,000 x 40 batches", "A1 A2", "openmc -s", "run-001", "python -m automc triga preview cli-case"):
            self.assertIn(needle, out)

    def test_quiet_prints_only_the_folder(self):
        code, out, _ = cli("build", *self.args, "--quiet")
        self.assertEqual((code, out.strip()), (0, str(self.ws.resolve() / "cli-case")))

    def test_zz_force_rebuilds_even_an_identical_build(self):
        model = Path(self.first["outdir"]) / "model.xml"
        before = model.stat().st_mtime_ns
        code, out, err = cli("build", *self.args, "--force", "--json")
        self.assertEqual(code, 0, err)
        self.assertFalse(json.loads(out)["reused"])
        self.assertGreater(model.stat().st_mtime_ns, before)
        self.assertEqual(json.loads(out)["files"][0]["sha256"], self.first["files"][0]["sha256"])      # same bytes, freshly written
        self.assertEqual(sorted(p.name for p in self.ws.iterdir()), ["cli-case"])

    def test_previews(self):
        code, out, err = cli("preview", "cli-case", "--workspace", str(self.ws), "--which", "core_xy,world_xz", "--pixels", "400", "--json")
        self.assertEqual(code, 0, err)
        made = json.loads(out)
        self.assertEqual([m["name"] for m in made], ["core_xy", "world_xz"])
        for m in made:
            data = Path(m["path"]).read_bytes()
            self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n")
            self.assertGreater(len(data), 10_000, m["name"])
        try:
            import matplotlib.image as mpimg
            img = mpimg.imread(made[0]["path"])
            self.assertGreater(len({tuple(px) for px in img.reshape(-1, img.shape[-1])[::97]}), 5)     # more than a flat colour
        except ImportError:
            pass
        # previews are extras: they must not disturb the build or its reuse
        code, out, _ = cli("build", *self.args)
        self.assertTrue(out.startswith("reused an identical build"), out)


@unittest.skipIf(SKIP, SKIP or "")
class FailurePaths(unittest.TestCase):
    def setUp(self):
        self.ws = Path(tempfile.mkdtemp(prefix="triga_fail_"))

    def tearDown(self):
        shutil.rmtree(self.ws, ignore_errors=True)

    def assertClean(self):
        self.assertEqual(sorted(p.name for p in self.ws.iterdir()), [], "a failed build must leave nothing behind")

    def test_unsupported_is_refused_before_any_worker_starts(self):
        t = time.time()
        for kw, feature in (({"environment": {"detector_geometry": False}}, "environment.barite / environment.detector_geometry"),
                            ({"config": 8, "environment": {"barite": False}}, "environment.barite / environment.detector_geometry")):
            with self.subTest(kw=kw):
                with self.assertRaises(UnsupportedFeature) as ctx:
                    build_input("openmc", "netl", workspace=self.ws, **kw)
                self.assertEqual(ctx.exception.feature, feature)
        with self.assertRaises(UnsupportedFeature):
            build_input("mcnp", "netl", workspace=self.ws)
        self.assertLess(time.time() - t, 3)
        self.assertClean()

    def test_invalid_spec_is_refused_before_any_worker_starts(self):
        with self.assertRaises(InvalidSpec) as ctx:
            build_input("openmc", "netl", rods={"S1": {"value": 1200, "unit": "console"}}, workspace=self.ws)
        self.assertEqual(ctx.exception.field, "rods.S1")
        self.assertClean()

    def test_outdir_outside_the_workspace_is_refused(self):
        with tempfile.TemporaryDirectory() as elsewhere:
            with self.assertRaises(InvalidSpec) as ctx:
                build_input("openmc", "netl", outdir=elsewhere + "/x", workspace=self.ws)
            self.assertEqual(ctx.exception.field, "outdir")
        self.assertClean()

    def test_missing_cross_sections(self):
        with mock.patch.dict(os.environ):
            os.environ.pop("OPENMC_CROSS_SECTIONS", None)
            with self.assertRaises(MissingLibrary) as ctx:
                build_input("openmc", "netl", workspace=self.ws)
        self.assertIn("OPENMC_CROSS_SECTIONS", str(ctx.exception))
        self.assertClean()

    def test_missing_library_snapshots(self):
        with tempfile.TemporaryDirectory() as empty, mock.patch.object(libraries, "SNAPSHOTS_DIR", Path(empty)):
            with self.assertRaises(MissingLibrary) as ctx:
                build_input("openmc", "netl", workspace=self.ws)
        self.assertIn("make_snapshots.py", str(ctx.exception))
        self.assertClean()

    def test_errors_inside_the_worker_keep_their_type_and_field(self):
        cases = {"invalid": InvalidSpec, "unsupported": UnsupportedFeature, "missing": MissingLibrary}
        for mode, exc in cases.items():
            with self.subTest(mode=mode), mock.patch.dict(os.environ, {"TRIGA_WORKER_TEST_FAIL": mode}):
                with self.assertRaises(exc) as ctx:
                    build_input("openmc", "netl", workspace=self.ws)
                if mode == "invalid":
                    self.assertEqual(ctx.exception.field, "test.field")
                if mode == "unsupported":
                    self.assertEqual(ctx.exception.feature, "test.feature")
                self.assertClean()

    def test_worker_crash_reports_the_log_tail(self):
        with mock.patch.dict(os.environ, {"TRIGA_WORKER_TEST_FAIL": "crash"}):
            with self.assertRaises(TrigaError) as ctx:
                build_input("openmc", "netl", workspace=self.ws)
        self.assertIn("exited with code 7", str(ctx.exception))
        self.assertIn("about to crash on purpose", str(ctx.exception))
        self.assertClean()

    def test_timeout_kills_the_worker_and_leaves_nothing(self):
        t = time.time()
        with mock.patch.dict(os.environ, {"TRIGA_WORKER_TEST_FAIL": "sleep"}):
            with self.assertRaises(BuildTimeout):
                build_input("openmc", "netl", workspace=self.ws, timeout_s=3)
        self.assertLess(time.time() - t, 30)
        self.assertClean()
        time.sleep(0.5)
        still = subprocess.run(["pgrep", "-f", "triga._worker"], capture_output=True, text=True)
        self.assertEqual(still.stdout.strip(), "", "the worker process was not killed")

    def test_existing_different_folder_is_refused_and_survives_a_failed_force(self):
        name = "triga-netl-c0-existing"
        target = self.ws / name
        target.mkdir()
        (target / "keep.txt").write_text("important")
        with self.assertRaises(InvalidSpec) as ctx:
            build_input("openmc", "netl", name=name, workspace=self.ws)
        self.assertEqual(ctx.exception.field, "outdir")
        with mock.patch.dict(os.environ, {"TRIGA_WORKER_TEST_FAIL": "invalid"}):
            with self.assertRaises(InvalidSpec):
                build_input("openmc", "netl", name=name, workspace=self.ws, force=True)
        self.assertEqual((target / "keep.txt").read_text(), "important")        # a failed forced rebuild does not destroy the old folder
        self.assertEqual(sorted(p.name for p in self.ws.iterdir()), [name])


if __name__ == "__main__":
    unittest.main()
