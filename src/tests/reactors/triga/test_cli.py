"""The command line, run as a real subprocess from AutoMC/src. No libraries needed."""
import json
import subprocess
import sys
import unittest
from pathlib import Path

from automc_paths import SRC  # noqa: E402
from tests.reactors.triga._packs import REF, ref_port_lengths, require_restricted  # noqa: E402


def setUpModule():
    require_restricted()


def run(*args):
    p = subprocess.run([sys.executable, "-m", "automc", "triga", *args], cwd=SRC, capture_output=True, text=True, timeout=120)
    return p.returncode, p.stdout, p.stderr


class Spec(unittest.TestCase):
    def test_default_spec(self):
        code, out, _ = run("spec")
        self.assertEqual(code, 0)
        d = json.loads(out)
        self.assertEqual(d["preset"], "netl_boc505@1")
        self.assertEqual(d["rods"]["transient"], REF["rods"]["transient"])

    def test_rod_in_console_units(self):
        code, out, _ = run("spec", "--rod", "S1=600:console", "--views")
        self.assertEqual(code, 0)
        d = json.loads(out)
        self.assertEqual(d["spec"]["rods"]["shim_1"], 0.625)
        self.assertEqual(d["rods_all_units"]["shim_1"]["console"], 600.0)
        self.assertEqual(len(d["spec_hash"]), 64)
        self.assertEqual(d["beam_port_lengths_cm"], dict(zip(("ports_2_to_4", "port_1_5"), ref_port_lengths())))

    def test_all_rods_then_one_override(self):
        _, out, _ = run("spec", "--rods", "0.3", "--rod", "TR=0.9")
        r = json.loads(out)["rods"]
        self.assertEqual((r["transient"], r["regulating"], r["shim_1"], r["shim_2"]), (0.9, 0.3, 0.3, 0.3))

    def test_same_rod_twice_is_an_error(self):
        code, _, err = run("spec", "--rod", "TR=0.1", "--rod", "transient=0.2")
        self.assertEqual(code, 2)
        self.assertIn("more than once", err)

    def test_config_and_3L_spelling(self):
        _, out, _ = run("spec", "--config", "8")
        d = json.loads(out)
        self.assertEqual((d["pnt"], d["three_el"]), ("thermal", "fast"))
        _, out, _ = run("spec", "--3L", "fast")
        d = json.loads(out)
        self.assertEqual((d["three_el"], d["config"]), ("fast", 5))

    def test_set_by_path_and_temperature_method(self):
        _, out, _ = run("spec", "--set", "conditions.water_temperature_K=320", "--set", "run.seed=7", "--views")
        d = json.loads(out)
        self.assertEqual(d["spec"]["conditions"]["water_temperature_K"], 320.0)
        self.assertEqual(d["resolved_temperature_method"], "interpolation")
        self.assertEqual(d["run_effective"]["seed"], 7)

    def test_invalid_input_exits_2_and_names_the_field(self):
        code, out, err = run("spec", "--rod", "TR=2")
        self.assertEqual(code, 2)
        self.assertIn("rods.TR", err)
        self.assertEqual(out, "")
        for bad in (["--rod", "nope"], ["--rod", "TR=abc"], ["--rod", "TR=1:furlongs"], ["--set", "novalue"], ["--config", "12"],
                    ["--preset", "missing"], ["--rods", "x"]):
            with self.subTest(args=bad):
                self.assertEqual(run("spec", *bad)[0], 2)


class BuildCommandErrors(unittest.TestCase):
    """Everything that is refused before a worker starts, so these run anywhere."""

    def test_help_has_examples_and_the_overwrite_flag(self):
        code, out, _ = run("build", "-h")
        self.assertEqual(code, 0)
        for needle in ("examples", "--force", "--json", "--timeout", "--allow-outside", "NAME=VALUE", "console"):
            self.assertIn(needle, out)

    def test_exit_codes(self):
        self.assertEqual(run("build", "--rod", "TR=2")[0], 2)
        self.assertEqual(run("build", "--set", "environment.detector_geometry=false")[0], 3)
        self.assertEqual(run("build", "--code", "mcnp")[0], 3)
        self.assertEqual(run("build", "--code", "serpent")[0], 2)
        self.assertEqual(run("preview", "nothere")[0], 2)
        self.assertEqual(run("preview", "x", "--pixels", "50")[0], 2)

    def test_messages_name_the_problem(self):
        _, _, err = run("build", "--set", "environment.detector_geometry=false")
        self.assertTrue(err.startswith("unsupported: environment.barite / environment.detector_geometry:"), err)
        self.assertIn("both must be true", err)
        _, _, err = run("build", "--rod", "TR=2")
        self.assertTrue(err.startswith("invalid: rods.TR:"), err)

    def test_json_errors_go_to_stdout_with_the_same_exit_code(self):
        code, out, err = run("build", "--set", "environment.detector_geometry=false", "--json")
        self.assertEqual((code, err), (3, ""))
        e = json.loads(out)
        self.assertEqual(e["ok"], False)
        self.assertEqual((e["error"]["type"], e["error"]["field"], e["error"]["exit_code"]), ("UnsupportedFeature", "environment.barite / environment.detector_geometry", 3))
        code, out, _ = run("build", "--rod", "S1=1200:console", "--json")
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(out)["error"]["field"], "rods.S1")

    def test_spec_views_shows_warnings_and_the_estimate(self):
        _, out, _ = run("spec", "--rods", "0", "--set", 'run.size="production"', "--views")
        d = json.loads(out)
        self.assertTrue(any("deeply subcritical" in w for w in d["warnings"]))
        self.assertTrue(any("estimated run time is long" in w for w in d["warnings"]))
        self.assertEqual(d["run_estimate"]["histories"], 750_000_000)
        _, out, _ = run("spec", "--views")
        self.assertEqual(json.loads(out)["warnings"], [])


class OtherCommands(unittest.TestCase):
    def test_presets(self):
        code, out, _ = run("presets")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)[0]["preset"], "netl_boc505@1")

    def test_assumptions(self):
        _, out, _ = run("assumptions", "--all")
        self.assertEqual([a["id"] for a in json.loads(out)], [f"A{i}" for i in range(1, 19)])
        _, out, _ = run("assumptions")
        self.assertIn("A10", {a["id"] for a in json.loads(out)})
        _, out, _ = run("assumptions", "--config", "1")
        self.assertIn("A7", {a["id"] for a in json.loads(out)})

    def test_version(self):
        code, out, err = run("--version")
        self.assertEqual(code, 0)
        self.assertRegex(out + err, r"triga \d+\.\d+\.\d+")


if __name__ == "__main__":
    unittest.main()
