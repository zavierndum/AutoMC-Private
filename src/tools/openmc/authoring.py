"""
Any OpenMC model from a Python build script (devops/AutoMC_Authoring_Plan.md, AU-1): the agent writes `build.py` with the OpenMC Python
API, the way OpenMC is meant to be used; this module builds, plots and checks it. Transport stays with the job service (runs.py) and the
user's approval.

    plan_build(case, script, overwrite, timeout_s)   the question for the user (the script shown); starts nothing
    start_build(plan, approved_by)                    runs the script in its own process, makes the case folder
    plot_case(case, views, pixels, color_by)          geometry slices as PNGs, overlaps marked (OpenMC's plot mode: no transport)
    check_case(case)                                  the model read back and checked before any run (no transport)

A build:
  - refuses a script that would run transport or other programs (`.run(`, `run_in_memory`, `search_for_keff`, `.integrate(`,
    `openmc.lib` stepping, `subprocess`, `os.system`): transport is started by OpenMCRunStartTool after the user's approval. The child
    process also replaces those functions with ones that refuse, in case a script reaches them another way;
  - runs `build.py` in a staging folder beside the case (`python -I`: no AutoMC modules, no user site), with a time limit and without
    keys (the Python tool's environment), then moves the folder into place, so a failed build leaves nothing behind;
  - writes `model.xml` (from separate XML files when the script wrote those) and `result.json`: the script's SHA-256, the files, the
    versions, the approval and the facility label, so the job service, the file tools and the data guard treat the case as they treat
    a TRIGA case (runs._label, places.label);
  - never replaces a case that has runs.

The label (decision A5): the conversation's label so far, and the label of every existing path the script names; the most restricted
wins, public when there is nothing else.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import automc_paths
import console

from . import jobs, runs
from .errors import OpenMCError

SCRIPT = "build.py"
LOG = "build.log"
RESULT = "result.json"
RESULT_VERSION = 1
DEFAULT_TIMEOUT, MAX_TIMEOUT = 300, 1800
MAX_SCRIPT = 200_000
CHILD = Path(__file__).with_name("_author_child.py")
LABEL_ORDER = {"public": 0, "unknown": 1, "restricted": 2}
# What a build script may not do, with why (checked on the text; the child also disarms the functions themselves)
FORBIDDEN = [
    (re.compile(r"\.\s*run\s*\("), "it calls .run(), which starts transport (or another program)"),
    (re.compile(r"\brun_in_memory\b"), "it uses openmc.lib.run_in_memory, which runs transport"),
    (re.compile(r"\bsearch_for_keff\b"), "it calls search_for_keff, which runs transport"),
    (re.compile(r"\.\s*integrate\s*\("), "it calls .integrate(), which runs a depletion (transport at every step)"),
    (re.compile(r"\b(simulation_init|next_batch|iter_batches)\b"), "it steps openmc.lib through batches, which runs transport"),
    (re.compile(r"\bsubprocess\b|\bos\s*\.\s*(system|popen|exec\w*|spawn\w*)\b"), "it starts other programs"),
]
_PATHS = re.compile(r"""['"]((?:/|~|[A-Za-z]:[\\/]|\.\.?/)[^'"\n]{1,300})['"]""")


def _now():
    return jobs.now()


def _sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _case_target(case):
    if not isinstance(case, str) or case.startswith(("/", "\\")) or "\\" in case or not runs._case_name(case):
        raise OpenMCError("case", f"must be a case name in the workspace, up to four folders deep (study-8/sphere), got {case!r}")
    return Path(automc_paths.WORKSPACE).resolve() / Path(*case.strip("/").split("/"))


def _label(script):
    """(label, why): the conversation's label so far and the labels of the existing paths the script names."""
    from tools import conversation
    from tools.files import places
    found = [((conversation.current() or {}).get("label") or "public", "the conversation")]
    for raw in _PATHS.findall(script):
        try:
            p = places.resolve(raw)
        except Exception:
            continue
        if p.exists():
            lab = places.label(p)[0]
            found.append((lab or "unknown", f"the script reads {raw}"))
    label, why = max(found, key=lambda x: LABEL_ORDER.get(x[0], 1))
    return label, why


