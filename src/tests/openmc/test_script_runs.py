"""
Script runs (decision A13, devops/AutoMC_Authoring_Plan.md): Python that runs transport itself (a criticality search, a depletion,
openmc.lib) plans like any run and starts only with the user's approval. The plan shows the whole script, the files copied and the
label; an approved script runs as run.py in a new run folder with a copy of the case's files, OMP_NUM_THREADS set and no keys; it ends
as success, failed (with the error) or stopped, at its time limit too; it cannot pause or continue from a statepoint; a cancel ends
what the script started as well. With OpenMC, a script that runs the case's model leaves its statepoint.
"""
import importlib.util
import json
import os
import shutil
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

import automc_paths
import automc_settings
from tools import conversation, registry
from tools.openmc import jobs, runs
from tools.openmc.errors import OpenMCError
from tests.openmc.test_runs import wait_for

POSIX = os.name == "posix"
HAVE_OPENMC = POSIX and importlib.util.find_spec("openmc") is not None and shutil.which("openmc") is not None

MODEL = "<model><settings><run_mode>fixed source</run_mode><particles>10</particles><batches>2</batches></settings></model>\n"


class Workspace(unittest.TestCase):
    def setUp(self):
        self.ws = Path(tempfile.mkdtemp(prefix="automc_script_")).resolve()
        self.patches = [mock.patch.object(automc_paths, "WORKSPACE", self.ws), mock.patch.dict(os.environ)]
        for p in self.patches:
            p.start()
        os.environ[automc_settings.USER_ENV] = str(self.ws / "no-user-settings.yaml")
        os.environ.setdefault("OPENMC_CROSS_SECTIONS", "/nonexistent/cross_sections.xml")    # fake scripts never read it
        automc_settings._CACHE.clear()
        case = self.ws / "study" / "case"
        case.mkdir(parents=True)
        (case / "model.xml").write_text(MODEL, encoding="utf-8")
        (case / "build.py").write_text("PITCH = 1.26\n", encoding="utf-8")
        (case / "result.json").write_text(json.dumps({"builder": "script", "facility_data": {"distribution": "public"}}), encoding="utf-8")

    def tearDown(self):
        for j in jobs.list_jobs(jobs.home(self.ws), active_only=True):            # never leave a worker behind
            try:
                runs.cancel(j["job_id"])
            except OpenMCError:
                pass
        for p in reversed(self.patches):
            p.stop()
        automc_settings._CACHE.clear()
        shutil.rmtree(self.ws, ignore_errors=True)

    def plan(self, script, label="public", **kw):
        with conversation.inside(None, label, "t1", None):
            return runs.plan_run(case="study/case", script=script, threads=kw.pop("threads", 2), **kw)

    def start(self, script, **kw):
        return runs.start(self.plan(script, **kw), approved_by="test")

    def finished(self, job_id, timeout=60.0):
        return wait_for(lambda: (lambda j: j if j["status"] not in jobs.ACTIVE else None)(runs.check(job_id=job_id)), timeout=timeout)


