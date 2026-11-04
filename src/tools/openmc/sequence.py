"""
The sequence supervisor: runs several cases in turn as one job (for example the states of a sweep), started by the job service in the
job's folder <workspace>/.automc/jobs/<job_id>/. Each case runs in its own worker process (worker.py) in a run folder of that case, as a
child of this process, so cancelling the job kills both. A case that already finished is skipped, as run_sweep.sh does.

    python -m tools.openmc.sequence --job-folder DIR --segment K

Files in the job folder:
  sequence.json  the cases, the threads and the workspace (written by the job service)
  control.json   pause, resume, checkpoint, stop: passed on to the case that is running; a pause between cases holds the next one
  status.json    state, segment, the case running now, and every case's status, run folder and last k-eff
  sequence.log   the supervisor's own lines, ending with AUTOMC_JOB_DONE rc=<n> status=<success|stopped|failed> segment=<k>

On a later segment (continuing a stopped or interrupted sequence), a case whose last run has a statepoint but did not finish continues
from that statepoint in the same run folder; a case with no usable run gets a new run folder.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

import automc_paths

from . import jobs, runs

POLL_S = 0.5


class Supervisor:
    def __init__(self, folder, segment):
        self.folder, self.segment = Path(folder), segment
        self.seq_file = jobs.read_json(self.folder / "sequence.json", {})
        automc_paths.WORKSPACE = Path(self.seq_file["workspace"])      # the job service's workspace, whatever this process was given
        self.cases, self.threads = self.seq_file["cases"], int(self.seq_file["threads"])
        self.seq = int((jobs.read_json(self.folder / "control.json", {}) or {}).get("seq", 0))
        old = {s["case"]: s for s in (jobs.read_json(self.folder / "status.json", {}) or {}).get("states", [])}
        self.status = {"state": "running", "segment": segment, "current": None, "pid": os.getpid(), "updated_at": jobs.now(),
                       "states": [old.get(c, {"case": c, "status": "queued", "run_folder": None, "keff": None, "sigma": None})
                                  for c in self.cases]}
        self.stop = False
        self.write()

    def write(self, **fields):
        self.status.update(fields, updated_at=jobs.now())
        jobs.atomic_write_json(self.folder / "status.json", self.status)

    def log(self, text):
        print(f"[{jobs.now()}] {text}", flush=True)

    def command(self):
        c = jobs.read_json(self.folder / "control.json", {}) or {}
        seq = int(c.get("seq", 0))
        if seq <= self.seq:
            return None
        self.seq = seq
        return c.get("command")

    def relay(self, run_folder, command):
        c = jobs.read_json(run_folder / "control.json", {}) or {}
        jobs.atomic_write_json(run_folder / "control.json", {"seq": int(c.get("seq", 0)) + 1, "command": command, "sent_at": jobs.now()})

    def hold_between_cases(self):
        """A pause that arrives between cases holds the next one until resume or stop."""
        cmd = self.command()
        while cmd == "pause" or (cmd is None and self.status["state"] == "paused"):
            if cmd == "pause":
                self.write(state="paused")
                self.log("paused between cases")
            time.sleep(POLL_S)
            cmd = self.command()
            if cmd == "resume":
                self.write(state="running")
                self.log("resumed")
                return
        if cmd == "stop":
            self.stop = True

    def where_to_run(self, folder, batches):
        """(run folder, worker segment, restart statepoint) for a case: continue an unfinished run on later segments, else a new one."""
        if self.segment > 1:
            existing = jobs.run_folders(folder)
            if existing:
                last = existing[-1]
                sent = jobs.read_sentinel(last / "run.log")
                sp = runs._latest_statepoint(last)
                if (sent is None or sent[1] != "success") and sp and sp[0] < batches:
                    seg = int((jobs.read_json(last / "status.json", {}) or {}).get("segment", 1)) + 1
                    st = jobs.read_json(last / "status.json", {}) or {}
                    jobs.atomic_write_json(last / "status.json", {**st, "state": "starting", "segment": seg})
                    self.relay(last, "run")
                    return last, seg, sp[1]
        return runs.prepare_run_folder(folder, jobs.next_run_name(folder)), 1, None

    def run_case(self, i):
        entry = self.status["states"][i]
        case = entry["case"]
        folder = runs.case_folder(case)
        batches = runs.model_settings(folder / "model.xml")["batches"]
        if runs.case_done(folder, batches):
            entry.update(status="skipped")
            self.write()
            self.log(f"{case}: already finished, skipped")
            return
        rf, seg, restart = self.where_to_run(folder, batches)
        entry.update(status="running", run_folder=str(rf.relative_to(automc_paths.WORKSPACE).as_posix()))
        self.write(current=i)
        self.log(f"{case}: starting in {entry['run_folder']}" + (f" from {restart}" if restart else ""))
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join([str(automc_paths.SRC)] + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))
        with open(rf / "run.log", "ab") as log:                    # same process group as this supervisor: a cancel kills both
            child = subprocess.Popen([os.environ.get("AUTOMC_PYTHON") or sys.executable, "-m",
                                      *runs.worker_args(self.threads, batches, seg, restart)],
                                     cwd=str(rf), env=env, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
        while child.poll() is None:
            cmd = self.command()
            if cmd in ("pause", "resume", "checkpoint", "stop"):
                self.relay(rf, cmd)
                self.log(f"{case}: {cmd} passed on")
                if cmd == "stop":
                    self.stop = True
            st = jobs.read_json(rf / "status.json", {}) or {}
            if st.get("segment") == seg:
                state = "paused" if st.get("state") == "paused" else "running"
                entry.update(status=state, keff=st.get("keff"), sigma=st.get("sigma"), batch=st.get("batch"))
                self.write(state=state)
            time.sleep(POLL_S)
        sent = jobs.read_sentinel(rf / "run.log")
        st = jobs.read_json(rf / "status.json", {}) or {}
        final = sent[1] if sent and sent[2] == seg else "failed"
        entry.update(status=final, keff=st.get("keff"), sigma=st.get("sigma"), batch=st.get("batch"))
        self.write(state="running")
        self.log(f"{case}: {final}" + (f", k-eff {st.get('keff')} +/- {st.get('sigma')}" if st.get("keff") is not None else ""))

    def run(self):
        for i in range(len(self.cases)):
            if self.status["states"][i]["status"] in ("success", "skipped"):
                continue
            self.hold_between_cases()
            if self.stop:
                break
            self.run_case(i)
            if self.stop:
                break
        statuses = [s["status"] for s in self.status["states"]]
        if self.stop:
            return "stopped"
        return "failed" if any(s in ("failed", "interrupted") for s in statuses) else "success"


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m tools.openmc.sequence")
    ap.add_argument("--job-folder", required=True)
    ap.add_argument("--segment", type=int, default=1)
    a = ap.parse_args(argv)
    sup = Supervisor(a.job_folder, a.segment)
    try:
        outcome = sup.run()
        rc = 0 if outcome != "failed" else 1
    except Exception as e:
        sup.log(f"ERROR {type(e).__name__}: {e}")
        outcome, rc = "failed", 1
    sup.write(state="finished", current=None, outcome=outcome)
    print(jobs.sentinel_line(rc, outcome, a.segment), flush=True)
    return rc


if __name__ == "__main__":
    sys.exit(main())
