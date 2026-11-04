"""
The job service: plan, start, check, pause, resume, checkpoint, stop, cancel and list runs of OpenMC models in the workspace. For any
model that sits in a case folder (<workspace>/<case>/model.xml); nothing here knows about a particular reactor. Every call returns in a
few seconds and none waits for a simulation (DESIGN.md section 4); the registry (jobs.py) is the source of truth.

Starting transport needs the user's approval, and only a surface can give it:

    plan = plan_run(case="my-case")           # what would run, its cost, which data it uses; starts nothing
    ... the command line or the chat shows the plan and asks the user ...
    start(plan, approved_by="cli")            # not an agent tool: the agent can only ask for a plan

`start` re-plans and refuses if anything changed since the user saw the plan (the model, the threads, the cases). The same holds for
continuing a stopped or interrupted run from its last statepoint (`plan_restart`, then `restart`). Pause, resume of a paused run,
checkpoint, stop and cancel need no approval: they never add compute.

A run is one worker process (worker.py) in a new run folder <case>/run-NNN with a frozen copy of model.xml. A sequence (several cases,
for example the states of a sweep) is one supervisor process (sequence.py) that runs them in turn and skips cases already done.

A script run (decision A13 of devops/AutoMC_Authoring_Plan.md) is for work that runs transport from inside Python: a criticality search,
a depletion, a loop over openmc.lib. `plan_run(case=..., script=...)` shows the script; after the user's approval it runs as run.py in a
new run folder holding a copy of the case's files (script_worker.py), with a time limit. It can be stopped or cancelled, not paused, and
it does not continue from a statepoint: it is planned again.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import posixpath
import re
import shutil
import signal
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import automc_paths

from . import jobs
from .errors import OpenMCError

WORKER_MODULE = "tools.openmc.worker"
SEQUENCE_MODULE = "tools.openmc.sequence"
SCRIPT_WORKER_MODULE = "tools.openmc.script_worker"
SCRIPT_NAME = "run.py"
SCRIPT_TIMEOUT, SCRIPT_MAX_TIMEOUT = 3600, 86400
MAX_SCRIPT = 200_000
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def workspace():
    return Path(automc_paths.WORKSPACE)


def _registry():
    """The folder of this workspace's run registry (.automc/jobs)."""
    return jobs.home(workspace())


def _case_name(text, extra=0):
    """True for a case named relative to the workspace: up to four folders deep (study-8/c8), plus `extra` levels (a sweep's state)."""
    parts = text.strip("/").split("/") if isinstance(text, str) else []
    return bool(parts) and len(parts) <= 4 + extra and all(_NAME.match(p) and not p.endswith(".") for p in parts)


def _need_posix():
    if os.name != "posix":
        raise OpenMCError("environment", "runs need Linux or WSL (the automc environment with OpenMC)", kind="MissingLibrary", exit_code=4)


def _need_cross_sections():
    if not os.environ.get("OPENMC_CROSS_SECTIONS"):
        raise OpenMCError("environment", "OPENMC_CROSS_SECTIONS is not set; the run could not load nuclear data", kind="MissingLibrary",
                          exit_code=4)


# ----------------------------------------------------------------------------------------------------------------------
# cases
# ----------------------------------------------------------------------------------------------------------------------
def case_folder(case, field="case"):
    """A case named relative to the workspace (a folder up to four deep, such as c8 or study-8/c8, or a sweep's state such as
    my-sweep/state-001) that holds model.xml."""
    if not isinstance(case, str) or not case:
        raise OpenMCError(field, "must be a case name in the workspace")
    if not _case_name(case, extra=1):
        raise OpenMCError(field, f"must be a case name in the workspace (folders up to four deep, or a sweep's state), not a path: "
                                 f"{case!r}")
    folder = workspace() / Path(*case.strip("/").split("/"))
    if not (folder / "model.xml").is_file():
        raise OpenMCError(field, f"no case {case!r} with a model.xml in the workspace", kind="NotFound")
    return folder


def model_settings(model_xml):
    """particles, batches, inactive, seed and run mode from the <settings> of a model.xml."""
    try:
        root = ET.parse(model_xml).getroot()
    except (ET.ParseError, OSError) as e:
        raise OpenMCError("case", f"cannot read {Path(model_xml).name}: {e}") from None
    st = root.find("settings") if root.tag != "settings" else root
    if st is None:
        raise OpenMCError("case", "model.xml has no <settings>")

    def num(tag, cast=int):
        t = st.findtext(tag)
        return cast(t) if t is not None and t.strip() else None
    out = {"run_mode": (st.findtext("run_mode") or "eigenvalue").strip(), "particles": num("particles"), "batches": num("batches"),
           "inactive": num("inactive") or 0, "seed": num("seed")}
    if not out["particles"] or not out["batches"]:
        raise OpenMCError("case", "model.xml settings need particles and batches")
    return out


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _built(folder):
    """What the builder recorded about a case (result.json), or {}."""
    return jobs.read_json(Path(folder) / "result.json", {}) or {}


def _label(built):
    fd = built.get("facility_data")
    return fd["distribution"] if isinstance(fd, dict) and fd.get("distribution") else "unknown"


