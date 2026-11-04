"""
General OpenMC agent tools, for any model: nothing here knows about a particular reactor. Same shape as the reactor tools: a dict of
arguments in, a JSON-serializable dict out, {"ok": false, "error": {"type", "field", "message", "exit_code"}} instead of raising.
Folders are given relative to the workspace and must stay inside it.
"""
from __future__ import annotations

import functools
import json
import math
from pathlib import Path

import automc_paths

from . import runs
from .errors import OpenMCError
from .results import NoResult, summarize


def as_tool(fn):
    @functools.wraps(fn)
    def handler(args=None):
        try:
            return {"ok": True, **fn(args if args is not None else {})}
        except OpenMCError as e:
            return {"ok": False, "error": e.payload()}
        except Exception as e:                                       # a bug: report it as data, never crash the caller
            return {"ok": False, "error": {"type": "Error", "field": None, "message": f"unexpected {type(e).__name__}: {e}", "exit_code": 1}}
    return handler


def _check(args, schema):
    if not isinstance(args, dict):
        raise OpenMCError("arguments", f"must be an object, got {type(args).__name__}")
    unknown = sorted(set(args) - set(schema["properties"]))
    if unknown:
        raise OpenMCError(unknown[0], f"unknown argument; allowed: {sorted(schema['properties'])}")
    missing = [k for k in schema["required"] if k not in args]
    if missing:
        raise OpenMCError(missing[0], "required argument")
    return args


def workspace_folder(rel, field):
    """A folder given relative to the workspace (or absolute inside it) -> absolute path. Anything outside is refused."""
    if not isinstance(rel, str) or not rel.strip():
        raise OpenMCError(field, "must be a folder relative to the workspace, for example my-case/run-001")
    ws = automc_paths.WORKSPACE.resolve()
    p = Path(rel)
    p = (p if p.is_absolute() else ws / p).resolve()
    if not (ws in p.parents):
        raise OpenMCError(field, f"must be a folder inside the workspace {ws}, got {rel!r}")
    return p


def _finite(x):
    return x if isinstance(x, (int, float)) and math.isfinite(x) else None


def _plain(obj):
    """Numbers read from a statepoint are numpy scalars; a tool returns plain JSON types only."""
    if isinstance(obj, dict):
        return {str(k): _plain(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_plain(v) for v in obj]
    if hasattr(obj, "item") and callable(obj.item) and not isinstance(obj, (str, bytes)):
        return obj.item()
    return obj


RUN_RESULT_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["run_folder"], "properties": {
    "run_folder": {"type": "string", "description": "the run folder relative to the workspace, for example my-case/run-001 "
                                                    "(it holds statepoint.N.h5 and, if the run was logged, run.log)"},
}}


@as_tool
def run_result_reader(args):
    """Summarize a finished OpenMC run. Reads only."""
    a = _check(args, RUN_RESULT_SCHEMA)
    folder = workspace_folder(a["run_folder"], "run_folder")
    try:
        i = summarize(str(folder))
    except NoResult as e:
        raise OpenMCError("run_folder", str(e), kind="NoResult") from None
    except ImportError as e:
        raise OpenMCError("environment", f"reading a statepoint needs OpenMC ({e}); use the Linux or WSL environment",
                                exit_code=4, kind="MissingLibrary") from None
    sim = i["runtime"].get("simulation") or i["runtime"].get("total simulation")
    threads = i["log"]["threads"]
    k, sigma = i["keff"]
    tallies = [{"name": name, "bins": n, "nonzero_bins": nz, "finite": finite, "nonnegative": nonneg}
               for name, (n, finite, nonneg, nz) in i["tallies"].items()]
    plausible = (sigma > 0 and 0.2 < k < 2.0 and not i["lost_particle_files"] and not i["log"]["errors"] and all(t["finite"] for t in tallies))
    out = {
        "run_folder": str(folder.relative_to(automc_paths.WORKSPACE.resolve()).as_posix()),
        "statepoint": i["file"], "seed": i["seed"],
        "keff": {"value": float(k), "sigma": float(sigma)},
        "batches": {"total": i["batches"], "inactive": i["inactive"], "particles_per_batch": i["particles"]},
        "k_by_batch": [round(float(x), 6) for x in i["gen"]],
        "active_batches": {"mean": _finite(i["active_mean"]), "spread": _finite(i["active_std"])},
        "runtime_s": {key: round(v, 3) for key, v in i["runtime"].items()},
        "histories_per_second": round(i["particles"] * i["batches"] / sim, 1) if sim else None,
        "threads": threads,
        "tallies": tallies,
        "lost_particle_files": i["lost_particle_files"],
        "log_warnings": i["log"]["warnings"][:20], "log_errors": i["log"]["errors"][:20],
        "plausible": bool(plausible),
        "plausible_means": "finite k-eff with a positive error, finite tallies, no lost particles, no errors in the log",
    }
    case_result = folder.parent / "result.json"                     # the build this run came from, if it was built by AutoMC
    if case_result.is_file():
        try:
            built = json.loads(case_result.read_text(encoding="utf-8"))
            out["built_from"] = {"case": built.get("name"), "preset": built.get("preset"), "spec_hash": built.get("spec_hash"),
                                 "assumptions": [x.get("id") for x in built.get("assumptions", [])]}
            out["facility_data"] = built.get("facility_data")
        except (OSError, ValueError):
            pass
    return _plain(out)


