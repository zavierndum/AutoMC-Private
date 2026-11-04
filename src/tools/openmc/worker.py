"""
The run worker: one process per run, started by the job service (runs.py) in the run folder, which holds the frozen model.xml. It drives
OpenMC through its C API (openmc.lib) one batch at a time, so a run can be paused, resumed, checkpointed and stopped between batches
(DESIGN.md section 5). The batches and their k-eff are the same as the `openmc` executable's for the same seed (checked 2026-10-25).

    python -m tools.openmc.worker --threads N --batches B --segment K [--restart statepoint.B.h5]

Files in the run folder:
  run.log        OpenMC's own output (the job service points stdout here); the worker ends it with
                 AUTOMC_JOB_DONE rc=<n> status=<success|stopped|failed> segment=<k>
  events.jsonl   one JSON object per line: started, batch {batch, batches, keff, sigma, wall_s}, paused, resumed, checkpoint, stopping,
                 finished, error. sigma is null until there are two active batches.
  status.json    the worker's present state, rewritten after every change: state (starting, running, paused, finished), batch,
                 batches, keff, sigma, last_statepoint, control_seq, segment, pid
  control.json   written by the job service: {"seq": n, "command": "pause" | "resume" | "checkpoint" | "stop"}; each new seq is acted on
                 once, between batches
  statepoint.B.h5  written at pause, checkpoint and stop, and by OpenMC at its own statepoint batches (the last batch by default)

Each launch is a segment (1 for the first; a restart from a statepoint is the next). The segment number in the sentinel keeps an older
segment's ending from being read as the end of the present one.

Test seam: AUTOMC_WORKER_TEST_DELAY_S sleeps that long after each batch (so tests can pause a tiny run); AUTOMC_WORKER_TEST_FAIL=init
raises before OpenMC starts, =crash exits without a sentinel after the first batch.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
import traceback
from pathlib import Path

from .jobs import atomic_write_json, now, read_json, sentinel_line

POLL_S = 0.5


class Worker:
    def __init__(self, folder, threads, batches, segment, restart=None):
        self.folder = Path(folder)
        self.threads, self.batches, self.segment, self.restart = threads, batches, segment, restart
        self.eigenvalue = _run_mode(self.folder) == "eigenvalue"
        self.t0 = time.time()
        control = read_json(self.folder / "control.json", {}) or {}
        self.seq = int(control.get("seq", 0))                 # commands up to this one were for an earlier segment
        self.state = {"state": "starting", "batch": None, "batches": None, "keff": None, "sigma": None, "last_statepoint": None,
                      "control_seq": self.seq, "segment": segment, "pid": os.getpid(), "updated_at": now()}
        self.write_status()

    # ---- files -----------------------------------------------------------------------------------------------------------
    def event(self, name, **fields):
        with open(self.folder / "events.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"t": now(), "event": name, "segment": self.segment, **fields}) + "\n")

    def write_status(self, **fields):
        self.state.update(fields, updated_at=now())
        atomic_write_json(self.folder / "status.json", self.state)

    def next_command(self):
        """The command of a control.json seq not yet acted on, or None."""
        c = read_json(self.folder / "control.json", {}) or {}
        seq = int(c.get("seq", 0))
        if seq <= self.seq:
            return None
        self.seq = seq
        self.write_status(control_seq=seq)
        return c.get("command")

    # ---- run ------------------------------------------------------------------------------------------------------------------
    def statepoint(self, lib, reason):
        if lib.current_batch() == 0:                         # nothing simulated yet: no statepoint to write or to restart from
            self.event(reason, batch=0, statepoint=None)
            return None
        name = f"statepoint.{lib.current_batch()}.h5"
        if not (self.folder / name).exists():
            lib.statepoint_write(name)
        self.write_status(last_statepoint=name)
        self.event(reason, batch=lib.current_batch(), statepoint=name)
        return name

    def keff(self, lib):
        if not self.eigenvalue:                              # a fixed-source run has no k-eff; openmc.lib reports 1.0 +/- 0.0
            return None, None
        k, s = lib.keff()
        return (k if math.isfinite(k) else None), (s if math.isfinite(s) else None)

    def run(self):
        if os.environ.get("AUTOMC_WORKER_TEST_FAIL") == "init":
            raise RuntimeError("forced failure for the tests")
        import openmc.lib as lib
        self.event("started", pid=os.getpid(), threads=self.threads, restart=self.restart)
        sys.stdout.flush()
        lib.init(args=["-s", str(self.threads)] + (["-r", self.restart] if self.restart else []))
        lib.simulation_init()
        batches = self.batches
        self.write_status(state="running", batch=lib.current_batch(), batches=batches)
        delay = float(os.environ.get("AUTOMC_WORKER_TEST_DELAY_S", "0") or 0)
        outcome = "success"
        while True:
            cmd = self.next_command()
            if cmd == "checkpoint":
                self.statepoint(lib, "checkpoint")
            elif cmd == "stop":
                outcome = "stopped"
                break
            elif cmd == "pause":
                self.statepoint(lib, "paused")
                self.write_status(state="paused")
                while True:                                   # resident and idle until resume or stop
                    time.sleep(POLL_S)
                    cmd = self.next_command()
                    if cmd == "resume":
                        self.write_status(state="running")
                        self.event("resumed", batch=lib.current_batch())
                        break
                    if cmd == "stop":
                        outcome = "stopped"
                        break
                    if cmd == "checkpoint":
                        self.event("checkpoint", batch=lib.current_batch(), statepoint=self.state["last_statepoint"])
                if outcome == "stopped":
                    break
            t = time.time()
            status = lib.next_batch()
            b = lib.current_batch()
            k, s = self.keff(lib)
            self.write_status(batch=b, keff=k, sigma=s)
            self.event("batch", batch=b, batches=batches, keff=k, sigma=s, wall_s=round(time.time() - t, 3))
            if os.environ.get("AUTOMC_WORKER_TEST_FAIL") == "crash":
                os._exit(9)
            if delay:
                time.sleep(delay)
            if status != 0:                                   # 1: the last batch; 2: tally triggers met
                break
        if outcome == "stopped":
            self.event("stopping", batch=lib.current_batch())
            self.statepoint(lib, "checkpoint")
        sys.stdout.flush()
        lib.simulation_finalize()
        lib.finalize()
        _flush_c_stdout()
        return outcome


def _run_mode(folder):
    """The run mode of the frozen model (model.xml, or settings.xml beside it); OpenMC's default, eigenvalue, when none is written.
    openmc.lib does not expose it in OpenMC 0.15 (settings.run_mode is an undefined symbol)."""
    import xml.etree.ElementTree as ET
    for name, path in (("model.xml", "settings/run_mode"), ("settings.xml", "run_mode")):
        try:
            mode = ET.parse(Path(folder) / name).getroot().findtext(path)
        except (OSError, ET.ParseError):
            continue
        return (mode or "eigenvalue").strip()
    return "eigenvalue"


def _flush_c_stdout():
    """OpenMC writes through C stdio; flush it so the sentinel that follows really is the last line of run.log."""
    try:
        import ctypes
        ctypes.CDLL(None).fflush(None)
    except (OSError, AttributeError):
        pass


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m tools.openmc.worker")
    ap.add_argument("--threads", type=int, required=True)
    ap.add_argument("--batches", type=int, required=True, help="batches in model.xml (for progress)")
    ap.add_argument("--segment", type=int, default=1)
    ap.add_argument("--restart", help="statepoint to continue from")
    a = ap.parse_args(argv)
    w = Worker(Path.cwd(), a.threads, a.batches, a.segment, a.restart)
    try:
        outcome = w.run()
        k, s = w.state["keff"], w.state["sigma"]
        w.write_status(state="finished", outcome=outcome)
        w.event("finished", outcome=outcome, batch=w.state["batch"], keff=k, sigma=s, seconds=round(time.time() - w.t0, 1))
        rc = 0
    except Exception as e:                                    # OpenMC errors arrive here as exceptions from openmc.lib
        traceback.print_exc()
        w.write_status(state="finished", outcome="failed", error=f"{type(e).__name__}: {e}")
        w.event("error", message=f"{type(e).__name__}: {e}")
        outcome, rc = "failed", 1
    _flush_c_stdout()
    sys.stdout.flush()
    sys.stderr.flush()
    print(sentinel_line(rc, outcome, a.segment), flush=True)
    return rc


if __name__ == "__main__":
    sys.exit(main())
