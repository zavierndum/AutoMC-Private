"""
The script-run worker (decision A13 of devops/AutoMC_Authoring_Plan.md): one process per approved run script, started by the job service
(runs.py) in a new run folder that holds the script (run.py) and a frozen copy of the case's files. Some work runs transport from inside
Python: a criticality search (`openmc.search_for_keff`), a depletion (`Integrator.integrate`), a loop over `openmc.lib`. A build script
may not do that; a run script may, because the user approved it as a run.

    python -m tools.openmc.script_worker --threads N --timeout S --segment K

The worker runs `python -I run.py` in the run folder as its own process group, with OMP_NUM_THREADS=N and without keys (the Python
tool's environment), and watches it: a `stop` in control.json ends it (SIGTERM, then SIGKILL), and so does the time limit. A script run
cannot pause or continue from a statepoint: it may run many simulations, and only the script knows how they follow one another.

Files in the run folder, as for a run (worker.py): run.log (the script's output, ended by the AUTOMC_JOB_DONE line), events.jsonl
(started, stopping, finished, error), status.json (state, child_pid, files written, seconds), control.json.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from .jobs import atomic_write_json, now, process_started_at, read_json, sentinel_line

POLL_S = 0.5
SCRIPT = "run.py"
KILL_WAIT_S = 10.0


def _env(threads):
    from tools.python import run as python_run
    env = python_run._clean_env()                   # no keys; OPENMC_CROSS_SECTIONS and the other OPENMC_ settings stay
    env.update(OMP_NUM_THREADS=str(threads), PYTHONDONTWRITEBYTECODE="1")
    env.pop("PYTHONPATH", None)                     # the script sees OpenMC, not AutoMC's modules
    return env


def _last_error(log):
    try:
        lines = Path(log).read_text(encoding="utf-8", errors="replace").splitlines()[-200:]
    except OSError:
        return None
    for line in reversed(lines):
        s = line.strip()
        if s and not s.startswith(("File ", "^", "~")) and ("Error" in s or "Exception" in s or s.startswith("ERROR")):
            return s[:300]
    return None


class ScriptWorker:
    def __init__(self, folder, threads, timeout, segment):
        self.folder = Path(folder)
        self.threads, self.timeout, self.segment = threads, timeout, segment
        self.t0 = time.time()
        control = read_json(self.folder / "control.json", {}) or {}
        self.seq = int(control.get("seq", 0))
        self.child = None
        self.before = {p.name for p in self.folder.iterdir()}
        self.state = {"state": "starting", "control_seq": self.seq, "segment": segment, "pid": os.getpid(), "child_pid": None,
                      "child_started_at": None, "updated_at": now()}
        self.write_status()

    def event(self, name, **fields):
        with open(self.folder / "events.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"t": now(), "event": name, "segment": self.segment, **fields}) + "\n")

    def write_status(self, **fields):
        self.state.update(fields, updated_at=now())
        atomic_write_json(self.folder / "status.json", self.state)

    def next_command(self):
        c = read_json(self.folder / "control.json", {}) or {}
        seq = int(c.get("seq", 0))
        if seq <= self.seq:
            return None
        self.seq = seq
        self.write_status(control_seq=seq)
        return c.get("command")

    def kill_child(self):
        """End the script and everything it started (its own process group): SIGTERM, then SIGKILL."""
        if self.child is None or self.child.poll() is not None:
            return
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(self.child.pid, sig)
            except (ProcessLookupError, PermissionError):
                return
            t = time.time()
            while time.time() - t < KILL_WAIT_S and self.child.poll() is None:
                time.sleep(0.1)
            if self.child.poll() is not None:
                return

    def files_written(self):
        return sorted(p.relative_to(self.folder).as_posix() for p in self.folder.rglob("*")
                      if p.is_file() and p.relative_to(self.folder).parts[0] not in self.before)

    def run(self):
        if not (self.folder / SCRIPT).is_file():
            raise RuntimeError(f"{SCRIPT} is missing from the run folder")
        sys.stdout.flush()
        self.child = subprocess.Popen([sys.executable, "-I", SCRIPT], cwd=str(self.folder), env=_env(self.threads),
                                      stdin=subprocess.DEVNULL, stdout=sys.stdout, stderr=subprocess.STDOUT, start_new_session=True)
        self.write_status(state="running", child_pid=self.child.pid, child_started_at=process_started_at(self.child.pid))
        self.event("started", pid=os.getpid(), child_pid=self.child.pid, threads=self.threads, timeout_s=self.timeout)
        while self.child.poll() is None:
            time.sleep(POLL_S)
            if self.next_command() == "stop":
                self.event("stopping", reason="stop")
                self.kill_child()
                return "stopped", None
            if time.time() - self.t0 > self.timeout:
                self.event("stopping", reason="time limit")
                self.kill_child()
                return "failed", f"stopped at the time limit of {self.timeout} s"
        if self.child.returncode == 0:
            return "success", None
        return "failed", _last_error(self.folder / "run.log") or f"the script ended with code {self.child.returncode}"


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m tools.openmc.script_worker")
    ap.add_argument("--threads", type=int, required=True)
    ap.add_argument("--timeout", type=int, required=True)
    ap.add_argument("--segment", type=int, default=1)
    a = ap.parse_args(argv)
    w = ScriptWorker(Path.cwd(), a.threads, a.timeout, a.segment)

    def on_term(signum, frame):                              # a cancel: take the script's process group down too
        w.kill_child()
        os._exit(143)
    signal.signal(signal.SIGTERM, on_term)
    try:
        outcome, error = w.run()
    except Exception as e:
        outcome, error = "failed", f"{type(e).__name__}: {e}"
    files = w.files_written()
    seconds = round(time.time() - w.t0, 1)
    w.write_status(state="finished", outcome=outcome, error=error, files=files, seconds=seconds)
    w.event("finished" if outcome != "failed" else "error", outcome=outcome, message=error, seconds=seconds, files=len(files))
    sys.stdout.flush()
    rc = 0 if outcome == "success" else 1
    print(sentinel_line(rc, outcome, a.segment), flush=True)
    return rc


if __name__ == "__main__":
    sys.exit(main())