# ----------------------------------------------------------------------------------------------------------------------
# runs: start (needs the user), check, pause, resume, stop, cancel, list
# ----------------------------------------------------------------------------------------------------------------------
_JOB = {"job_id": {"type": "string", "description": "the job id (from a start, a check or the job list)"}}

START_SCHEMA = {"type": "object", "additionalProperties": False, "required": [], "properties": {
    "case": {"type": "string", "description": "one case in the workspace (a folder with model.xml), or sweep/state-NNN"},
    "cases": {"type": "array", "items": {"type": "string"}, "description": "several cases, run in turn as one job"},
    "sweep": {"type": "string", "description": "a sweep in the workspace: its states run in turn, finished ones skipped"},
    "threads": {"type": "integer", "minimum": 1, "maximum": 1024, "description": "OpenMP threads (default: all processors)"},
    "concurrent": {"type": "boolean", "description": "allow it alongside another active run (shares the processors)"},
    "script": {"type": "string", "description": "a script run, with case: the text of run.py, Python that runs OpenMC itself (a "
                                                "criticality search with openmc.search_for_keff, a depletion with an integrator, a loop "
                                                "over openmc.lib). It runs in a new run folder holding a copy of the case's files "
                                                "(model.xml, build.py and what build.py wrote), so it can import build.py"},
    "timeout_s": {"type": "integer", "minimum": 1, "maximum": 86400, "description": "a script run's time limit (default 3600 s)"},
}}


@as_tool
def run_start(args):
    """Ask to start a run. Never starts it: returns the plan for the user to approve."""
    a = _check(args, START_SCHEMA)
    plan = runs.plan_run(case=a.get("case"), cases=a.get("cases"), sweep=a.get("sweep"), threads=a.get("threads"),
                         concurrent=bool(a.get("concurrent", False)), script=a.get("script"), timeout_s=a.get("timeout_s"))
    return {"started": False, "needs_approval": True, "plan": plan,
            "message": "Nothing was started. Show this plan to the user; only the user can approve it (in the chat when asked, or with "
                       "python -m automc run start). Do not call this tool again to get around the question."}


CHECK_SCHEMA = {"type": "object", "additionalProperties": False, "required": [], "properties": {
    **_JOB, "case": {"type": "string", "description": "the newest job of this case, when no job id is given"}}}


@as_tool
def run_check(args):
    """Status, progress, latest k-eff and the last events of a job. Returns at once."""
    a = _check(args, CHECK_SCHEMA)
    return {"job": runs.check(job_id=a.get("job_id"), case=a.get("case"))}


JOB_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["job_id"], "properties": dict(_JOB)}


@as_tool
def run_pause(args):
    a = _check(args, JOB_SCHEMA)
    return {"job": runs.control(a["job_id"], "pause")}


RESUME_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["job_id"], "properties": {
    **_JOB, "threads": {"type": "integer", "minimum": 1, "maximum": 1024, "description": "threads when it continues from a statepoint"}}}


