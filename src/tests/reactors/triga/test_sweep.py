"""
Sweeps (Block 2, B2-2).

Part 1 runs everywhere: planning, validation before any build, names, seeds, the run script, the CLI error paths and the worth arithmetic.
Part 2 builds a real three-state sweep in the WSL automc environment (about 35 s) and checks the folder, the manifest, reuse and the
refusal to overwrite a different sweep. No transport is run.
"""
import json
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tools.reactors.triga import (InvalidSpec, UnsupportedFeature, build_input, build_sweep, field_states, list_states, load_sweep, plan_sweep,
                   rod_states)

from tests.reactors.triga.test_build_config0 import SKIP, setUpModule as _environment  # noqa: E402  (environment guard, cross sections)
from tests.reactors.triga._packs import REF, ref_port_lengths, require_restricted  # noqa: E402


def setUpModule():
    require_restricted()
    _environment()

from automc_paths import SRC  # noqa: E402


def cli(*args):
    p = subprocess.run([sys.executable, "-m", "automc", "triga", *args], cwd=SRC, capture_output=True, text=True, timeout=300)
    return p.returncode, p.stdout, p.stderr


def load_read_sweep():
    from tools.reactors.triga import sweep_results
    return sweep_results


class Planning(unittest.TestCase):
    def test_rod_sweep_states(self):
        p = plan_sweep(rod_states("TR", [0, 0.25, 0.5, 1.0]), name="demo")
        self.assertEqual((p.kind, p.varied), ("rod", "transient in fraction"))
        self.assertEqual([s.spec.rods["transient"] for s in p.states], [0.0, 0.25, 0.5, 1.0])
        self.assertEqual([s.name for s in p.states], ["demo-s001", "demo-s002", "demo-s003", "demo-s004"])
        self.assertEqual([s.folder for s in p.states], ["state-001", "state-002", "state-003", "state-004"])
        for s in p.states:                                   # only the swept rod differs from the base
            self.assertEqual({k: v for k, v in s.spec.rods.items() if k != "transient"},
                             {k: v for k, v in REF["rods"].items() if k != "transient"})

    def test_every_rod_unit_gives_the_same_state(self):
        a = plan_sweep(rod_states("transient", [480], "console"), name="a").states[0]
        b = plan_sweep(rod_states("transient", [0.5], "fraction"), name="b").states[0]
        c = plan_sweep(rod_states("transient", [50], "percent"), name="c").states[0]
        self.assertEqual(a.spec_hash, b.spec_hash)
        self.assertEqual(b.spec_hash, c.spec_hash)

    def test_all_rods_together(self):
        p = plan_sweep(rod_states("all", [0.3, 0.7]), name="all")
        self.assertEqual(p.states[1].spec.rods, {"transient": 0.7, "regulating": 0.7, "shim_1": 0.7, "shim_2": 0.7})
        self.assertEqual(p.varied, "all rods in fraction")

    def test_common_values_apply_to_every_state(self):
        p = plan_sweep(rod_states("TR", [0.1, 0.9]), name="c", common={"rods": {"S1": 0.2}, "conditions": {"water_temperature_K": 300.0}})
        for s in p.states:
            self.assertEqual(s.spec.rods["shim_1"], 0.2)
            self.assertEqual(s.spec.conditions.water_temperature_K, 300.0)

    def test_same_seed_in_every_state_and_it_is_explicit(self):
        p = plan_sweep(rod_states("TR", [0.1, 0.9]), name="seed")
        self.assertEqual({s.spec.run.seed for s in p.states}, {1})
        self.assertEqual(p.seed, 1)
        q = plan_sweep(rod_states("TR", [0.1, 0.9]), name="seed", common={"run": {"seed": 777}})
        self.assertEqual({s.spec.run.seed for s in q.states}, {777})
        self.assertEqual(q.seed, 777)

    def test_a_state_may_vary_the_seed_itself(self):
        p = plan_sweep(list_states([{"run": {"seed": 1}}, {"run": {"seed": 2}}]), name="seeds")
        self.assertEqual([s.spec.run.seed for s in p.states], [1, 2])

    def test_everything_is_checked_before_any_build(self):
        with self.assertRaises(InvalidSpec) as ctx:
            plan_sweep(rod_states("TR", [0.1, 0.5, 2.0, 0.7]), name="bad")
        self.assertIn("state 3", str(ctx.exception))
        self.assertIn("rods.transient", str(ctx.exception))
        with self.assertRaises(InvalidSpec) as ctx:
            plan_sweep(rod_states("TR", [0.5, 50], "fraction"), name="bad")                 # 50 fraction is out of range
        self.assertIn("state 2", str(ctx.exception))

    def test_duplicates_are_refused_even_across_units(self):
        with self.assertRaises(InvalidSpec) as ctx:
            plan_sweep(list_states([{"rods": {"TR": 0.5}}, {"rods": {"TR": {"value": 50, "unit": "percent"}}}]), name="dup")
        self.assertIn("states 1 and 2", str(ctx.exception))

    def test_unsupported_states_are_refused_at_planning(self):
        with self.assertRaises(UnsupportedFeature):
            plan_sweep(list_states([{"config": 0}, {"environment": {"detector_geometry": False}}]), name="cfg")

    def test_size_and_name_limits(self):
        with self.assertRaises(InvalidSpec) as ctx:
            plan_sweep(rod_states("TR", [i / 100 for i in range(60)]), name="many")
        self.assertIn("limit", str(ctx.exception))
        plan_sweep(rod_states("TR", [i / 100 for i in range(60)]), name="many", max_states=60)
        for bad in ("../x", "a b", "", "x" * 51):
            with self.subTest(name=bad):
                with self.assertRaises(InvalidSpec):
                    plan_sweep(rod_states("TR", [0.1]), name=bad)

    def test_front_end_input_errors(self):
        cases = [(lambda: rod_states("TR", []), "positions"), (lambda: rod_states("TR", ["a"]), "positions[0]"),
                 (lambda: rod_states("TR", [0.1], "furlongs"), "unit"), (lambda: rod_states("nope", [0.1]), "rod"),
                 (lambda: field_states("", [1]), "field"), (lambda: field_states("a..b", [1]), "field"),
                 (lambda: field_states("conditions.x", []), "values"), (lambda: list_states([]), "states"),
                 (lambda: list_states(["x"]), "states")]
        for fn, field in cases:
            with self.subTest(field=field):
                with self.assertRaises(InvalidSpec) as ctx:
                    fn()
                self.assertTrue(ctx.exception.field.startswith(field) or field in ctx.exception.field, ctx.exception.field)

    def test_field_sweep_nests_the_path(self):
        kind, varied, states, labels = field_states("conditions.fuel_temperature_K", [294.0, 600.0])
        self.assertEqual(states, [{"conditions": {"fuel_temperature_K": 294.0}}, {"conditions": {"fuel_temperature_K": 600.0}}])
        p = plan_sweep((kind, varied, states, labels), name="temp")
        self.assertEqual([s.spec.conditions.fuel_temperature_K for s in p.states], [294.0, 600.0])
        self.assertEqual(p.kind, "field")
        with self.assertRaises(InvalidSpec) as ctx:
            plan_sweep(field_states("conditions.fuel_temperature_K", [294, -5]), name="temp")
        self.assertIn("state 2", str(ctx.exception))

    def test_list_states_labels(self):
        p = plan_sweep(list_states([{"label": "start", "rods": {"TR": 0.0}}, {"rods": {"TR": 0.5}}]), name="seq")
        self.assertEqual([s.label for s in p.states], ["start", "state 2"])

    def test_estimate_adds_the_states(self):
        p = plan_sweep(rod_states("TR", [0.1, 0.5, 0.9]), name="est")
        one = p.states[0].spec
        from tools.reactors.triga import advice
        single = advice.run_time_estimate(one)
        self.assertAlmostEqual(p.estimate["seconds_low"], 3 * single["seconds_low"], delta=0.2)
        self.assertEqual(p.estimate["states"], 3)


