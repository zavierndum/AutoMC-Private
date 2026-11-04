"""
The agent tools (tools/registry.py): every tool with good and bad arguments. Results must be plain JSON and errors must come back as the
same payload, never as an exception. Part 1 runs anywhere; part 2 builds, sweeps, previews and reads a tiny run in the WSL automc
environment (one 1,000 x 3 batch transport run, allowed in tests by decision D6).
"""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import automc_paths
from tools import registry
from tools.reactors.triga import tools as triga_tools

from tests.reactors.triga._packs import PUBLIC_PRESET, restricted  # noqa: E402
from tests.reactors.triga.test_build_config0 import _XS_FALLBACK  # noqa: E402

TINY = {"run": {"particles": 1000, "batches": 3, "inactive": 1, "seed": 7}}


def call(name, args=None):
    out = registry.call(name, args)
    json.dumps(out)                                                   # always plain JSON
    return out


class Registry(unittest.TestCase):
    def test_every_tool_is_described_with_a_strict_schema(self):
        self.assertEqual(registry.names(), ["TRIGAInputSpecTool", "TRIGAInputBuilderTool", "TRIGASweepBuilderTool", "TRIGAInputPreviewTool",
                                            "TRIGAAssumptionsTool", "TRIGASweepResultsTool", "RunResultReaderTool", "OpenMCRunStartTool",
                                            "OpenMCRunCheckTool", "OpenMCRunPauseTool", "OpenMCRunResumeTool", "OpenMCRunStopTool",
                                            "OpenMCRunCancelTool", "OpenMCJobListTool", "OpenMCModelBuildTool", "OpenMCGeometryPlotTool",
                                            "OpenMCModelCheckTool", "SkillLookupTool", "FileListTool",
                                            "FileReadTool", "FileWriteTool", "DocumentParseTool", "DocumentCheckTool",
                                            "DocumentSearchTool", "DocumentCancelTool", "WebSearchTool", "WebFetchTool",
                                            "PythonRunTool", "ChatSearchTool", "MemoryWriteTool", "MemoryReadTool"])
        for t in registry.describe():
            with self.subTest(tool=t["name"]):
                self.assertTrue(t["name"].endswith("Tool"))
                self.assertGreater(len(t["description"]), 80)
                self.assertFalse(t["parameters"]["additionalProperties"])
                json.dumps(t)

    def test_tools_that_write_say_they_never_run_transport(self):
        d = {t["name"]: t["description"] for t in registry.describe()}
        for name in ("TRIGAInputBuilderTool", "TRIGASweepBuilderTool"):
            self.assertIn("never runs transport", d[name])
        self.assertIn("Reads only", d["RunResultReaderTool"])
        self.assertIn("NEVER starts a run", d["OpenMCRunStartTool"])

    def test_unknown_tool_and_unknown_or_missing_arguments_are_payloads(self):
        out = call("NoSuchTool", {})
        self.assertEqual((out["ok"], out["error"]["type"]), (False, "UnknownTool"))
        for name in registry.names():
            with self.subTest(tool=name):
                out = call(name, {"colour": "blue"})
                self.assertFalse(out["ok"])
                self.assertEqual(out["error"]["field"], "colour")
                self.assertEqual(out["error"]["exit_code"], 2)
        out = call("TRIGASweepBuilderTool", {"sweep": {"rod": "TR", "positions": [0, 1]}})
        self.assertEqual(out["error"]["field"], "sweep_name")
        out = call("TRIGAInputSpecTool", "not an object")
        self.assertEqual(out["error"]["field"], "arguments")


class SpecAndAssumptions(unittest.TestCase):
    def test_the_public_preset_resolves_and_reports_everything(self):
        out = call("TRIGAInputSpecTool", {"preset": PUBLIC_PRESET, "pnt": "thermal", "rods": {"TR": {"value": 480, "unit": "console"}}})
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["spec"]["rods"]["transient"], 0.5)
        self.assertEqual(out["rods_all_units"]["transient"]["console"], 480.0)
        self.assertTrue(out["buildable"])
        self.assertEqual(out["facility_data"]["distribution"], "public")
        self.assertIn("A7", {a["id"] for a in out["assumptions"]})
        self.assertEqual(len(out["spec_hash"]), 64)

    def test_bad_values_name_the_field(self):
        out = call("TRIGAInputSpecTool", {"preset": PUBLIC_PRESET, "rods": {"TR": 1.5}})
        self.assertEqual((out["ok"], out["error"]["type"], out["error"]["field"]), (False, "InvalidSpec", "rods.TR"))         # the field as the caller wrote it
        out = call("TRIGAInputSpecTool", {"spec": "/etc/passwd"})            # a spec is an object, never a file path
        self.assertEqual(out["error"]["field"], "spec")

    def test_unbuildable_specs_say_why_without_failing(self):
        out = call("TRIGAInputSpecTool", {"preset": PUBLIC_PRESET, "environment": {"detector_geometry": True}})
        self.assertTrue(out["ok"], out)
        self.assertFalse(out["buildable"])
        self.assertIn("barite", out["not_buildable_because"])

    def test_assumptions(self):
        out = call("TRIGAAssumptionsTool", {"preset": PUBLIC_PRESET})
        self.assertTrue({"A17", "A18"} <= {a["id"] for a in out["assumptions"]})
        out = call("TRIGAAssumptionsTool", {"preset": PUBLIC_PRESET, "all": True})
        self.assertTrue(out["ok"])
        self.assertTrue(all("statement" in a and "applies" not in a for a in out["assumptions"]))
        out = call("TRIGAAssumptionsTool", {"all": True, "pnt": "thermal"})
        self.assertEqual(out["error"]["field"], "all")

    @unittest.skipUnless(restricted() is not None, "needs the restricted NETL facility pack")
    def test_the_reference_preset_is_labelled_restricted(self):
        out = call("TRIGAInputSpecTool", {"config": 8})
        self.assertEqual(out["facility_data"]["distribution"], "restricted")