def _measured_rate(folder, particles):
    """Histories per second per thread of an earlier run of this case, from its events, or None."""
    for rf in reversed(jobs.run_folders(folder)):
        ev = [e for e in jobs.read_events(rf / "events.jsonl") if e.get("event") == "batch" and e.get("wall_s")]
        started = [e for e in jobs.read_events(rf / "events.jsonl") if e.get("event") == "started"]
        if len(ev) >= 3 and started:
            per_batch = sorted(e["wall_s"] for e in ev)[len(ev) // 2]
            return particles / per_batch / max(1, int(started[-1].get("threads") or 1))
    return None


def _estimate(folder, settings, threads, batches_left=None):
    """{seconds_low, seconds_high, basis} for the batches still to run, or None when nothing is known about the speed."""
    histories = settings["particles"] * (settings["batches"] if batches_left is None else batches_left)
    rate = _measured_rate(folder, settings["particles"])
    if rate:
        s = histories / (rate * threads)
        return {"seconds_low": round(0.8 * s, 1), "seconds_high": round(1.25 * s, 1), "basis": "measured on an earlier run of this case"}
    est = (_built(folder).get("summary") or {}).get("run_estimate") or {}
    if est.get("seconds_low") and est.get("histories") and est.get("threads"):
        scale = histories / est["histories"] * est["threads"] / threads
        return {"seconds_low": round(est["seconds_low"] * scale, 1), "seconds_high": round(est["seconds_high"] * scale, 1),
                "basis": "the builder's estimate"}
    return None


def _duration(s):
    if s is None:
        return "unknown"
    return f"{s:.0f} s" if s < 90 else (f"{s / 60:.0f} min" if s < 5400 else f"{s / 3600:.1f} h")


def case_done(folder, batches):
    """A case counts as done if one of its runs finished successfully (or, for runs made before the job service, has the final
    statepoint)."""
    for rf in jobs.run_folders(folder):
        sent = jobs.read_sentinel(rf / "run.log")
        if (sent and sent[1] == "success") or (rf / f"statepoint.{batches}.h5").is_file() and sent is None:
            return True
    return False


# ----------------------------------------------------------------------------------------------------------------------
# plans
# ----------------------------------------------------------------------------------------------------------------------
def _threads(threads):
    if threads is None:
        return os.cpu_count() or 1
    if not isinstance(threads, int) or isinstance(threads, bool) or not 1 <= threads <= 1024:
        raise OpenMCError("threads", f"must be a whole number from 1 to 1024, got {threads!r}")
    return threads


def _sweep_cases(sweep):
    folder = workspace() / sweep.strip("/") if _case_name(sweep) else None
    manifest = jobs.read_json(folder / "sweep.json") if folder is not None else None
    if not manifest or not manifest.get("states"):
        raise OpenMCError("sweep", f"no sweep {sweep!r} with a sweep.json in the workspace", kind="NotFound")
    return [f"{sweep}/{st['folder']}" for st in manifest["states"]]


def _plan_id(plan):
    keys = {k: plan[k] for k in ("kind", "threads", "concurrent", "job_id", "restart_from")}
    keys["cases"] = [(c["case"], c["model_sha256"]) for c in plan["cases"]]
    return hashlib.sha256(json.dumps(keys, sort_keys=True).encode()).hexdigest()[:16]


def _running_jobs():
    return [j for j in list_jobs(active_only=True) if j["status"] == "running"]


def plan_run(case=None, cases=None, sweep=None, threads=None, concurrent=False, script=None, timeout_s=None):
    """
    What starting would do, without starting anything: the cases, their settings and histories, the run folders it would create,
    an estimate, which facility data the models were built from, and what else is running. One of case, cases or sweep; with
    `script` (the text of run.py), a script run of one case.
    """
    # A script run only with a script. A model whose service fills every argument (OpenAI's strict tool calls) sends script "" and a
    # timeout_s for an ordinary run, as it sends sweep "" and cases []: empty means not given, and a timeout without a script is unused.
    if case and isinstance(cases, list) and [str(c).strip("/") for c in cases] == [str(case).strip("/")]:
        cases = None                                       # the same one case twice, as such a model first sends it: one case
    blank = script is None or (isinstance(script, str) and not script.strip())
    if not blank:                                          # a script that is not text is refused by _plan_script
        if cases or sweep or not case:
            raise OpenMCError("script", "a script run takes one case (the folder it runs in), not cases or a sweep")
        return _plan_script(case, script, threads, concurrent, timeout_s)
    given = [x for x in (case, cases, sweep) if x]
    if len(given) != 1:
        raise OpenMCError("case", "give exactly one of case, cases or sweep")
    names = [case] if case else (list(cases) if cases else _sweep_cases(sweep))
    if not isinstance(names, list) or not names or len(names) > 200 or len(set(names)) != len(names):
        raise OpenMCError("cases", "must be a list of 1 to 200 different case names")
    threads = _threads(threads)
    rows, total, low, high, unknown, labels = [], 0, 0.0, 0.0, False, set()
    for name in names:
        folder = case_folder(name, "case" if case else "cases")
        st = model_settings(folder / "model.xml")
        done = case_done(folder, st["batches"]) if not case else False
        est = None if done else _estimate(folder, st, threads)
        built = _built(folder)
        labels.add(_label(built))
        histories = 0 if done else st["particles"] * st["batches"]
        rows.append({"case": name, "model_sha256": _sha256(folder / "model.xml"), **st, "histories": histories, "done": done,
                     "run_folder": None if done else f"{name}/{jobs.next_run_name(folder)}", "estimate": est,
                     "built_from": {"preset": built.get("preset"), "spec_hash": built.get("spec_hash"),
                                    "assumptions": [a.get("id") for a in built.get("assumptions", [])]} if built else None})
        total += histories
        if est:
            low, high = low + est["seconds_low"], high + est["seconds_high"]
        elif not done:
            unknown = True
    running = _running_jobs()
    distribution = "restricted" if "restricted" in labels else ("unknown" if "unknown" in labels else "public")
    warnings = []
    if running and not concurrent:
        warnings.append(f"another run is active (job {running[0]['job_id']}, {running[0]['case']}); starting would be refused unless "
                        f"concurrent is true")
    if running and concurrent:
        warnings.append(f"runs at the same time as {len(running)} other run(s); they share the processor and each is slower")
    if all(r["done"] for r in rows):
        warnings.append("every case already has a finished run; nothing would run")
    plan = {"kind": "run" if case else "sequence", "cases": rows, "threads": threads, "concurrent": bool(concurrent), "job_id": None,
            "restart_from": None, "histories": total,
            "estimate": None if unknown and not low else {"seconds_low": round(low, 1), "seconds_high": round(high, 1),
                                                        "partial": unknown},
            "facility_data": distribution, "running_jobs": [{"job_id": j["job_id"], "case": j["case"]} for j in running],
            "warnings": warnings}
    plan["plan_id"] = _plan_id(plan)
    plan["text"] = describe_plan(plan)
    return plan


def describe_plan(plan):
    """The plan in a few lines, as the user is asked about it."""
    todo = [c for c in plan["cases"] if not c["done"]]
    est = plan["estimate"]
    when = (f"about {_duration(est['seconds_low'])} to {_duration(est['seconds_high'])}" + (" (some cases have no estimate)"
            if est.get("partial") else "")) if est else "no estimate (no earlier run or builder estimate)"
    head = {"run": "Run", "sequence": "Run in turn", "restart": "Continue"}[plan["kind"]]
    lines = [f"{head} {len(todo)} case(s) on {plan['threads']} threads: {plan['histories']:,} histories, {when}."]
    for c in plan["cases"][:12]:
        if c["done"]:
            lines.append(f"  {c['case']}: already finished, skipped")
        else:
            extra = f", from {plan['restart_from']}" if plan.get("restart_from") else ""
            seed = f"seed {c['seed']}" if c["seed"] is not None else "no seed in model.xml (OpenMC's default, 1)"
            lines.append(f"  {c['case']}: {c['particles']:,} x {c['batches']} batches ({c['inactive']} inactive), {seed}, "
                         f"in {c['run_folder']}{extra}")
    if len(plan["cases"]) > 12:
        lines.append(f"  ... and {len(plan['cases']) - 12} more")
    lines.append(f"Facility data: {plan['facility_data']}.")
    lines += [f"Warning: {w}" for w in plan["warnings"]]
    return "\n".join(lines)


def _case_files(folder):
    """The files a script run gets a copy of: the case folder's own files (model.xml, build.py and what it wrote), not its runs."""
    return sorted(p for p in Path(folder).iterdir() if p.is_file())


def _plan_script(case, script, threads, concurrent, timeout_s):
    from . import authoring                                # authoring imports this module
    folder = case_folder(case)
    if not isinstance(script, str) or not script.strip():
        raise OpenMCError("script", "must be the text of run.py: Python that runs OpenMC, for example a criticality search or a depletion")
    if len(script) > MAX_SCRIPT:
        raise OpenMCError("script", f"is {len(script):,} characters; at most {MAX_SCRIPT:,}")
    timeout = SCRIPT_TIMEOUT if timeout_s is None else timeout_s
    if not isinstance(timeout, int) or isinstance(timeout, bool) or not 1 <= timeout <= SCRIPT_MAX_TIMEOUT:
        raise OpenMCError("timeout_s", f"must be a whole number of seconds from 1 to {SCRIPT_MAX_TIMEOUT}")
    threads = _threads(threads)
    case = case.strip("/")
    label, why = authoring._label(script)
    case_label = _label(_built(folder))
    if authoring.LABEL_ORDER.get(case_label, 1) > authoring.LABEL_ORDER.get(label, 1):
        label, why = case_label, "the case it runs in"
    running = _running_jobs()
    warnings = []
    if running and not concurrent:
        warnings.append(f"another run is active (job {running[0]['job_id']}, {running[0]['case']}); starting would be refused unless "
                        f"concurrent is true")
    if running and concurrent:
        warnings.append(f"runs at the same time as {len(running)} other run(s); they share the processor and each is slower")
    files = [p.name for p in _case_files(folder)]
    plan = {"kind": "script", "cases": [{"case": case, "model_sha256": _sha256(folder / "model.xml"), "done": False,
                                         "run_folder": f"{case}/{jobs.next_run_name(folder)}"}],
            "script": script, "script_sha256": hashlib.sha256(script.encode("utf-8")).hexdigest(), "timeout_s": timeout,
            "threads": threads, "concurrent": bool(concurrent), "job_id": None, "restart_from": None, "histories": None, "estimate": None,
            "copies": files, "facility_data": label, "label_reason": why,
            "running_jobs": [{"job_id": j["job_id"], "case": j["case"]} for j in running], "warnings": warnings}
    plan["plan_id"] = hashlib.sha256(json.dumps([plan["kind"], case, plan["cases"][0]["model_sha256"], plan["script_sha256"], timeout,
                                                 threads, plan["concurrent"]]).encode()).hexdigest()[:16]
    plan["text"] = describe_script(plan)
    return plan


def describe_script(plan):
    import console
    c = plan["cases"][0]
    lines = plan["script"].rstrip("\n").split("\n")
    width = len(str(len(lines)))
    code = "\n".join(f"  {i:>{width}} | {line}" for i, line in enumerate(lines, 1))
    copies = ", ".join(plan["copies"][:8]) + (f" and {len(plan['copies']) - 8} more" if len(plan["copies"]) > 8 else "")
    rows = [("Case", f"workspace/{c['case']}/"),
            ("Runs", f"{SCRIPT_NAME} in {c['run_folder']}/ on {plan['threads']} threads, at most {_duration(plan['timeout_s'])}, no keys; "
                     f"it may run transport as often as it says (a search or a depletion runs many simulations)"),
            ("Copies", f"the case's files into the run folder: {copies}"),
            ("Label", f"{plan['facility_data']} ({plan['label_reason']})"),
            ("Control", "stop or cancel; it cannot pause or continue from a statepoint"),
            ("Note", "not a sandbox: the script can do what you can, so read it before you say yes")]
    out = [console.banner("Run a Python script that runs OpenMC"), console.fields(rows)]
    out += [f"  Warning: {w}" for w in plan["warnings"]]
    out += ["", f"  {SCRIPT_NAME} ({len(lines)} line(s)):", code]
    return "\n".join(out)


# ----------------------------------------------------------------------------------------------------------------------
# launching
# ----------------------------------------------------------------------------------------------------------------------
def _launch(module_args, cwd, log_path):
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([str(automc_paths.SRC)] + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    python = os.environ.get("AUTOMC_PYTHON") or sys.executable
    with open(log_path, "ab") as log:
        proc = subprocess.Popen([python, "-m", *module_args], cwd=str(cwd), env=env, stdout=log, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, start_new_session=True)   # its own process group: it outlives this process
    jobs.CHILDREN[proc.pid] = proc
    return proc.pid, jobs.process_started_at(proc.pid)


def named_files(folder):
    """
    The files the case's model.xml names by path: [(kind, as written, Path)] for the multigroup library (materials/cross_sections),
    DAGMC geometries (dagmc_universe filename) and unstructured meshes (mesh filename). A relative path is read from the case folder;
    `inside` says whether a run folder can carry it (relative, no '..').
    """
    try:
        root = ET.parse(Path(folder) / "model.xml").getroot()
    except (ET.ParseError, OSError):
        return []
    named = [("library", root.findtext("materials/cross_sections"))]
    named += [("DAGMC geometry", e.get("filename")) for e in root.iter("dagmc_universe")]
    named += [("unstructured mesh", e.findtext("filename")) for e in root.iter("mesh") if e.get("type") == "unstructured"]
    out, seen = [], set()
    for kind, text in named:
        text = (text or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        rel = Path(text)
        out.append({"kind": kind, "text": text, "path": rel if rel.is_absolute() else Path(folder) / rel,
                    "inside": not rel.is_absolute() and ".." not in rel.parts})
    return out


def prepare_run_folder(folder, run_name):
    """A new run folder with a frozen copy of the case's model.xml, the files it names by a relative path inside the case (a
    multigroup library, a CAD geometry, a mesh: linked, or copied where the file system cannot link; a case with runs is never
    rebuilt, so they stay as they were), and an empty control file. Never reuses a folder."""
    rf = Path(folder) / run_name
    rf.mkdir()
    shutil.copy2(Path(folder) / "model.xml", rf / "model.xml")
    for f in named_files(folder):
        if not f["inside"] or not f["path"].is_file():
            continue
        target = rf / f["text"]
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.link(f["path"], target)
        except OSError:
            shutil.copy2(f["path"], target)
    jobs.atomic_write_json(rf / "control.json", {"seq": 0, "command": "run"})
    return rf


def worker_args(threads, batches, segment, restart=None):
    return [WORKER_MODULE, "--threads", str(threads), "--batches", str(batches), "--segment", str(segment)] + (
        ["--restart", restart] if restart else [])


def _namespace():
    return "wsl" if "microsoft" in platform.release().lower() else "native"


def start(plan, approved_by):
    """
    Start what a plan describes. `approved_by` names the surface that asked the user ("cli", "chat"); there is no default, and no
    agent tool calls this. The plan is made again and must be identical to the one the user approved.
    """
    _need_posix()
    _need_cross_sections()
    if not isinstance(approved_by, str) or not approved_by.strip():
        raise OpenMCError("approved_by", "a run starts only with the user's approval, given through a surface", kind="NeedsApproval",
                          exit_code=3)
    if plan.get("kind") == "restart":
        return restart(plan, approved_by)
    if plan.get("kind") == "script":
        return _start_script(plan, approved_by)
    names = [c["case"] for c in plan["cases"]]
    fresh = plan_run(case=names[0] if plan["kind"] == "run" else None, cases=names if plan["kind"] == "sequence" else None,
                     threads=plan["threads"], concurrent=plan["concurrent"])
    if fresh["plan_id"] != plan.get("plan_id"):
        raise OpenMCError("plan", "the cases or settings changed since this plan was made; make a new plan and ask again", kind="NotAllowed",
                          exit_code=3)
    running = _running_jobs()
    if running and not plan["concurrent"]:
        raise OpenMCError("case", f"a run is already active (job {running[0]['job_id']}, {running[0]['case']}); wait, pause or stop "
                                  f"it, or plan with concurrent true", kind="NotAllowed", exit_code=3)
    if all(c["done"] for c in fresh["cases"]):
        raise OpenMCError("cases", "every case already has a finished run; nothing to start", kind="NotAllowed", exit_code=3)
    ws = workspace()
    common = {"backend": "lib", "pid_namespace": _namespace(), "threads": plan["threads"], "segment": 1, "approved_by": approved_by,
              "concurrent": bool(plan["concurrent"]),
              "approved_at": jobs.now(), "plan_id": plan["plan_id"], "facility_data": fresh["facility_data"],
              "histories": fresh["histories"], "estimate": fresh["estimate"], "restarts": [], "returncode": None,
              "derived_from": None, "keff_latest": None, "last_statepoint": None}
    if plan["kind"] == "run":
        c = fresh["cases"][0]
        folder = case_folder(c["case"])
        rf = prepare_run_folder(folder, Path(c["run_folder"]).name)
        pid, started = _launch(worker_args(plan["threads"], c["batches"], 1), rf, rf / "run.log")
        job = jobs.create_job(jobs.home(ws), kind="run", case=c["case"], cases=[c["case"]], run_folder=c["run_folder"], attempt=rf.name,
                              status="running", pid=pid, pid_started_at=started, batches_total=c["batches"], batches_done=0,
                              particles=c["particles"], inactive=c["inactive"], seed=c["seed"], model_sha256=c["model_sha256"],
                              started_at=jobs.now(), **common)
        return summarize(job)
    shared = posixpath.commonpath(names) if len(names) > 1 else ""      # a sweep's states share the sweep's folder
    job = jobs.create_job(jobs.home(ws), kind="sequence", case=shared or names[0], cases=names,
                          status="running", pid=None, pid_started_at=None, started_at=jobs.now(), states=[], **common)
    jf = ws / jobs.JOB_FOLDERS / job["job_id"]
    jf.mkdir(parents=True)
    jobs.atomic_write_json(jf / "sequence.json", {"cases": names, "threads": plan["threads"], "workspace": str(ws)})
    jobs.atomic_write_json(jf / "control.json", {"seq": 0, "command": "run"})
    pid, started = _launch([SEQUENCE_MODULE, "--job-folder", str(jf), "--segment", "1"], jf, jf / "sequence.log")
    job = jobs.update_job(jobs.home(ws), job["job_id"], pid=pid, pid_started_at=started, job_folder=f"{jobs.JOB_FOLDERS}/{job['job_id']}")
    return summarize(job)


def _start_script(plan, approved_by):
    c = plan["cases"][0]
    fresh = plan_run(case=c["case"], script=plan.get("script"), threads=plan["threads"], concurrent=plan["concurrent"],
                     timeout_s=plan["timeout_s"])
    if fresh["plan_id"] != plan.get("plan_id"):
        raise OpenMCError("plan", "the script, the case or the settings changed since this plan was made; make a new plan and ask again",
                          kind="NotAllowed", exit_code=3)
    running = _running_jobs()
    if running and not plan["concurrent"]:
        raise OpenMCError("case", f"a run is already active (job {running[0]['job_id']}, {running[0]['case']}); wait, pause or stop "
                                  f"it, or plan with concurrent true", kind="NotAllowed", exit_code=3)
    c = fresh["cases"][0]
    folder = case_folder(c["case"])
    rf = folder / Path(c["run_folder"]).name
    rf.mkdir()
    for p in _case_files(folder):
        shutil.copy2(p, rf / p.name)
    (rf / SCRIPT_NAME).write_text(fresh["script"], encoding="utf-8")
    jobs.atomic_write_json(rf / "control.json", {"seq": 0, "command": "run"})
    pid, started = _launch([SCRIPT_WORKER_MODULE, "--threads", str(fresh["threads"]), "--timeout", str(fresh["timeout_s"]),
                            "--segment", "1"], rf, rf / "run.log")
    job = jobs.create_job(jobs.home(workspace()), kind="script", case=c["case"], cases=[c["case"]], run_folder=c["run_folder"],
                          attempt=rf.name, status="running", pid=pid, pid_started_at=started, model_sha256=c["model_sha256"],
                          script_sha256=fresh["script_sha256"], timeout_s=fresh["timeout_s"], backend="script",
                          pid_namespace=_namespace(), threads=fresh["threads"], segment=1, approved_by=approved_by,
                          concurrent=bool(fresh["concurrent"]), approved_at=jobs.now(), plan_id=fresh["plan_id"],
                          facility_data=fresh["facility_data"], histories=None, estimate=None, restarts=[], returncode=None,
                          started_at=jobs.now())
    return summarize(job)


# ----------------------------------------------------------------------------------------------------------------------
# settling and checking
# ----------------------------------------------------------------------------------------------------------------------
def _job_files(job):
    ws = workspace()
    if job["kind"] in ("run", "script"):
        folder = ws / job["run_folder"]
        return folder, folder / "run.log"
    folder = ws / job["job_folder"]
    return folder, folder / "sequence.log"


def _last_error_line(log):
    try:
        lines = Path(log).read_text(encoding="utf-8", errors="replace").splitlines()[-400:]
    except OSError:
        return None
    errors = [l.strip() for l in lines if "ERROR" in l or "Traceback" in l]
    return errors[-1][:300] if errors else None


def settle(job):
    """Bring an active job's registry record up to date from its files and its process. Returns the record."""
    jobs.reap_children()                                 # reap our own finished children, whatever their files say
    if job["status"] not in jobs.ACTIVE or not job.get("pid"):
        return job
    folder, log = _job_files(job)
    st = jobs.read_json(folder / "status.json", {}) or {}
    sent = jobs.read_sentinel(log)
    fields = {}
    if job["kind"] == "run":
        if st.get("segment") == job["segment"]:
            fields["phase"] = st.get("state")                 # starting (loading nuclear data), running, paused, finished
        if st.get("segment") == job["segment"] and st.get("batch") is not None:
            fields.update(batches_done=st["batch"], last_statepoint=st.get("last_statepoint") or job.get("last_statepoint"),
                          keff_latest={"value": st.get("keff"), "sigma": st.get("sigma"), "batch": st["batch"]})
    elif job["kind"] == "script":
        if st.get("segment") == job["segment"]:
            fields.update(phase=st.get("state"), files=st.get("files"), seconds=st.get("seconds"))
    else:
        fields["states"] = st.get("states", job.get("states", []))
    if sent is not None and sent[2] == job["segment"]:
        fields.update(status=sent[1], returncode=sent[0])
        if sent[1] == "failed":
            fields["error"] = st.get("error") or _last_error_line(log)
    elif jobs.process_alive(job["pid"], job.get("pid_started_at")):
        fields["status"] = "paused" if st.get("state") == "paused" and st.get("segment") == job["segment"] else "running"
    elif job.get("cancel_requested"):
        fields["status"] = "cancelled"
    else:
        err = _last_error_line(log)
        fields.update(status="failed" if err else "interrupted",
                      error=err or "the process ended without finishing (no AUTOMC_JOB_DONE line); the machine may have restarted "
                                   "or the process was killed")
    return jobs.update_job(_registry(), job["job_id"], **fields) or job


def reconcile():
    """Settle every active job (AutoSAM's startup reconciliation). Returns {checked, running, paused, finished}."""
    out = {"checked": 0, "running": [], "paused": [], "finished": []}
    for job in jobs.list_jobs(_registry(), active_only=True):
        out["checked"] += 1
        j = settle(job)
        out["running" if j["status"] == "running" else "paused" if j["status"] == "paused" else "finished"].append(j["job_id"])
    return out


def _find(job_id=None, case=None):
    job = jobs.find_job(_registry(), job_id=job_id, case=case)
    if job is None:
        if job_id:
            msg = f"no job {job_id!r} in the registry"
        elif case:
            msg = (f"no job of case {case!r} in the registry: the case has never been run as a job (it may still exist; "
                   f"OpenMCRunStartTool plans a run and says whether the case exists)")
        else:
            msg = "no jobs in the registry"
        raise OpenMCError("job_id" if job_id else "case", msg, kind="NotFound")
    return settle(job)


def _progress(job):
    folder, _ = _job_files(job)
    ev = [e for e in jobs.read_events(folder / "events.jsonl") if e.get("segment") == job.get("segment")] if job["kind"] == "run" else []
    batches = [e for e in ev if e.get("event") == "batch"]
    out = {}
    if batches:
        per_batch = sum(e["wall_s"] for e in batches[-10:]) / len(batches[-10:])
        out["seconds_per_batch"] = round(per_batch, 2)
        out["histories_per_second"] = round(job["particles"] / per_batch, 1) if per_batch else None
        left = job["batches_total"] - (job.get("batches_done") or 0)
        if job["status"] == "running":
            out["remaining_s"] = round(left * per_batch, 1)
            out["remaining"] = _duration(left * per_batch)
        elif job["status"] == "paused":                       # no time passes towards the end while it is held
            out["remaining_once_resumed"] = _duration(left * per_batch)
    return out


def summarize(job):
    """A job as a tool or the command line reports it."""
    out = {k: job.get(k) for k in ("job_id", "kind", "case", "status", "phase", "approved_by", "threads", "segment", "facility_data",
                                    "created_at", "started_at", "completed_at", "error")}
    if job["kind"] == "script":
        out.update(run_folder=job["run_folder"], script=SCRIPT_NAME, timeout_s=job.get("timeout_s"), seconds=job.get("seconds"))
        files = job.get("files") or []
        if files:
            out["files"] = files[:40] + ([f"... and {len(files) - 40} more"] if len(files) > 40 else [])
        if job["status"] == "success":
            out["next"] = (f"read what the script wrote in {job['run_folder']} (run.log has its output): a post.py through PythonRunTool, "
                           f"or RunResultReaderTool on a folder that holds a statepoint")
        elif job["status"] in ("stopped", "interrupted", "cancelled", "failed"):
            out["next"] = ("read run.log in the run folder for the reason; a script run does not continue, so fix the script if needed "
                           "and plan it again with OpenMCRunStartTool (the user approves)")
        return out
    if job["kind"] == "run":
        out.update(run_folder=job["run_folder"], batches={"done": job.get("batches_done"), "total": job.get("batches_total"),
                                                           "inactive": job.get("inactive")},
                   keff=job.get("keff_latest"), last_statepoint=job.get("last_statepoint"), **_progress(job))
        if job["status"] == "success":
            out["next"] = f"read the result with RunResultReaderTool (run_folder {job['run_folder']})"
        elif job["status"] == "paused":
            sp = job.get("last_statepoint")
            out["next"] = ((f"paused after batch {job.get('batches_done')}; it waits in memory, and {Path(sp).name} holds the run so far, so "
                            f"it can also continue from there if the process ends. Resume: OpenMCRunResumeTool, or python -m automc run "
                            f"resume {job['job_id']}") if sp else
                           f"paused before its first batch, so no statepoint yet. Resume: OpenMCRunResumeTool, or python -m automc run "
                           f"resume {job['job_id']}")
    else:
        out.update(cases=job.get("cases"), states=job.get("states"))
    if job["status"] in ("stopped", "interrupted", "cancelled"):
        last = _latest_statepoint(workspace() / job["run_folder"]) if job["kind"] == "run" else None
        can = job["kind"] == "sequence" or last is not None
        out["next"] = (f"it can continue from its last statepoint{f' ({last[1]})' if last else ''} with a resume (which needs the user's "
                       f"approval)" if can else "it wrote no statepoint; start the case again to run it")
    return out


LOG_TAIL_LINES = 25


def _log_tail(log, n=LOG_TAIL_LINES):
    try:
        return [l.rstrip() for l in Path(log).read_text(encoding="utf-8", errors="replace").splitlines()[-n:]]
    except OSError:
        return []


def check(job_id=None, case=None):
    job = _find(job_id, case)
    out = summarize(job)
    folder, log = _job_files(job)
    out["recent_events"] = jobs.read_events(folder / "events.jsonl", last=5) if job["kind"] in ("run", "script") else []
    if job["status"] in ("failed", "interrupted"):
        out["log_tail"] = _log_tail(log)                     # what came before the error line (overlap lines before a crash)
    return out


def list_jobs(active_only=False):
    """Every job (settled), newest last."""
    return [settle(j) for j in jobs.list_jobs(_registry(), active_only=active_only)]


# ----------------------------------------------------------------------------------------------------------------------
# control
# ----------------------------------------------------------------------------------------------------------------------
def _send(job, command):
    folder, _ = _job_files(job)
    c = jobs.read_json(folder / "control.json", {}) or {}
    seq = int(c.get("seq", 0)) + 1
    jobs.atomic_write_json(folder / "control.json", {"seq": seq, "command": command, "sent_at": jobs.now()})
    return seq


def _pending(job):
    """The last command sent that the worker has not acted on yet, or None."""
    folder, _ = _job_files(job)
    c = jobs.read_json(folder / "control.json", {}) or {}
    st = jobs.read_json(folder / "status.json", {}) or {}
    return c.get("command") if int(c.get("seq", 0)) > int(st.get("control_seq", 0)) else None


def control(job_id, command):
    """pause, resume (a paused run), checkpoint or stop. Takes effect at the end of the current batch."""
    if command not in ("pause", "resume", "checkpoint", "stop"):
        raise OpenMCError("command", f"must be pause, resume, checkpoint or stop, got {command!r}")
    job = _find(job_id)
    if job["kind"] == "script" and command != "stop":
        raise OpenMCError("command", f"job {job['job_id']} is a script run: it can be stopped or cancelled, not paused, resumed or "
                                     f"checkpointed (only the script knows how its simulations follow one another)", kind="NotAllowed",
                          exit_code=3)
    if job["status"] not in ("running", "paused"):
        hint = " Continuing it from its last statepoint is a restart, which needs the user's approval." if (
            command == "resume" and job["status"] in ("stopped", "interrupted", "cancelled")) else ""
        raise OpenMCError("job_id", f"job {job['job_id']} is {job['status']}; {command} needs a running or paused job.{hint}",
                          kind="NotAllowed", exit_code=3)
    if command == "resume" and job["status"] != "paused" and _pending(job) != "pause":
        raise OpenMCError("job_id", f"job {job['job_id']} is running, not paused", kind="NotAllowed", exit_code=3)
    if command == "resume" and not job.get("concurrent") and [j for j in _running_jobs() if j["job_id"] != job["job_id"]]:
        raise OpenMCError("job_id", "another run is active; pause or stop it first", kind="NotAllowed", exit_code=3)
    seq = _send(job, command)
    note = ("takes effect when the run has loaded its nuclear data and finished its current batch" if job.get("phase") == "starting"
            else "takes effect at the end of the current batch")
    return {**summarize(job), "requested": command, "control_seq": seq, "note": f"{note}; check the job to see it"}


def cancel(job_id):
    """Kill the job's process group at once. Statepoints already written stay; nothing is deleted."""
    job = _find(job_id)
    if job["status"] not in jobs.ACTIVE:
        raise OpenMCError("job_id", f"job {job['job_id']} is {job['status']}; nothing to cancel", kind="NotAllowed", exit_code=3)
    jobs.update_job(_registry(), job["job_id"], cancel_requested=True)
    _need_posix()
    if job.get("pid") and jobs.process_alive(job["pid"], job.get("pid_started_at")):
        for sig, wait in ((signal.SIGTERM, 5.0), (signal.SIGKILL, 5.0)):
            try:
                os.killpg(job["pid"], sig)
            except (ProcessLookupError, PermissionError):
                break
            t = time.time()
            while time.time() - t < wait and jobs.process_alive(job["pid"], job.get("pid_started_at")):
                time.sleep(0.1)
            if not jobs.process_alive(job["pid"], job.get("pid_started_at")):
                break
    if job["kind"] == "script":                     # the script runs in its own process group; its worker ends it, unless it was killed
        st = jobs.read_json(_job_files(job)[0] / "status.json", {}) or {}
        child = st.get("child_pid")
        if child and jobs.process_alive(child, st.get("child_started_at")):
            for sig in (signal.SIGTERM, signal.SIGKILL):
                try:
                    os.killpg(child, sig)
                except (ProcessLookupError, PermissionError):
                    break
                t = time.time()
                while time.time() - t < 5.0 and jobs.process_alive(child, st.get("child_started_at")):
                    time.sleep(0.1)
                if not jobs.process_alive(child, st.get("child_started_at")):
                    break
    return summarize(settle(jobs.get_job(_registry(), job["job_id"])))


# ----------------------------------------------------------------------------------------------------------------------
# continuing from a statepoint
# ----------------------------------------------------------------------------------------------------------------------
def _latest_statepoint(run_folder):
    found = []
    for p in Path(run_folder).glob("statepoint.*.h5"):
        m = re.match(r"^statepoint\.(\d+)\.h5$", p.name)
        if m and int(m.group(1)) > 0:
            found.append((int(m.group(1)), p.name))
    return max(found) if found else None


def plan_restart(job_id, threads=None):
    """What continuing a stopped, interrupted or cancelled job would do. Starts nothing; `start` (or `restart`) runs it."""
    job = _find(job_id)
    if job["kind"] == "script":
        raise OpenMCError("job_id", f"job {job['job_id']} is a script run, which does not continue from a statepoint; plan the script "
                                    f"again with OpenMCRunStartTool (the user approves)", kind="NotAllowed", exit_code=3)
    if job["status"] not in ("stopped", "interrupted", "cancelled"):
        raise OpenMCError("job_id", f"job {job['job_id']} is {job['status']}; only a stopped, interrupted or cancelled job continues",
                          kind="NotAllowed", exit_code=3)
    threads = _threads(threads if threads is not None else job.get("threads"))
    if job["kind"] == "sequence":
        plan = plan_run(cases=job["cases"], threads=threads, concurrent=bool(job.get("concurrent")))
        plan.update(kind="restart", job_id=job["job_id"])
    else:
        folder = workspace() / job["run_folder"]
        sp = _latest_statepoint(folder)
        if sp is None:
            raise OpenMCError("job_id", f"job {job['job_id']} wrote no statepoint; start the case again instead", kind="NotAllowed",
                              exit_code=3)
        batch, name = sp
        if batch >= job["batches_total"]:
            raise OpenMCError("job_id", f"job {job['job_id']} already has its last statepoint ({name})", kind="NotAllowed", exit_code=3)
        settings = model_settings(folder / "model.xml")
        left = job["batches_total"] - batch
        est = _estimate(workspace() / job["case"], settings, threads, batches_left=left)
        running = _running_jobs()
        plan = {"kind": "restart", "job_id": job["job_id"], "restart_from": name, "threads": threads,
                "concurrent": bool(job.get("concurrent")), "histories": settings["particles"] * left,
                "cases": [{"case": job["case"], "model_sha256": _sha256(folder / "model.xml"), **settings, "done": False,
                           "histories": settings["particles"] * left, "run_folder": job["run_folder"], "estimate": est}],
                "estimate": est, "facility_data": job.get("facility_data"),
                "running_jobs": [{"job_id": j["job_id"], "case": j["case"]} for j in running],
                "warnings": [f"another run is active (job {running[0]['job_id']})"] if running else []}
    plan["plan_id"] = _plan_id(plan)
    plan["text"] = describe_plan(plan)
    return plan


def restart(plan, approved_by):
    """Continue a job as its plan_restart plan says, after the user's approval. Same rules as `start`."""
    _need_posix()
    _need_cross_sections()
    if not isinstance(approved_by, str) or not approved_by.strip():
        raise OpenMCError("approved_by", "a run continues only with the user's approval, given through a surface", kind="NeedsApproval",
                          exit_code=3)
    fresh = plan_restart(plan["job_id"], threads=plan["threads"])
    if fresh["plan_id"] != plan.get("plan_id"):
        raise OpenMCError("plan", "the job or its files changed since this plan was made; make a new plan and ask again",
                          kind="NotAllowed", exit_code=3)
    running = _running_jobs()
    if running and not plan["concurrent"]:
        raise OpenMCError("job_id", f"a run is already active (job {running[0]['job_id']}); wait, pause or stop it first",
                          kind="NotAllowed", exit_code=3)
    job = jobs.get_job(_registry(), plan["job_id"])
    segment = int(job.get("segment", 1)) + 1
    folder, log = _job_files(job)
    st = jobs.read_json(folder / "status.json", {}) or {}
    jobs.atomic_write_json(folder / "status.json", {**st, "state": "starting", "segment": segment, "updated_at": jobs.now()})
    _send(job, "run")                                  # commands sent to the earlier segment do not carry over
    if job["kind"] == "run":
        pid, started = _launch(worker_args(fresh["threads"], job["batches_total"], segment, fresh["restart_from"]), folder, log)
    else:
        pid, started = _launch([SEQUENCE_MODULE, "--job-folder", str(folder), "--segment", str(segment)], folder, log)
    restarts = list(job.get("restarts") or []) + [{"segment": segment, "from": fresh.get("restart_from"), "approved_by": approved_by,
                                                   "at": jobs.now(), "threads": fresh["threads"]}]
    job = jobs.update_job(_registry(), job["job_id"], status="running", pid=pid, pid_started_at=started, segment=segment,
                          threads=fresh["threads"], cancel_requested=False, error=None, returncode=None, restarts=restarts)
    return summarize(job)
