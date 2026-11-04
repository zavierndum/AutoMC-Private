"""
Runs from the command line (WSL, automc environment, OPENMC_CROSS_SECTIONS set). From AutoMC/src:

    python -m automc run start CASE [--threads N] [--concurrent]     show the plan, ask y/n, start; the command returns at once
    python -m automc run start --sweep NAME | --cases A,B,...         several cases in turn as one job (finished ones are skipped)
    python -m automc run start CASE --script FILE [--timeout S]       a script run: FILE becomes run.py in a new run folder of CASE
    python -m automc run check [JOB_ID]                                status, progress, latest k-eff (default: the newest job)
    python -m automc run watch [JOB_ID]                                follow a job until it ends; Ctrl-C stops watching, not the run
    python -m automc run pause | resume | checkpoint | stop | cancel JOB_ID
    python -m automc run jobs [--active]                               the registry

A start, and a resume that continues a stopped or interrupted job from its statepoint, ask "Start it? [y/N]". Without a terminal to
ask on, they refuse unless --yes is given (then typing the command was the approval). Add --json for machine-readable output.
"""
from __future__ import annotations

import argparse
import json
import sys
import time

from . import runs
from .errors import OpenMCError


def _ask(plan, yes):
    print(plan["text"])
    if yes:
        return True
    if not sys.stdin.isatty():
        raise OpenMCError("approval", "there is no terminal to ask on; give --yes to approve this start", kind="NeedsApproval", exit_code=3)
    answer = input("Start it? [y/N] ").strip().lower()
    return answer in ("y", "yes")


def _keff(k):
    if not k or k.get("value") is None:
        return "no k-eff yet"
    s = f" +/- {k['sigma']:.5f}" if k.get("sigma") is not None else " (no error yet: fewer than two active batches)"
    return f"k-eff {k['value']:.5f}{s} at batch {k['batch']}"


def _print_job(j):
    if j["kind"] == "script":
        took = f"  {j['seconds']} s" if j.get("seconds") is not None else ""
        print(f"job {j['job_id']}  {j['status']:11s} {j['case']}  {j['run_folder']}  script run.py{took}")
        if j.get("files"):
            print(f"    wrote: {', '.join(j['files'][:10])}" + (" ..." if len(j["files"]) > 10 else ""))
    elif j["kind"] == "run":
        b = j["batches"]
        line = (f"job {j['job_id']}  {j['status']:11s} {j['case']}  {j['run_folder']}  batch {b['done'] or 0} of {b['total']}  "
                f"{_keff(j.get('keff'))}")
        if j.get("remaining"):
            line += f"  about {j['remaining']} left"
        elif j.get("remaining_once_resumed"):
            line += f"  about {j['remaining_once_resumed']} left once resumed"
        print(line)
    else:
        states = j.get("states") or []
        done = sum(1 for s in states if s.get("status") in ("success", "skipped"))
        print(f"job {j['job_id']}  {j['status']:11s} sequence of {len(j.get('cases') or [])} cases ({done} done)")
        for s in states:
            k = f"  k-eff {s['keff']:.5f}" + (f" +/- {s['sigma']:.5f}" if s.get("sigma") is not None else "") if s.get("keff") else ""
            print(f"    {s['case']:32s} {s['status']:11s} {s.get('run_folder') or ''}{k}")
    if j.get("error"):
        print(f"    error: {j['error']}")
    if j.get("next"):
        print(f"    next: {j['next']}")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m automc run", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)
    p = sub.add_parser("start", help="show the plan, ask, start")
    p.add_argument("case", nargs="?")
    p.add_argument("--cases", help="comma list of cases, run in turn")
    p.add_argument("--sweep", help="a sweep name: its states in turn")
    p.add_argument("--threads", type=int)
    p.add_argument("--concurrent", action="store_true", help="allow it alongside another active run")
    p.add_argument("--script", help="a Python file to run as run.py in a new run folder of CASE (a script run)")
    p.add_argument("--timeout", type=int, help="a script run's time limit in seconds")
    p.add_argument("--yes", action="store_true", help="approve without asking (you typed the command)")
    p.add_argument("--json", action="store_true")
    for name in ("check", "watch"):
        q = sub.add_parser(name)
        q.add_argument("job_id", nargs="?")
        q.add_argument("--json", action="store_true")
    for name in ("pause", "resume", "checkpoint", "stop", "cancel"):
        q = sub.add_parser(name)
        q.add_argument("job_id")
        q.add_argument("--json", action="store_true")
        if name == "resume":
            q.add_argument("--threads", type=int)
            q.add_argument("--yes", action="store_true")
    q = sub.add_parser("jobs")
    q.add_argument("--active", action="store_true")
    q.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    def show(out):
        if a.json:
            print(json.dumps(out, indent=2))
        elif isinstance(out, list):
            for j in out:
                _print_job(j)
            if not out:
                print("no jobs")
        else:
            _print_job(out)

    try:
        if a.command == "start":
            script = None
            if a.script:
                try:
                    script = open(a.script, encoding="utf-8").read()
                except OSError as e:
                    raise OpenMCError("script", f"cannot read {a.script}: {e}") from None
            plan = runs.plan_run(case=a.case, cases=[c.strip() for c in a.cases.split(",") if c.strip()] if a.cases else None,
                                 sweep=a.sweep, threads=a.threads, concurrent=a.concurrent, script=script, timeout_s=a.timeout)
            if not _ask(plan, a.yes):
                print("not started")
                return 1
            job = runs.start(plan, approved_by="cli")
            show(job)
            if not a.json:
                print(f"Started; the command returns now and the run continues. Follow it: python -m automc run watch {job['job_id']}")
        elif a.command == "check":
            show(runs.check(job_id=a.job_id))
        elif a.command == "watch":
            job = runs.check(job_id=a.job_id)
            last = None
            try:
                while True:
                    line = (job["status"], (job.get("batches") or {}).get("done"), json.dumps(job.get("states")))
                    if line != last:
                        show(job)
                        last = line
                    if job["status"] not in ("queued", "running", "paused"):
                        break
                    time.sleep(2)
                    job = runs.check(job_id=job["job_id"])
            except KeyboardInterrupt:
                print("\nstopped watching; the run goes on")
        elif a.command == "resume":
            job = runs.check(job_id=a.job_id)
            if job["status"] in ("stopped", "interrupted", "cancelled"):
                plan = runs.plan_restart(a.job_id, threads=a.threads)
                if not _ask(plan, a.yes):
                    print("not started")
                    return 1
                show(runs.restart(plan, approved_by="cli"))
            else:
                show(runs.control(a.job_id, "resume"))
        elif a.command in ("pause", "checkpoint", "stop"):
            out = runs.control(a.job_id, a.command)
            show(out)
            if not a.json:
                print(f"    {a.command} requested: {out['note']}")
        elif a.command == "cancel":
            show(runs.cancel(a.job_id))
        elif a.command == "jobs":
            show([runs.summarize(j) for j in runs.list_jobs(active_only=a.active)])
    except OpenMCError as e:
        if getattr(a, "json", False):
            print(json.dumps({"ok": False, "error": e.payload()}, indent=2))
        else:
            print(f"{e.kind}: {e}", file=sys.stderr)
        return e.exit_code
    return 0
