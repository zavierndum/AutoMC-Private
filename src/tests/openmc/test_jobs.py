"""
The job registry, the done sentinel, plans and how a job's status is settled from its files and its process. No transport and no OpenMC:
these run anywhere, with made-up run folders.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import automc_paths
from tools.openmc import jobs, runs, tools as openmc_tools
from tools.openmc.errors import OpenMCError

SETTINGS = """<?xml version='1.0' encoding='utf-8'?>
<model><materials/><geometry/><settings><run_mode>eigenvalue</run_mode><particles>{p}</particles><batches>{b}</batches>
<inactive>2</inactive><seed>4</seed></settings></model>
"""


def make_case(ws, name, particles=300, batches=8):
    folder = Path(ws) / name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "model.xml").write_text(SETTINGS.format(p=particles, b=batches), encoding="utf-8")
    return folder


def dead_pid():
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    return p.pid


class Workspace(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self.tmp.name)
        self.patch = mock.patch.object(automc_paths, "WORKSPACE", self.ws)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()


class Registry(Workspace):
    def test_create_update_find_list(self):
        a = jobs.create_job(self.ws, kind="run", case="a", status="running", pid=None)
        b = jobs.create_job(self.ws, kind="run", case="b", status="paused", pid=None)
        self.assertEqual(len(a["job_id"]), 12)
        done = jobs.update_job(self.ws, a["job_id"], status="success")
        self.assertIsNotNone(done["completed_at"])
        self.assertEqual(jobs.find_job(self.ws, case="a")["job_id"], a["job_id"])
        self.assertEqual(jobs.find_job(self.ws)["job_id"], b["job_id"])                  # an active job comes first
        self.assertEqual([j["case"] for j in jobs.list_jobs(self.ws, active_only=True)], ["b"])
        self.assertIsNone(jobs.update_job(self.ws, "nope", status="failed"))
        json.loads((self.ws / jobs.REGISTRY_NAME).read_text())                              # always valid JSON on disk

    def test_unknown_statuses_are_refused(self):
        with self.assertRaises(ValueError):
            jobs.create_job(self.ws, kind="run", case="a", status="finished")
        j = jobs.create_job(self.ws, kind="run", case="a", status="running")
        with self.assertRaises(ValueError):
            jobs.update_job(self.ws, j["job_id"], status="done")

    def test_run_folder_names(self):
        folder = self.ws / "c"
        folder.mkdir()
        self.assertEqual(jobs.next_run_name(folder), "run-001")
        for n in ("run-001", "run-002", "run-010", "notes"):
            (folder / n).mkdir()
        self.assertEqual(jobs.next_run_name(folder), "run-011")                             # sorted by number, not by text

    def test_the_sentinel_is_a_whole_line_and_names_its_segment(self):
        log = self.ws / "run.log"
        log.write_text("text AUTOMC_JOB_DONE rc=0 status=success segment=1 inside a line\n", encoding="utf-8")
        self.assertIsNone(jobs.read_sentinel(log))
        log.write_text("out\n" + jobs.sentinel_line(0, "stopped", 1) + "\nmore\n" + jobs.sentinel_line(0, "success", 2) + "\n")
        self.assertEqual(jobs.read_sentinel(log), (0, "success", 2))
        self.assertIsNone(jobs.read_sentinel(self.ws / "missing.log"))

    def test_events_skip_a_partly_written_line(self):
        p = self.ws / "events.jsonl"
        p.write_text('{"event": "a"}\n{"event": "b"}\n{"event": "c', encoding="utf-8")
        self.assertEqual([e["event"] for e in jobs.read_events(p)], ["a", "b"])
        self.assertEqual([e["event"] for e in jobs.read_events(p, last=1)], ["b"])

    def test_process_check(self):
        self.assertTrue(jobs.process_alive(os.getpid(), jobs.process_started_at(os.getpid())))
        self.assertFalse(jobs.process_alive(dead_pid()))
        self.assertFalse(jobs.process_alive(None))


class Settle(Workspace):
    def job_with_files(self, sentinel=None, state="running", pid=None, log_extra="", **job_fields):
        rf = make_case(self.ws, "c") / "run-001"
        rf.mkdir()
        jobs.atomic_write_json(rf / "status.json", {"state": state, "batch": 3, "keff": 1.01, "sigma": 0.002, "segment": 1,
                                                    "control_seq": 0, "last_statepoint": None})
        (rf / "run.log").write_text("OpenMC output\n" + log_extra + (sentinel + "\n" if sentinel else ""), encoding="utf-8")
        return jobs.create_job(jobs.home(self.ws), kind="run", case="c", run_folder="c/run-001", status="running", segment=1,
                               pid=pid if pid is not None else dead_pid(), pid_started_at=None, batches_total=8, particles=300, **job_fields)

    def test_the_sentinel_of_this_segment_decides(self):
        j = runs.settle(self.job_with_files(jobs.sentinel_line(0, "success", 1)))
        self.assertEqual((j["status"], j["returncode"], j["batches_done"]), ("success", 0, 3))
        self.assertEqual(j["keff_latest"], {"value": 1.01, "sigma": 0.002, "batch": 3})

    def test_an_older_segment_s_sentinel_does_not_count(self):
        j = runs.settle(self.job_with_files(jobs.sentinel_line(0, "stopped", 0)))
        self.assertEqual(j["status"], "interrupted")

    def test_a_vanished_process_is_interrupted_or_failed_or_cancelled(self):
        self.assertEqual(runs.settle(self.job_with_files())["status"], "interrupted")
        self.tearDown(), self.setUp()
        j = runs.settle(self.job_with_files(log_extra=" ERROR: No cross_sections.xml file was specified\n"))
        self.assertEqual(j["status"], "failed")
        self.assertIn("cross_sections", j["error"])
        self.tearDown(), self.setUp()
        self.assertEqual(runs.settle(self.job_with_files(cancel_requested=True))["status"], "cancelled")

    def test_a_live_process_is_running_or_paused(self):
        live = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        try:
            j = runs.settle(self.job_with_files(state="paused", pid=live.pid))
            self.assertEqual(j["status"], "paused")
        finally:
            live.kill()
            live.wait()


class Plans(Workspace):
    def test_a_plan_says_what_would_run_and_starts_nothing(self):
        make_case(self.ws, "a")
        plan = runs.plan_run(case="a", threads=2)
        self.assertEqual((plan["kind"], plan["histories"], plan["threads"]), ("run", 300 * 8, 2))
        self.assertEqual(plan["cases"][0]["run_folder"], "a/run-001")
        self.assertEqual(plan["facility_data"], "unknown")                                  # no result.json: not built by AutoMC
        self.assertIn("2,400 histories", plan["text"])
        self.assertEqual(jobs.list_jobs(jobs.home(self.ws)), [])
        self.assertFalse((self.ws / "a" / "run-001").exists())

    def test_the_plan_id_follows_the_model_and_the_threads(self):
        folder = make_case(self.ws, "a")
        p1 = runs.plan_run(case="a", threads=2)
        self.assertEqual(p1["plan_id"], runs.plan_run(case="a", threads=2)["plan_id"])
        self.assertNotEqual(p1["plan_id"], runs.plan_run(case="a", threads=4)["plan_id"])
        (folder / "model.xml").write_text(SETTINGS.format(p=500, b=8), encoding="utf-8")
        self.assertNotEqual(p1["plan_id"], runs.plan_run(case="a", threads=2)["plan_id"])

    def test_cases_are_names_inside_the_workspace(self):
        for bad in ("../x", "/etc", "a/b/c", "", "a/../b"):
            with self.subTest(case=bad):
                with self.assertRaises(OpenMCError):
                    runs.plan_run(case=bad)
        with self.assertRaises(OpenMCError) as ctx:
            runs.plan_run(case="missing")
        self.assertEqual(ctx.exception.kind, "NotFound")

    def test_a_sweep_plan_skips_finished_states(self):
        make_case(self.ws, "sw/state-001")
        done = make_case(self.ws, "sw/state-002") / "run-001"
        done.mkdir()
        (done / "run.log").write_text(jobs.sentinel_line(0, "success", 1) + "\n", encoding="utf-8")
        (self.ws / "sw" / "sweep.json").write_text(json.dumps({"states": [{"folder": "state-001"}, {"folder": "state-002"}]}))
        plan = runs.plan_run(sweep="sw", threads=1)
        self.assertEqual([c["done"] for c in plan["cases"]], [False, True])
        self.assertEqual(plan["histories"], 2400)
        self.assertIn("already finished, skipped", plan["text"])

    def test_start_needs_an_approver(self):
        make_case(self.ws, "a")
        plan = runs.plan_run(case="a", threads=1)
        with self.assertRaises(OpenMCError) as ctx:
            with mock.patch.dict(os.environ, {"OPENMC_CROSS_SECTIONS": "x"}), mock.patch.object(runs.os, "name", "posix"):
                runs.start(plan, approved_by="")
        self.assertEqual(ctx.exception.kind, "NeedsApproval")


class AgentTools(Workspace):
    def test_the_start_tool_never_starts_and_cannot_be_approved_by_the_agent(self):
        make_case(self.ws, "a")
        out = openmc_tools.run_start({"case": "a", "threads": 1})
        self.assertTrue(out["ok"])
        self.assertEqual((out["started"], out["needs_approval"]), (False, True))
        self.assertEqual(out["plan"]["kind"], "run")
        self.assertEqual(jobs.list_jobs(jobs.home(self.ws)), [])
        for sneaky in ("approved_by", "approved", "yes"):
            with self.subTest(argument=sneaky):
                out = openmc_tools.run_start({"case": "a", sneaky: "user"})
                self.assertEqual((out["ok"], out["error"]["field"]), (False, sneaky))
        self.assertEqual(jobs.list_jobs(jobs.home(self.ws)), [])
        json.dumps(out)

    def test_control_tools_name_unknown_jobs(self):
        for tool in (openmc_tools.run_check, openmc_tools.run_pause, openmc_tools.run_stop, openmc_tools.run_cancel,
                     openmc_tools.run_resume):
            with self.subTest(tool=tool.__name__):
                out = tool({"job_id": "nope"})
                self.assertEqual((out["ok"], out["error"]["type"]), (False, "NotFound"))
        self.assertEqual(openmc_tools.job_list({})["jobs"], [])