class RunScript(unittest.TestCase):
    def test_script_lists_every_state_and_uses_relative_paths_only(self):
        from tools.reactors.triga.sweep import run_script_text
        p = plan_sweep(rod_states("TR", [0, 0.5, 1]), name="run")
        text = run_script_text(p, 8)
        self.assertTrue(text.startswith("#!/usr/bin/env bash"))
        self.assertIn("STATES=(state-001 state-002 state-003)", text)
        self.assertIn("THREADS=${THREADS:-8}", text)
        self.assertIn("openmc -s", text)
        self.assertIn("skipping", text)
        for forbidden in ("/mnt/", "C:\\", str(p.workspace)):
            self.assertNotIn(forbidden, text)

    def test_labels_cannot_inject_commands(self):
        from tools.reactors.triga.sweep import run_script_text
        nasty = "x'; rm -rf / #"
        p = plan_sweep(list_states([{"label": nasty, "rods": {"TR": 0.1}}, {"rods": {"TR": 0.2}}]), name="nasty")
        text = run_script_text(p, 4)
        lines = text.split("LABELS=(\n")[1].split("\n)")[0].split("\n")
        self.assertEqual(shlex.split(lines[0].strip())[0], nasty)             # the label survives quoting as one harmless word
        self.assertNotIn("\n rm -rf", text)


