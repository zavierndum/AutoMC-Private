"""
The job service with real, tiny runs (decision D6: tests may run 1,000-history transport). A three-nuclide sphere loads in seconds, so
every control path runs for real: start, finish, pause and resume, stop and continue from the statepoint, cancel, a crash, a failure,
a second start refused, a sequence that skips a finished case, and the command line's approval question. Needs the WSL automc
environment; skips elsewhere.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import automc_paths
from automc_paths import SRC
from tools.openmc import jobs, runs
from tools.openmc.errors import OpenMCError

from tests.reactors.triga.test_build_config0 import _XS_FALLBACK  # noqa: E402


def _why_skip():
    if os.name != "posix":
        return "needs Linux or WSL"
    try:
        import openmc.lib  # noqa: F401
    except ImportError:
        return "OpenMC is not importable in this interpreter (use the WSL automc environment)"
    if shutil.which("openmc") is None:
        return "the openmc executable is not on PATH"
    if not os.environ.get("OPENMC_CROSS_SECTIONS") and not _XS_FALLBACK.is_file():
        return "no cross-section library"
    return None


SKIP = _why_skip()


def write_sphere(folder, batches=8, particles=300, seed=1, tally=True):
    """A bare sphere of water with a little U-235: three nuclides, so OpenMC starts in seconds. With a 4 x 4 x 4 mesh tally of flux and
    fission, so a restart is checked for its tallies as well as its k."""
    import openmc
    folder.mkdir(parents=True, exist_ok=True)
    openmc.reset_auto_ids()
    m = openmc.Material(name="fuel_water")
    m.set_density("g/cm3", 1.0)
    m.add_nuclide("H1", 2.0)
    m.add_nuclide("O16", 1.0)
    m.add_nuclide("U235", 0.004)
    sphere = openmc.Sphere(r=40.0, boundary_type="vacuum")
    geometry = openmc.Geometry([openmc.Cell(fill=m, region=-sphere)])
    s = openmc.Settings()
    s.particles, s.batches, s.inactive = particles, batches, 2
    if seed is not None:
        s.seed = seed
    s.source = openmc.IndependentSource(space=openmc.stats.Point())
    s.output = {"tallies": False}
    tallies = openmc.Tallies()
    if tally:
        mesh = openmc.RegularMesh()
        mesh.lower_left, mesh.upper_right, mesh.dimension = (-40.0, -40.0, -40.0), (40.0, 40.0, 40.0), (4, 4, 4)
        t = openmc.Tally(name="flux_mesh")
        t.filters = [openmc.MeshFilter(mesh)]
        t.scores = ["flux", "fission"]
        tallies.append(t)
    openmc.Model(geometry=geometry, materials=openmc.Materials([m]), settings=s, tallies=tallies).export_to_model_xml(
        str(folder / "model.xml"))


def wait_for(fn, timeout=120.0, every=0.3):
    t = time.time()
    while time.time() - t < timeout:
        out = fn()
        if out:
            return out
        time.sleep(every)
    raise AssertionError("timed out waiting")


@unittest.skipIf(SKIP, SKIP or "")
class Runs(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not os.environ.get("OPENMC_CROSS_SECTIONS"):
            os.environ["OPENMC_CROSS_SECTIONS"] = str(_XS_FALLBACK)
        cls.tmp = tempfile.mkdtemp(prefix="automc_runs_")
        cls.ws = Path(cls.tmp)
        cls.patch = mock.patch.object(automc_paths, "WORKSPACE", cls.ws)
        cls.patch.start()
        for name in ("plain", "paused", "stopped", "cancelled", "crash", "fail", "seq-a", "seq-b", "seq-c", "cli"):
            write_sphere(cls.ws / name)

    @classmethod
    def tearDownClass(cls):
        for j in jobs.list_jobs(jobs.home(cls.ws), active_only=True):                 # never leave a worker behind
            try:
                runs.cancel(j["job_id"])
            except OpenMCError:
                pass
        cls.patch.stop()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def start(self, case, env=None, **kw):
        with mock.patch.dict(os.environ, env or {}):
            return runs.start(runs.plan_run(case=case, threads=2, **kw), approved_by="test")

    def finished(self, job_id, timeout=180):
        return wait_for(lambda: (lambda j: j if j["status"] not in jobs.ACTIVE else None)(runs.check(job_id=job_id)), timeout)

    def at_batch(self, job_id, batch):
        return wait_for(lambda: (lambda j: j if (j["batches"]["done"] or 0) >= batch else None)(runs.check(job_id=job_id)))

    def k_by_batch(self, run_folder):
        import openmc
        with openmc.StatePoint(str(sorted(Path(run_folder).glob("statepoint.8.h5"))[0])) as sp:
            return list(sp.k_generation), (sp.keff.n, sp.keff.s)

    def assert_same_histories(self, ref, other):
        """The same batches and the same tallies as the plain run (same model and seed), whatever happened to `other` on the way."""
        from tools.openmc.results import K_TOL, STD_TOL, tally_differences
        self.assertLess(max(abs(x - y) for x, y in zip(self.k_by_batch(ref)[0], self.k_by_batch(other)[0])), K_TOL)
        t = tally_differences(ref, other)
        self.assertEqual((t["count"], t["only_a"], t["only_b"], t["other_bins"]), (1, [], [], []))
        self.assertLess(t["mean"], K_TOL)
        self.assertLess(t["std"], STD_TOL)

    # ---- the plain path --------------------------------------------------------------------------------------------------
    def test_a_run_finishes_and_matches_the_openmc_executable(self):
        job = self.start("plain")
        self.assertEqual((job["status"], job["approved_by"], job["run_folder"]), ("running", "test", "plain/run-001"))
        done = self.finished(job["job_id"])
        self.assertEqual(done["status"], "success", done)
        self.assertEqual(done["batches"]["done"], 8)
        self.assertGreater(done["keff"]["sigma"], 0)
        rf = self.ws / "plain" / "run-001"
        self.assertEqual(jobs.read_sentinel(rf / "run.log"), (0, "success", 1))
        self.assertEqual([e["batch"] for e in jobs.read_events(rf / "events.jsonl") if e["event"] == "batch"], list(range(1, 9)))
        exe = self.ws / "plain-exe"
        exe.mkdir()
        shutil.copy(rf / "model.xml", exe)
        subprocess.run(["openmc", "-s", "2"], cwd=exe, capture_output=True, check=True, timeout=300)
        (ka, ea), (kb, eb) = self.k_by_batch(rf), self.k_by_batch(exe)
        self.assertEqual(len(ka), len(kb))
        self.assertLess(max(abs(x - y) for x, y in zip(ka, kb)), 1e-10)          # the same histories, batch by batch
        self.assertAlmostEqual(ea[0], eb[0], places=10)
        self.assertEqual(json.loads((rf / "control.json").read_text())["seq"], 0)

    def test_pause_then_resume(self):
        job = self.start("paused", env={"AUTOMC_WORKER_TEST_DELAY_S": "0.6"})
        self.at_batch(job["job_id"], 2)
        runs.control(job["job_id"], "pause")
        j = wait_for(lambda: (lambda j: j if j["status"] == "paused" else None)(runs.check(job_id=job["job_id"])))
        held = j["batches"]["done"]
        time.sleep(2.0)
        j = runs.check(job_id=job["job_id"])
        self.assertEqual(j["batches"]["done"], held)                                   # paused means no batches
        self.assertTrue((self.ws / "paused" / "run-001" / f"statepoint.{held}.h5").is_file())
        self.assertNotIn("remaining", j)                                               # no time counts down while it is held
        self.assertIn("remaining_once_resumed", j)
        self.assertIn(f"statepoint.{held}.h5", j["next"])
        self.assertIn(f"run resume {job['job_id']}", j["next"])
        runs.control(job["job_id"], "resume")
        done = self.finished(job["job_id"])
        self.assertEqual((done["status"], done["batches"]["done"]), ("success", 8))
        names = [e["event"] for e in jobs.read_events(self.ws / "paused" / "run-001" / "events.jsonl")]
        self.assertLess(names.index("paused"), names.index("resumed"))
        ref = self.ws / "plain" / "run-001"
        if (ref / "statepoint.8.h5").is_file():                                       # same model and seed as the plain run
            self.assert_same_histories(ref, self.ws / "paused" / "run-001")

    def test_stop_then_continue_from_the_statepoint_gives_the_same_batches(self):
        job = self.start("stopped", env={"AUTOMC_WORKER_TEST_DELAY_S": "0.6"})
        self.at_batch(job["job_id"], 3)
        runs.control(job["job_id"], "stop")
        stopped = self.finished(job["job_id"])
        self.assertEqual(stopped["status"], "stopped", stopped)
        self.assertIn("continue from its last statepoint (statepoint.", stopped["next"])
        with self.assertRaises(OpenMCError) as ctx:
            runs.control(job["job_id"], "resume")                                   # a dead run does not resume without approval
        self.assertIn("needs the user's approval", str(ctx.exception))
        plan = runs.plan_restart(job["job_id"], threads=2)
        self.assertEqual(plan["kind"], "restart")
        self.assertLess(plan["histories"], 300 * 8)
        with self.assertRaises(OpenMCError):
            runs.restart(plan, approved_by="")
        runs.start(plan, approved_by="test")
        done = self.finished(job["job_id"])
        self.assertEqual((done["status"], done["segment"], done["batches"]["done"]), ("success", 2, 8))
        ref = self.ws / "plain" / "run-001"
        if (ref / "statepoint.8.h5").is_file():                                       # same model and seed as the plain run
            self.assert_same_histories(ref, self.ws / "stopped" / "run-001")

    def test_cancel_kills_the_process_group(self):
        job = self.start("cancelled", env={"AUTOMC_WORKER_TEST_DELAY_S": "1.0"})
        self.at_batch(job["job_id"], 1)
        out = runs.cancel(job["job_id"])
        self.assertEqual(out["status"], "cancelled")
        self.assertFalse(jobs.process_alive(job["pid"] if "pid" in job else jobs.get_job(jobs.home(self.ws), job["job_id"])["pid"]))

    def test_a_crash_is_interrupted_and_a_failure_is_failed(self):
        crash = self.finished(self.start("crash", env={"AUTOMC_WORKER_TEST_FAIL": "crash"})["job_id"])
        self.assertEqual(crash["status"], "interrupted", crash)
        fail = self.finished(self.start("fail", env={"AUTOMC_WORKER_TEST_FAIL": "init"})["job_id"])
        self.assertEqual(fail["status"], "failed", fail)
        self.assertIn("forced failure", fail["error"])
        # the check of a failed run carries the end of its log, and the tool's result the knowledge base entries that match it
        from tools import registry
        out = registry.call("OpenMCRunCheckTool", {"job_id": fail["job_id"]})
        self.assertTrue(any("status=failed" in line for line in out["job"]["log_tail"]), out)
        self.assertIn("status=failed", [k["matched"] for k in out.get("known_errors", [])])

    def test_a_second_start_is_refused_while_one_runs(self):
        first = self.start("seq-c", env={"AUTOMC_WORKER_TEST_DELAY_S": "0.5"})
        try:
            with self.assertRaises(OpenMCError) as ctx:
                self.start("plain")
            self.assertEqual(ctx.exception.kind, "NotAllowed")
            plan = runs.plan_run(case="plain", threads=1)
            self.assertTrue(any("another run is active" in w for w in plan["warnings"]))
        finally:
            runs.cancel(first["job_id"])

    def test_a_plan_says_which_seed_a_model_without_one_gets(self):
        write_sphere(self.ws / "no-seed", seed=None)
        self.assertIn("no seed in model.xml (OpenMC's default, 1)", runs.plan_run(case="no-seed", threads=2)["text"])
        self.assertIn("seed 1,", runs.plan_run(case="plain", threads=2)["text"])

    def test_a_stale_plan_is_refused(self):
        plan = runs.plan_run(case="cli", threads=2)
        write_sphere(self.ws / "cli", particles=301)
        with self.assertRaises(OpenMCError) as ctx:
            runs.start(plan, approved_by="test")
        self.assertIn("changed since this plan was made", str(ctx.exception))

    # ---- sequences ------------------------------------------------------------------------------------------------------
    def test_a_sequence_runs_its_cases_in_turn_and_skips_finished_ones(self):
        done = self.ws / "seq-b" / "run-001"
        done.mkdir()
        (done / "run.log").write_text(jobs.sentinel_line(0, "success", 1) + "\n")
        plan = runs.plan_run(cases=["seq-a", "seq-b"], threads=2)
        self.assertEqual([c["done"] for c in plan["cases"]], [False, True])
        job = runs.start(plan, approved_by="test")
        self.assertEqual(job["kind"], "sequence")
        out = self.finished(job["job_id"])
        self.assertEqual(out["status"], "success", out)
        self.assertEqual([s["status"] for s in out["states"]], ["success", "skipped"])
        self.assertIsNotNone(out["states"][0]["keff"])
        with self.assertRaises(OpenMCError) as ctx:
            runs.start(runs.plan_run(cases=["seq-a", "seq-b"], threads=2), approved_by="test")
        self.assertIn("nothing to start", str(ctx.exception))

    # ---- the command line ------------------------------------------------------------------------------------------------
    def test_the_command_line_asks_and_refuses_without_a_terminal(self):
        env = dict(os.environ, AUTOMC_WORKSPACE=str(self.ws))
        p = subprocess.run([sys.executable, "-m", "automc", "run", "start", "cli", "--threads", "2"], cwd=SRC, env=env,
                           capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=120)
        self.assertEqual(p.returncode, 3, p.stderr)
        self.assertIn("histories", p.stdout)                                          # the plan is shown
        self.assertIn("--yes", p.stderr)
        self.assertFalse((self.ws / "cli" / "run-001").exists())
        p = subprocess.run([sys.executable, "-m", "automc", "run", "start", "cli", "--threads", "2", "--yes", "--json"], cwd=SRC, env=env,
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(p.returncode, 0, p.stderr)
        job = json.loads(p.stdout[p.stdout.index("{"):])
        self.assertEqual(job["approved_by"], "cli")
        out = self.finished(job["job_id"])
        self.assertEqual(out["status"], "success")
        p = subprocess.run([sys.executable, "-m", "automc", "run", "jobs"], cwd=SRC, env=env, capture_output=True, text=True, timeout=120)
        self.assertIn(job["job_id"], p.stdout)