class Plans(Workspace):
    def test_the_plan_shows_the_script_and_starts_nothing(self):
        out = registry.call("OpenMCRunStartTool", {"case": "study/case", "script": "print('hello from the script')\n"})
        self.assertTrue(out["ok"], out)
        self.assertFalse(out["started"])
        plan = out["plan"]
        self.assertEqual(plan["kind"], "script")
        self.assertIn("print('hello from the script')", plan["text"])
        self.assertIn("run.py", plan["text"])
        self.assertEqual(plan["copies"], ["build.py", "model.xml", "result.json"])
        self.assertEqual(plan["cases"][0]["run_folder"], "study/case/run-001")
        self.assertEqual(plan["timeout_s"], runs.SCRIPT_TIMEOUT)
        self.assertEqual(jobs.list_jobs(jobs.home(self.ws)), [])
        self.assertFalse((self.ws / "study" / "case" / "run-001").exists())

    def test_bad_requests_are_refused(self):
        for kw, field in [({"case": "study/case", "cases": ["a"], "script": "x=1"}, "script"),
                          ({"case": "study/case", "script": 42}, "script"),
                          ({"case": "study/case", "script": "x=1", "timeout_s": 0}, "timeout_s"),
                          ({"case": "nowhere", "script": "x=1"}, "case")]:
            with self.subTest(kw=kw):
                with self.assertRaises(OpenMCError) as e:
                    runs.plan_run(**kw)
                self.assertEqual(e.exception.field, field)

    def test_every_argument_filled_with_empty_values_is_an_ordinary_run(self):
        """OpenAI's strict tool calls fill every argument: script "", sweep "", cases [] and a timeout_s mean an ordinary run (the
        2026-11-02 cloud evaluation could not plan one before)."""
        out = registry.call("OpenMCRunStartTool", {"case": "study/case", "cases": [], "sweep": "", "threads": 2, "concurrent": False,
                                                   "script": "", "timeout_s": 3600})
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["plan"]["kind"], "run")
        self.assertEqual(runs.plan_run(case="study/case", script="   ")["kind"], "run")
        # its first call also repeats the case in cases; that is still the one case, for a run and a script run alike
        self.assertEqual(runs.plan_run(case="study/case", cases=["study/case"])["kind"], "run")
        self.assertEqual(runs.plan_run(case="study/case", cases=["study/case"], script="x = 1\n")["kind"], "script")
        with self.assertRaises(OpenMCError):
            runs.plan_run(case="study/case", cases=["study/other"])

    def test_a_run_folder_gets_the_multigroup_library_the_model_names(self):
        case = self.ws / "study" / "case"
        (case / "model.xml").write_text("<model><materials><cross_sections>mgxs.h5</cross_sections></materials>" + MODEL[7:],
                                        encoding="utf-8")
        (case / "mgxs.h5").write_bytes(b"multigroup data")
        rf = runs.prepare_run_folder(case, "run-001")
        self.assertEqual((rf / "mgxs.h5").read_bytes(), b"multigroup data")
        (case / "model.xml").write_text("<model><materials><cross_sections>/elsewhere/x.h5</cross_sections></materials>" + MODEL[7:],
                                        encoding="utf-8")
        self.assertFalse(any(p.suffix == ".h5" for p in runs.prepare_run_folder(case, "run-002").iterdir()))

    def test_a_run_folder_gets_the_geometry_and_mesh_files_the_model_names(self):
        """A CAD geometry and an unstructured mesh named by relative paths reach the run folder (linked, or copied); a path out of
        the case or an absolute one is not carried."""
        case = self.ws / "study" / "case"
        (case / "mesh").mkdir()
        (case / "geom.h5m").write_bytes(b"cad")
        (case / "mesh" / "pin.h5m").write_bytes(b"tets")
        (case / "model.xml").write_text(
            '<model><geometry><dagmc_universe id="1" filename="geom.h5m"/></geometry>' + MODEL[7:-9] +
            '<tallies><mesh id="1" type="unstructured" library="moab"><filename>mesh/pin.h5m</filename></mesh></tallies></model>\n',
            encoding="utf-8")
        found = {f["kind"]: (f["text"], f["inside"]) for f in runs.named_files(case)}
        self.assertEqual(found, {"DAGMC geometry": ("geom.h5m", True), "unstructured mesh": ("mesh/pin.h5m", True)})
        rf = runs.prepare_run_folder(case, "run-001")
        self.assertEqual(((rf / "geom.h5m").read_bytes(), (rf / "mesh" / "pin.h5m").read_bytes()), (b"cad", b"tets"))
        (case / "model.xml").write_text('<model><geometry><dagmc_universe id="1" filename="../geom.h5m"/></geometry>' + MODEL[7:],
                                        encoding="utf-8")
        self.assertEqual([f["inside"] for f in runs.named_files(case)], [False])
        self.assertEqual(sorted(p.name for p in runs.prepare_run_folder(case, "run-002").iterdir()), ["control.json", "model.xml"])

    def test_the_label_follows_the_conversation_and_the_case(self):
        self.assertEqual(self.plan("x = 1\n")["facility_data"], "public")
        self.assertEqual(self.plan("x = 1\n", label="restricted")["facility_data"], "restricted")
        (self.ws / "study" / "case" / "result.json").unlink()            # a case AutoMC did not build
        self.assertEqual(self.plan("x = 1\n")["facility_data"], "unknown")

    @unittest.skipUnless(POSIX, "runs need Linux or WSL")
    def test_a_changed_script_is_refused_at_start(self):
        plan = self.plan("x = 1\n")
        plan["script"] = "x = 2\n"
        with self.assertRaises(OpenMCError) as e:
            runs.start(plan, approved_by="test")
        self.assertEqual(e.exception.kind, "NotAllowed")