class Confinement(unittest.TestCase):
    def test_case_and_sweep_names_cannot_be_paths(self):
        for name, args in (("TRIGAInputPreviewTool", {"case": "../outside"}), ("TRIGASweepResultsTool", {"sweep_name": "/tmp/x"}),
                           ("TRIGASweepBuilderTool", {"sweep_name": "a/../b", "sweep": {"rod": "TR", "positions": [0, 1]}}),
                           ("TRIGAInputBuilderTool", {"preset": PUBLIC_PRESET, "name": "../escape"})):
            with self.subTest(tool=name):
                out = call(name, args)
                self.assertFalse(out["ok"])
                self.assertEqual(out["error"]["type"], "InvalidSpec")

    def test_run_folders_stay_inside_the_workspace(self):
        for rel in ("../outside", "/etc", ""):
            with self.subTest(rel=rel):
                out = call("RunResultReaderTool", {"run_folder": rel})
                self.assertEqual((out["ok"], out["error"]["field"]), (False, "run_folder"))

    def test_a_folder_without_a_statepoint_is_reported(self):
        with tempfile.TemporaryDirectory() as ws, mock.patch.object(automc_paths, "WORKSPACE", Path(ws)):
            (Path(ws) / "case" / "run-001").mkdir(parents=True)
            out = call("RunResultReaderTool", {"run_folder": "case/run-001"})
        self.assertEqual((out["ok"], out["error"]["type"]), (False, "NoResult"))

    def test_a_missing_sweep_is_reported(self):
        out = call("TRIGASweepResultsTool", {"sweep_name": "no-such-sweep-here"})
        self.assertEqual((out["ok"], out["error"]["field"]), (False, "sweep_name"))


class BundledSkillFiles(unittest.TestCase):
    """SkillLookupTool with file: the working examples by path under src/skills, public, and nothing outside that folder."""

    def test_an_example_folder_lists_its_files_and_each_file_reads_whole(self):
        index = call("SkillLookupTool", {"file": "working_examples/index.md"})
        self.assertTrue(index["ok"], index)
        self.assertIn("`jezebel`", index["text"])
        folder = call("SkillLookupTool", {"file": "working_examples/jezebel/"})
        self.assertTrue(folder["folder"])
        self.assertTrue({"README.md", "build.py", "post.py", "expected.json"} <= set(folder["files"]), folder["files"])
        build = call("SkillLookupTool", {"file": "working_examples\\jezebel\\build.py"})
        on_disk = (automc_paths.SRC / "skills" / "working_examples" / "jezebel" / "build.py").read_text(encoding="utf-8")
        self.assertEqual((build["text"], build["facility_data"]), (on_disk, {"distribution": "public"}))
        self.assertNotIn("truncated", build)

    def test_paths_that_leave_the_skills_folder_or_do_not_exist_are_refused(self):
        for path in ("../automc_settings.py", "working_examples/../../automc_paths.py", "/etc/passwd", "C:/Windows/win.ini",
                     "~/.bashrc", "/", "working_examples/jezebel/__pycache__"):
            with self.subTest(path=path):
                out = call("SkillLookupTool", {"file": path})
                self.assertEqual((out["ok"], out["error"]["field"]), (False, "file"), out)
        missing = call("SkillLookupTool", {"file": "working_examples/jezebel/nothing.py"})
        self.assertEqual(missing["error"]["type"], "NotFound")
        self.assertIn("build.py", missing["error"]["message"])                       # it says what the folder holds
        both = call("SkillLookupTool", {"name": "x", "file": "working_examples/index.md"})
        self.assertFalse(both["ok"])

    def test_the_listing_points_at_the_bundled_files(self):
        out = call("SkillLookupTool", {})
        self.assertTrue(out["ok"], out)
        self.assertIn("working_examples/index.md", out["bundled"])

    def test_an_empty_argument_beside_the_other_means_not_given(self):
        """OpenAI's strict tool calls fill every argument (the 2026-11-02 cloud evaluation): name "" beside a file, file "" beside a
        name, or both empty for the listing."""
        self.assertEqual(call("SkillLookupTool", {"name": "", "file": "working_examples/index.md"})["file"], "working_examples/index.md")
        named = call("SkillLookupTool", {"name": "no-such-skill", "file": ""})
        self.assertEqual((named["ok"], named["error"]["field"]), (False, "name"))              # looked up as a pack skill
        self.assertIn("bundled", call("SkillLookupTool", {"name": "", "file": ""}))