@as_tool
def run_resume(args):
    """A paused job resumes at once. A stopped, interrupted or cancelled job can only continue from its statepoint with the user's
    approval: then this returns the plan and starts nothing."""
    a = _check(args, RESUME_SCHEMA)
    job = runs.check(job_id=a["job_id"])
    if job["status"] == "paused":
        return {"job": runs.control(a["job_id"], "resume")}
    if job["status"] in ("stopped", "interrupted", "cancelled"):
        return {"started": False, "needs_approval": True, "plan": runs.plan_restart(a["job_id"], threads=a.get("threads")),
                "message": "Nothing was started. Continuing from the statepoint adds compute, so the user must approve this plan."}
    return {"job": runs.control(a["job_id"], "resume")}                 # raises the explanation for other statuses


@as_tool
def run_stop(args):
    a = _check(args, JOB_SCHEMA)
    return {"job": runs.control(a["job_id"], "stop")}


@as_tool
def run_cancel(args):
    a = _check(args, JOB_SCHEMA)
    return {"job": runs.cancel(a["job_id"])}


LIST_SCHEMA = {"type": "object", "additionalProperties": False, "required": [], "properties": {
    "active_only": {"type": "boolean", "description": "only queued, running and paused jobs"}}}


@as_tool
def job_list(args):
    a = _check(args, LIST_SCHEMA)
    return {"jobs": [runs.summarize(j) for j in runs.list_jobs(active_only=bool(a.get("active_only", False)))]}


TOOLS = [
    {"name": "RunResultReaderTool", "handler": run_result_reader, "parameters": RUN_RESULT_SCHEMA,
     "description": "Summarize a finished OpenMC run in the workspace: k-eff with its statistical error, k by batch, run settings, "
                    "timing and speed, every tally's bin count and whether its values are finite, lost particles, warnings and errors "
                    "from the log, and the case it was built from. Reads only; never starts or changes a run. Always report k-eff with "
                    "its error."},
    {"name": "OpenMCRunStartTool", "handler": run_start, "parameters": START_SCHEMA,
     "description": "Ask to run OpenMC on a case, several cases or a sweep. It NEVER starts a run: it returns the plan (histories, threads, "
                    "an estimate of the time, the run folders, which facility data the models use, what else is running) and "
                    "needs_approval. Only the user can approve a start; the chat asks them. Use it whenever the user asks for a run. "
                    "With case and script it plans a script run instead: Python that runs transport itself (a criticality search, a "
                    "depletion, openmc.lib), which a build script may not do; it can be stopped or cancelled, not paused."},
    {"name": "OpenMCRunCheckTool", "handler": run_check, "parameters": CHECK_SCHEMA,
     "description": "Check a job: status (running, paused, stopped, success, failed, cancelled, interrupted), batches done, the latest "
                    "k-eff with its error, speed, time remaining and the last events. Returns at once; never waits for the run."},
    {"name": "OpenMCRunPauseTool", "handler": run_pause, "parameters": JOB_SCHEMA,
     "description": "Pause a running job at the end of its current batch; it writes a statepoint and waits, using no processor time. "
                    "Resume continues it at once."},
    {"name": "OpenMCRunResumeTool", "handler": run_resume, "parameters": RESUME_SCHEMA,
     "description": "Resume a paused job at once. For a stopped, interrupted or cancelled job it returns a plan to continue from its "
                    "last statepoint, which needs the user's approval; it starts nothing itself."},
    {"name": "OpenMCRunStopTool", "handler": run_stop, "parameters": JOB_SCHEMA,
     "description": "Stop a job gracefully at the end of its current batch: it writes a statepoint and finishes, so the batches done so "
                    "far give a result and the run can continue later with the user's approval."},
    {"name": "OpenMCRunCancelTool", "handler": run_cancel, "parameters": JOB_SCHEMA,
     "description": "Kill a job at once (its whole process group). Statepoints already written stay; nothing is deleted. Prefer stop, "
                    "which keeps the current batch."},
    {"name": "OpenMCJobListTool", "handler": job_list, "parameters": LIST_SCHEMA,
     "description": "List the jobs in the registry with their status, case, progress and latest k-eff. Returns at once."},
]