# ----------------------------------------------------------------------------------------------------------------------
# building
# ----------------------------------------------------------------------------------------------------------------------
def plan_build(case, script, overwrite=False, timeout_s=None):
    target = _case_target(case)
    if not isinstance(script, str) or not script.strip():
        raise OpenMCError("script", "must be the text of build.py (the OpenMC Python API, ending with model.export_to_model_xml())")
    if len(script) > MAX_SCRIPT:
        raise OpenMCError("script", f"is {len(script):,} characters; at most {MAX_SCRIPT:,}")
    timeout = DEFAULT_TIMEOUT if timeout_s is None else timeout_s
    if not isinstance(timeout, int) or isinstance(timeout, bool) or not 1 <= timeout <= MAX_TIMEOUT:
        raise OpenMCError("timeout_s", f"must be a whole number of seconds from 1 to {MAX_TIMEOUT}")
    refusals = [f"line {script[:m.start()].count(chr(10)) + 1}: {why}" for rx, why in FORBIDDEN for m in rx.finditer(script)]
    if refusals:
        raise OpenMCError("script", "refused: " + "; ".join(refusals[:6]) + ". A build script only writes the model; transport is "
                          "started by OpenMCRunStartTool after the user's approval. Python that runs transport itself (a search, a "
                          "depletion, openmc.lib) goes to OpenMCRunStartTool as a script run (case and script) after this build",
                          kind="NotAllowed", exit_code=3)
    if target.exists():
        if jobs.run_folders(target):
            raise OpenMCError("case", f"{case} already has runs; a case with runs is never rebuilt. Choose another name",
                              kind="NotAllowed", exit_code=3)
        if not overwrite:
            raise OpenMCError("case", f"{case} already exists; set overwrite true to replace it (it has no runs)", kind="NotAllowed",
                              exit_code=3)
    label, why = _label(script)
    plan = {"kind": "model_build", "case": case.strip("/"), "script": script, "timeout_s": timeout, "overwrite": bool(overwrite),
            "replaces": target.exists(), "label": label, "label_reason": why,
            "facility_data": "public"}         # the plan holds the model's script and goes to the user's question, not to the model
    plan["plan_id"] = _sha(json.dumps([plan["case"], script, timeout, bool(overwrite)]))[:16]
    plan["text"] = describe(plan)
    return plan


def describe(plan):
    lines = plan["script"].rstrip("\n").split("\n")
    width = len(str(len(lines)))
    code = "\n".join(f"  {i:>{width}} | {line}" for i, line in enumerate(lines, 1))
    return "\n".join([
        console.banner("Build an OpenMC model from a Python script"),
        console.fields([("Case", f"workspace/{plan['case']}/" + (" (replaces the one there, which has no runs)" if plan["replaces"] else "")),
                        ("Runs", f"build.py in its own process, at most {plan['timeout_s']} s, no keys; it writes model.xml and "
                                 f"never runs transport"),
                        ("Label", f"{plan['label']} ({plan['label_reason']})"),
                        ("Note", "not a sandbox: the script can do what you can, so read it before you say yes")]),
        "", f"  build.py ({len(lines)} line(s)):", code])


def _clean_env():
    from tools.python import run as python_run
    env = python_run._clean_env()
    if os.environ.get("OPENMC_CROSS_SECTIONS"):
        env["OPENMC_CROSS_SECTIONS"] = os.environ["OPENMC_CROSS_SECTIONS"]
    return env