def _why_no_build():
    if os.name != "posix":
        return "needs Linux or WSL"
    try:
        import openmc  # noqa: F401
    except ImportError:
        return "OpenMC is not importable in this interpreter"
    if not os.environ.get("OPENMC_CROSS_SECTIONS") and not _XS_FALLBACK.is_file():
        return "no cross-section library"
    if shutil.which("openmc") is None:
        return "the openmc executable is not on PATH"
    return None


@unittest.skipIf(_why_no_build(), _why_no_build() or "")
class InTheWorkspace(unittest.TestCase):
    """Build, sweep, preview and read results with the tools, in a temporary workspace."""

    @classmethod
    def setUpClass(cls):
        if not os.environ.get("OPENMC_CROSS_SECTIONS"):
            os.environ["OPENMC_CROSS_SECTIONS"] = str(_XS_FALLBACK)
        cls.ws = Path(tempfile.mkdtemp(prefix="automc_tools_"))
        cls.patches = [mock.patch.object(automc_paths, "WORKSPACE", cls.ws), mock.patch.object(triga_tools, "WORKSPACE", cls.ws)]
        for p in cls.patches:
            p.start()
        cls.build = call("TRIGAInputBuilderTool", {"preset": PUBLIC_PRESET, "name": "tool-case", "rods": 0.6, **TINY})
        run = cls.ws / "tool-case" / "run-001"
        run.mkdir()
        shutil.copy(cls.ws / "tool-case" / "model.xml", run)
        with open(run / "run.log", "w") as log:                       # one tiny real run, for the reader
            subprocess.run(["openmc", "-s", "2"], cwd=run, stdout=log, stderr=subprocess.STDOUT, timeout=600, check=True)

    @classmethod
    def tearDownClass(cls):
        for p in cls.patches:
            p.stop()
        shutil.rmtree(cls.ws, ignore_errors=True)

    def test_build(self):
        self.assertTrue(self.build["ok"], self.build)
        r = self.build["result"]
        self.assertEqual((r["name"], r["preset"], r["facility_data"]["distribution"]), ("tool-case", f"{PUBLIC_PRESET}@1", "public"))
        again = call("TRIGAInputBuilderTool", {"preset": PUBLIC_PRESET, "name": "tool-case", "rods": 0.6, **TINY})
        self.assertTrue(again["result"]["reused"])
        other = call("TRIGAInputBuilderTool", {"preset": PUBLIC_PRESET, "name": "tool-case", "rods": 0.7, **TINY})
        self.assertEqual((other["ok"], other["error"]["field"]), (False, "outdir"))
        forced = call("TRIGAInputBuilderTool", {"preset": PUBLIC_PRESET, "name": "tool-case", "rods": 0.7, "force": True, **TINY})
        self.assertEqual(forced["error"]["field"], "outdir")              # the folder holds a run, so even force refuses
        self.assertIn("transport runs", forced["error"]["message"])

    def test_read_the_run(self):
        out = call("RunResultReaderTool", {"run_folder": "tool-case/run-001"})
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["batches"], {"total": 3, "inactive": 1, "particles_per_batch": 1000})
        self.assertGreater(out["keff"]["value"], 0.2)
        self.assertGreater(out["keff"]["sigma"], 0.0)
        self.assertEqual(len(out["k_by_batch"]), 3)
        self.assertTrue(out["plausible"], out)
        self.assertEqual(out["built_from"]["case"], "tool-case")
        self.assertEqual(out["facility_data"]["distribution"], "public")

    def test_preview(self):
        out = call("TRIGAInputPreviewTool", {"case": "tool-case", "which": ["core_xy"], "pixels": 200})
        self.assertTrue(out["ok"], out)
        self.assertEqual([p["name"] for p in out["previews"]], ["core_xy"])
        self.assertTrue(Path(out["previews"][0]["path"]).is_file())

    def test_sweep_and_its_results_before_any_run(self):
        out = call("TRIGASweepBuilderTool", {"sweep_name": "tool-sweep", "preset": PUBLIC_PRESET, **TINY,
                                             "sweep": {"rod": "TR", "positions": [0.2, 0.8]}})
        self.assertTrue(out["ok"], out)
        self.assertEqual([s["folder"] for s in out["sweep"]["states"]], ["state-001", "state-002"])
        res = call("TRIGASweepResultsTool", {"sweep_name": "tool-sweep", "beta_eff": 0.007})
        self.assertTrue(res["ok"], res)
        self.assertEqual([s["missing"] for s in res["states"]], [True, True])
        self.assertFalse(res["complete"])
        self.assertEqual(res["facility_data"]["distribution"], "public")
        bad = call("TRIGASweepBuilderTool", {"sweep_name": "tool-sweep-2", "sweep": {"rod": "TR", "values": [1]}})
        self.assertEqual(bad["error"]["field"], "sweep.values")