class WorthArithmetic(unittest.TestCase):
    def setUp(self):
        self.rs = load_read_sweep()

    def test_worth_sign_and_size(self):
        w, e = self.rs.worth_pcm(1.00000, 0.0, 1.00500, 0.0)
        self.assertAlmostEqual(w, 1.0e5 * (1 / 1.0 - 1 / 1.005), places=6)
        self.assertGreater(w, 0)
        self.assertAlmostEqual(self.rs.worth_pcm(1.005, 0, 1.0, 0)[0], -w, places=6)
        self.assertEqual(e, 0.0)

    def test_error_combines_both_states(self):
        _, e = self.rs.worth_pcm(1.0, 0.0003, 1.0, 0.0004)
        self.assertAlmostEqual(e, 1.0e5 * 0.0005, places=6)

    def test_order_check(self):
        good = [{"label": "a", "k": 1.00, "s": 0.0005}, {"label": "b", "k": 1.02, "s": 0.0005}, {"label": "c", "k": 1.05, "s": 0.0005}]
        self.assertEqual(self.rs.order_problems(good), [])
        noise = [{"label": "a", "k": 1.0200, "s": 0.0010}, {"label": "b", "k": 1.0195, "s": 0.0010}]
        self.assertEqual(self.rs.order_problems(noise), [])                  # a fall within noise is not a failure
        bad = [{"label": "a", "k": 1.05, "s": 0.0005}, {"label": "b", "k": 1.00, "s": 0.0005}]
        problems = self.rs.order_problems(bad)
        self.assertEqual(len(problems), 1)
        self.assertIn("a to b", problems[0])

    def test_rod_position_only_for_rod_sweeps(self):
        state = {"rods": {"transient": 0.25, "regulating": 0.5}}
        self.assertEqual(self.rs.rod_position({"kind": "rod", "varied": "transient in fraction"}, state), 0.25)
        self.assertEqual(self.rs.rod_position({"kind": "rod", "varied": "all rods in percent"}, state), 0.25)
        self.assertIsNone(self.rs.rod_position({"kind": "field", "varied": "conditions.x"}, state))

    def test_missing_sweep_json(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(self.rs.main([d]), 2)


class CommandLineErrors(unittest.TestCase):
    def test_exit_codes_and_fields(self):
        cases = [
            (["--name", "x"], "--sweep-rod / --sweep-field / --states-file"),
            (["--name", "x", "--sweep-rod", "TR", "--sweep-field", "a.b"], "--sweep-rod / --sweep-field / --states-file"),
            (["--name", "x", "--sweep-rod", "TR"], "--positions"),
            (["--name", "x", "--sweep-rod", "TR", "--positions", "a,b"], "--positions"),
            (["--name", "x", "--sweep-rod", "TR", "--positions", "0,2"], "rods.transient"),
            (["--name", "x", "--sweep-rod", "TR", "--positions", "0.5,0.5"], "states"),
            (["--sweep-rod", "TR", "--positions", "0,1"], "--name"),
            (["--name", "x", "--sweep-field", "conditions.fuel_temperature_K"], "--values"),
            (["--name", "x", "--sweep-field", "conditions.fuel_temperature_K", "--values", "[1,"], "--values"),
            (["--name", "x", "--states-file", "does-not-exist.json"], "--states-file"),
        ]
        for args, field in cases:
            with self.subTest(args=args):
                code, out, _ = cli("sweep", *args, "--json")
                self.assertEqual(code, 2)
                self.assertEqual(json.loads(out)["error"]["field"], field)

    def test_help_has_examples(self):
        code, out, _ = cli("sweep", "-h")
        self.assertEqual(code, 0)
        self.assertIn("--sweep-rod", out)
        self.assertIn("run_sweep.sh", out)


# ======================================================================================================================
# real builds
# ======================================================================================================================
@unittest.skipIf(SKIP, SKIP or "")
class BuiltSweep(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ws = Path(tempfile.mkdtemp(prefix="triga_sweep_"))
        cls.common = {"run": {"particles": 1000, "batches": 3, "inactive": 1}}
        cls.plan = plan_sweep(rod_states("TR", [0.0, 0.5, 1.0]), name="rod-tr", common=cls.common, workspace=cls.ws)
        cls.manifest = build_sweep(cls.plan)

    def test_folder_layout(self):
        f = self.ws / "rod-tr"
        self.assertTrue((f / "sweep.json").is_file())
        self.assertTrue((f / "run_sweep.sh").is_file())
        for i in (1, 2, 3):
            for name in ("model.xml", "result.json", "build.log"):
                self.assertTrue((f / f"state-{i:03d}" / name).is_file(), (i, name))
        self.assertEqual(sorted(p.name for p in f.iterdir() if p.name.startswith(".")), [])           # no temporary leftovers

    def test_manifest(self):
        m = load_sweep(self.ws / "rod-tr")
        self.assertEqual((m["name"], m["kind"], m["varied"], m["seed"]), ("rod-tr", "rod", "transient in fraction", 1))
        self.assertEqual([s["rods"]["transient"] for s in m["states"]], [0.0, 0.5, 1.0])
        self.assertEqual([s["rods_console"]["transient"] for s in m["states"]], [0.0, 480.0, 960.0])
        self.assertEqual([s["reused"] for s in self.manifest["states"]], [False, False, False])      # the first call built them all
        self.assertEqual(len({s["spec_hash"] for s in m["states"]}), 3)
        self.assertEqual(m["estimate"]["states"], 3)
        self.assertIn("outdir", m)

    def test_each_state_is_an_ordinary_build_with_the_same_seed(self):
        for i, frac in enumerate((0.0, 0.5, 1.0), start=1):
            r = json.loads((self.ws / "rod-tr" / f"state-{i:03d}" / "result.json").read_text())
            self.assertEqual(r["spec"]["rods"]["transient"], frac)
            self.assertEqual(r["spec"]["rods"]["regulating"], REF["rods"]["regulating"])
            self.assertEqual(r["spec"]["run"]["seed"], 1)
            self.assertEqual(r["name"], f"rod-tr-s{i:03d}")
            self.assertEqual(r["summary"]["settings"]["seed"], 1)

    def test_only_the_transient_rod_moves(self):
        z = [json.loads((self.ws / "rod-tr" / f"state-{i:03d}" / "result.json").read_text())["summary"]["rods"] for i in (1, 2, 3)]
        self.assertLess(z[0]["transient"]["library_z_cm"], z[1]["transient"]["library_z_cm"])
        self.assertLess(z[1]["transient"]["library_z_cm"], z[2]["transient"]["library_z_cm"])
        for rod in ("regulating", "shim_1", "shim_2"):
            self.assertEqual({zz[rod]["library_z_cm"] for zz in z}, {z[0][rod]["library_z_cm"]})

    def test_the_run_script_matches_the_states(self):
        text = (self.ws / "rod-tr" / "run_sweep.sh").read_text()
        self.assertIn("STATES=(state-001 state-002 state-003)", text)
        self.assertEqual(text.count("transient = "), 3)

    def test_a_second_identical_call_reuses_everything(self):
        again = build_sweep(plan_sweep(rod_states("TR", [0.0, 0.5, 1.0]), name="rod-tr", common=self.common, workspace=self.ws))
        self.assertEqual([s["reused"] for s in again["states"]], [True, True, True])

    def test_a_different_sweep_under_the_same_name_is_refused_and_runs_survive(self):
        run = self.ws / "rod-tr" / "state-002" / "run-001"
        run.mkdir()
        (run / "statepoint.3.h5").write_bytes(b"result")
        other = plan_sweep(rod_states("TR", [0.0, 0.25, 1.0]), name="rod-tr", common=self.common, workspace=self.ws)
        with self.assertRaises(InvalidSpec) as ctx:
            build_sweep(other)
        self.assertIn("different sweep", str(ctx.exception))
        self.assertEqual((run / "statepoint.3.h5").read_bytes(), b"result")
        self.assertFalse((self.ws / "rod-tr" / "state-002" / "model.xml").read_text() == "")

    def test_a_non_sweep_folder_is_not_taken_over(self):
        other = self.ws / "something-else"
        other.mkdir()
        (other / "notes.txt").write_text("mine")
        with self.assertRaises(InvalidSpec):
            build_sweep(plan_sweep(rod_states("TR", [0.2, 0.8]), name="something-else", common=self.common, workspace=self.ws))
        self.assertEqual((other / "notes.txt").read_text(), "mine")

    def test_force_refuses_to_delete_transport_runs(self):
        r = build_input("openmc", "netl", name="has-runs", workspace=self.ws, **self.common)
        run = Path(r.outdir) / "run-001"
        run.mkdir()
        (run / "statepoint.3.h5").write_bytes(b"result")
        with self.assertRaises(InvalidSpec) as ctx:
            build_input("openmc", "netl", name="has-runs", workspace=self.ws, force=True, **self.common)
        self.assertIn("transport runs", str(ctx.exception))
        self.assertEqual((run / "statepoint.3.h5").read_bytes(), b"result")

    def test_command_line_reuses_the_same_sweep_and_prints_the_run_commands(self):
        code, out, err = cli("sweep", "--name", "rod-tr", "--sweep-rod", "TR", "--positions", "0,0.5,1", "--workspace", str(self.ws),
                             "--set", "run.particles=1000", "--set", "run.batches=3", "--set", "run.inactive=1")
        self.assertEqual(code, 0, err)
        self.assertIn("reused identical builds: sweep rod-tr", out)
        self.assertIn("Nothing was run", out)
        self.assertIn("run_sweep.sh", out)
        self.assertIn("triga read-sweep", out)
        self.assertIn("same random seed 1", out)


if __name__ == "__main__":
    unittest.main()