def _child(task, folder, timeout, **args):
    """Run the child (`_author_child.py`, isolated) in `folder`; returns (report dict, the log text)."""
    cmd = [sys.executable, "-I", str(CHILD), task, json.dumps(args)]
    try:
        p = subprocess.run(cmd, cwd=str(folder), env=_clean_env(), capture_output=True, text=True, timeout=timeout,
                           stdin=subprocess.DEVNULL)
        out, code = p.stdout, p.returncode
        log = (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired as e:
        log = ((e.stdout or b"").decode(errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or "")) + \
              ((e.stderr or b"").decode(errors="replace") if isinstance(e.stderr, bytes) else (e.stderr or ""))
        return {"ok": False, "error": f"stopped after {timeout} s"}, log
    marker = "AUTOMC_REPORT "
    lines = [l for l in (out or "").splitlines() if l.startswith(marker)]
    if not lines:
        return {"ok": False, "error": f"the process ended (code {code}) without a report"}, log
    return json.loads(lines[-1][len(marker):]), log


def _tail(text, n=40):
    return "\n".join((text or "").rstrip().splitlines()[-n:])


def _unstage(stage):
    """Make paths into the staging folder that model.xml still names (a script that set openmc.config['resolve_paths'] itself)
    relative to the case: the staging folder is gone once the case is moved in. Returns how many were rewritten."""
    path = stage / "model.xml"
    text = path.read_text(encoding="utf-8")
    n = 0
    for form in sorted({str(stage), str(stage.resolve()), stage.as_posix(), stage.resolve().as_posix()}, key=len, reverse=True):
        n += text.count(form + "/") + text.count(form + "<")
        text = text.replace(form + "/", "").replace(form + "<", ".<")
    if n:
        path.write_text(text, encoding="utf-8")
    return n


def start_build(plan, approved_by, send=False):
    """Build an approved plan: the script runs in a staging folder, which becomes the case only if model.xml came out of it."""
    if not isinstance(approved_by, str) or not approved_by.strip():
        raise OpenMCError("approved_by", "a model is built only with the user's approval, given through a surface", kind="NeedsApproval",
                          exit_code=3)
    if plan.get("kind") != "model_build":
        raise OpenMCError("plan", "not a model-build plan")
    fresh = plan_build(plan["case"], plan["script"], plan["overwrite"], plan["timeout_s"])
    if fresh["plan_id"] != plan.get("plan_id"):
        raise OpenMCError("plan", "the script or the case changed since the user saw it; make a new plan and ask again", kind="NotAllowed",
                          exit_code=3)
    target = _case_target(plan["case"])
    target.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{target.name}.build-", dir=target.parent))
    t0 = time.monotonic()
    try:
        (stage / SCRIPT).write_text(plan["script"], encoding="utf-8")
        report, log = _child("build", stage, plan["timeout_s"], script=SCRIPT)
        (stage / LOG).write_text(log, encoding="utf-8")
        seconds = round(time.monotonic() - t0, 1)
        if not report.get("ok") or not (stage / "model.xml").is_file():
            why = report.get("error") or "the script did not write model.xml (end it with model.export_to_model_xml())"
            return {"case": plan["case"], "built": False, "error": why, "seconds": seconds, "log_tail": _tail(log),
                    "facility_data": {"distribution": fresh["label"], "reason": fresh["label_reason"]},
                    "next": "read the error, fix build.py, and ask to build again (the user approves each build)"}
        relative = _unstage(stage)
        files = sorted(p for p in stage.rglob("*") if p.is_file() and p.name != RESULT)
        record = {"result_version": RESULT_VERSION, "builder": "script", "name": plan["case"], "built_at": _now(),
                  "facility_data": {"distribution": fresh["label"], "reason": fresh["label_reason"]},
                  "script_sha256": _sha(plan["script"]), "approved_by": approved_by, "seconds": seconds,
                  "versions": {"automc": _automc_version(), "openmc": report.get("openmc_version")},
                  "files": [{"path": p.relative_to(stage).as_posix(), "bytes": p.stat().st_size,
                             "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in files],
                  "model": report.get("model")}
        if relative:
            record["paths_made_relative"] = relative
        jobs.atomic_write_json(stage / RESULT, record)
        old = None
        if target.exists():
            if jobs.run_folders(target):
                raise OpenMCError("case", f"{plan['case']} got runs while it was being built; the new build is not moved in",
                                  kind="NotAllowed", exit_code=3)
            old = target.with_name(f".{target.name}.old-{os.getpid()}")
            target.replace(old)
        stage.replace(target)
        if old is not None:
            shutil.rmtree(old, ignore_errors=True)
        out = {"case": plan["case"], "built": True, "folder": plan["case"], "seconds": seconds, "files": [f["path"] for f in record["files"]],
               "model": record["model"], "facility_data": record["facility_data"],
               "next": "plot it with OpenMCGeometryPlotTool for the user to inspect, check it with OpenMCModelCheckTool, then plan "
                       "the run with OpenMCRunStartTool (the user approves)"}
        if relative:
            out["note"] = (f"model.xml named {relative} file path(s) inside the build folder; they now name the case's own files "
                           "(relative paths)")
        return out
    finally:
        if stage.exists():
            shutil.rmtree(stage, ignore_errors=True)


def _automc_version():
    try:
        import automc_version
        return automc_version.VERSION
    except Exception:
        return None


# ----------------------------------------------------------------------------------------------------------------------
# plotting and checking (no transport, no user code: no approval)
# ----------------------------------------------------------------------------------------------------------------------
def _case_label(folder):
    from tools.files import places
    return places.label(folder)[0] or "unknown"


def plot_case(case, views=None, pixels=800, color_by="material"):
    folder = runs.case_folder(case)
    if color_by not in ("material", "cell"):
        raise OpenMCError("color_by", "must be material or cell")
    if not isinstance(pixels, int) or isinstance(pixels, bool) or not 100 <= pixels <= 3000:
        raise OpenMCError("pixels", "must be a whole number from 100 to 3000")
    for v in views or []:
        if not isinstance(v, dict) or set(v) - {"basis", "origin", "width"} or v.get("basis", "xy") not in ("xy", "xz", "yz"):
            raise OpenMCError("views", "each view is {basis: xy | xz | yz, origin: [x, y, z], width: [w, h]} (cm)")
    out = folder / "plots"
    out.mkdir(exist_ok=True)
    report, log = _child("plot", folder, 300, views=views or [], pixels=pixels, color_by=color_by, out=str(out))
    if not report.get("ok"):
        raise OpenMCError("case", f"could not plot {case}: {report.get('error')}; {_tail(log, 6)}", kind="PlotFailed")
    rel = lambda p: Path(p).relative_to(Path(automc_paths.WORKSPACE).resolve()).as_posix()
    return {"case": case, "plots": [{**p, "path": rel(p["path"])} for p in report["plots"]],
            "overlaps_seen": report.get("overlaps_seen", 0), "facility_data": _case_label(folder),
            "next": "tell the user where the pictures are so they can inspect them; overlaps, if any, are drawn red. A slice lying "
                    "exactly on a surface of the model can show red on that surface without any overlap: OpenMCModelCheckTool "
                    "tells the two apart"}


def _file_findings(folder):
    """The CAD geometries and meshes model.xml names: missing, out of the case ('..'), or absolute (the child checks the library)."""
    fix = "copy the file into the case in build.py and name it by a relative path"
    out = []
    for f in runs.named_files(folder):
        if f["kind"] == "library":
            continue
        if not f["path"].is_file():
            out.append({"severity": "error", "check": "files", "message": f"model.xml names the {f['kind']} {f['text']}, which does "
                        "not exist", "fix": fix})
        elif not f["inside"] and not Path(f["text"]).is_absolute():
            out.append({"severity": "error", "check": "files", "message": f"the {f['kind']} {f['text']} is named by a path out of "
                        "the case folder ('..'); a run folder, one level down, would look elsewhere", "fix": fix})
        elif not f["inside"]:
            out.append({"severity": "warning", "check": "files", "message": f"the {f['kind']} {f['text']} is named by an absolute "
                        "path: runs on this machine find it, but the case does not carry it to another machine or a container",
                        "fix": fix})
    return out


def check_case(case):
    folder = runs.case_folder(case)
    files = _file_findings(folder)
    if any(f["severity"] == "error" for f in files):     # OpenMC opens a CAD file while loading the model: say what is missing first
        report = {"findings": [{"severity": "info", "check": "model", "message": "the rest of the model was not checked: it cannot "
                                "be loaded without the files above", "fix": None}]}
    else:
        report, log = _child("check", folder, 300)
        if not report.get("ok") and not report.get("findings"):
            raise OpenMCError("case", f"could not check {case}: {report.get('error')}; {_tail(log, 6)}", kind="CheckFailed")
    findings = files + report.get("findings", [])
    errors = [f for f in findings if f["severity"] == "error"]
    return {"case": case, "passed": not errors, "findings": findings, "summary": report.get("summary"),
            "facility_data": _case_label(folder),
            "next": ("fix the errors in build.py and build again" if errors else
                     "the model may be run: plan it with OpenMCRunStartTool (the user approves)")}