@unittest.skipUnless(POSIX, "runs need Linux or WSL")
class Running(Workspace):
    def test_a_script_runs_in_its_own_folder_without_keys(self):
        os.environ["AUTOMC_TEST_API_KEY"] = "do-not-pass"
        script = textwrap.dedent('''
            import json, os
            assert os.path.isfile("model.xml") and os.path.isfile("build.py"), "the case's files are copied"
            out = {"threads": os.environ.get("OMP_NUM_THREADS"), "key_seen": "AUTOMC_TEST_API_KEY" in os.environ}
            json.dump(out, open("out.json", "w"))
            print("script says hello")
        ''')
        job = self.start(script, threads=3)
        self.assertEqual(job["kind"], "script")
        done = self.finished(job["job_id"])
        self.assertEqual(done["status"], "success", done)
        rf = self.ws / done["run_folder"]
        out = json.loads((rf / "out.json").read_text())
        self.assertEqual(out, {"threads": "3", "key_seen": False})
        self.assertIn("out.json", done["files"])
        self.assertIn("script says hello", (rf / "run.log").read_text())
        self.assertEqual((rf / "run.py").read_text(), script)
        self.assertIn("post.py", done["next"])

    def test_a_failing_script_reports_its_error(self):
        done = self.finished(self.start("raise ValueError('the search bracket holds no root')\n")["job_id"])
        self.assertEqual(done["status"], "failed")
        self.assertIn("ValueError: the search bracket holds no root", done["error"])

    def test_stop_pause_restart_and_the_time_limit(self):
        job = self.start("import time\ntime.sleep(60)\n")
        wait_for(lambda: runs.check(job_id=job["job_id"]).get("phase") == "running", timeout=20)
        for command in ("pause", "checkpoint", "resume"):
            with self.assertRaises(OpenMCError) as e:
                runs.control(job["job_id"], command)
            self.assertEqual(e.exception.kind, "NotAllowed")
        runs.control(job["job_id"], "stop")
        done = self.finished(job["job_id"], timeout=30)
        self.assertEqual(done["status"], "stopped")
        with self.assertRaises(OpenMCError):
            runs.plan_restart(job["job_id"])
        late = self.finished(self.start("import time\ntime.sleep(60)\n", timeout_s=2)["job_id"], timeout=40)
        self.assertEqual(late["status"], "failed")
        self.assertIn("time limit", late["error"])

    def test_cancel_ends_what_the_script_started(self):
        script = textwrap.dedent('''
            import subprocess, time
            p = subprocess.Popen(["sleep", "120"])
            open("child.pid", "w").write(str(p.pid))
            time.sleep(120)
        ''')
        job = self.start(script)
        rf = self.ws / job["run_folder"]
        wait_for(lambda: (rf / "child.pid").is_file(), timeout=20)
        grandchild = int((rf / "child.pid").read_text())
        out = runs.cancel(job["job_id"])
        self.assertEqual(out["status"], "cancelled")
        wait_for(lambda: not jobs.process_alive(grandchild), timeout=15)


@unittest.skipUnless(HAVE_OPENMC, "needs OpenMC (WSL, the automc environment)")
class WithOpenMC(Workspace):
    def setUp(self):
        super().setUp()
        if os.environ["OPENMC_CROSS_SECTIONS"].startswith("/nonexistent"):
            from tests.reactors.triga.test_build_config0 import _XS_FALLBACK
            os.environ["OPENMC_CROSS_SECTIONS"] = str(_XS_FALLBACK)
        from tests.openmc.test_authoring import SPHERE
        from tools.openmc import authoring
        with conversation.inside(None, "public", "t1", None):
            built = authoring.start_build(authoring.plan_build("sphere", SPHERE), approved_by="test")
        self.assertTrue(built["built"], built)

    def test_a_script_runs_the_model(self):
        script = "import openmc\nopenmc.run(threads=2, output=False)\nprint('ran')\n"
        with conversation.inside(None, "public", "t1", None):
            plan = runs.plan_run(case="sphere", script=script, threads=2)
        done = self.finished(runs.start(plan, approved_by="test")["job_id"], timeout=120)
        self.assertEqual(done["status"], "success", done)
        self.assertIn("statepoint.5.h5", done["files"])

    def test_an_ordinary_fixed_source_run_reports_no_keff(self):
        """The same fixed-source case run by the worker: openmc.lib answers 1.0 +/- 0.0 for k-eff there, which is no result."""
        with conversation.inside(None, "public", "t1", None):
            plan = runs.plan_run(case="sphere", threads=2)
        done = self.finished(runs.start(plan, approved_by="test")["job_id"], timeout=120)
        self.assertEqual(done["status"], "success", done)
        self.assertEqual([(e["keff"], e["sigma"]) for e in done["recent_events"] if e["event"] in ("batch", "finished")],
                         [(None, None)] * len([e for e in done["recent_events"] if e["event"] in ("batch", "finished")]))
        self.assertIsNone(done["keff"]["value"])


if __name__ == "__main__":
    unittest.main()
