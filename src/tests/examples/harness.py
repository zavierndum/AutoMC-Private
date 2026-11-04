"""
Building, running and checking the working examples (src/skills/working_examples, AU-2) the way the agent does: the example's build.py
goes through the build tool (its OPTIONS line set to the variant), the model is checked, a transport example runs through the job
service, and post.py turns the run into results, which are compared with the example's expected.json. A variant with "script" (for
example "run.py") runs that file as a script run (decision A13): it runs in a copy of the case, so it imports build.py and reads the
variant's OPTIONS from there. An analysis example has `analysis.py` instead of build.py (nuclear data read and processed, no model):
the agent runs it with PythonRunTool, and here it runs as Python in a folder of its own, printing its RESULTS itself.

expected.json:
    {"example": NAME, "derived_from": "...", "variants": [{"name": ..., "options": {...}, "transport": true|false,
                                                           "script": "run.py" (optional), "env": {NAME: VALUE} (optional;
                                                           "@example/" in a value is the example's folder), "check_errors":
                                                           ["overlaps", ...] (optional: the model check's errors a variant that
                                                           shows a flawed model must give), "checks": [CHECK, ...]}]}
    CHECK: {"result": KEY, ...} with one of
        "equals": V                         the result is exactly V
        "expect": V, "n_sigma": N           |result - V| <= N * sqrt(sigma_result^2 + sigma^2); sigma (the reference's) default 0,
              ["sigma": S]                   sigma_result is the result KEY + "_sigma"
        "expect": V, "rel": R               |result - V| <= R * |V|
        "min": A and/or "max": B            A <= result <= B
"""
from __future__ import annotations

import json
import math
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import automc_paths

EXAMPLES = automc_paths.SRC / "skills" / "working_examples"
_OPTIONS = re.compile(r"^OPTIONS = \{.*\}$", re.MULTILINE)


def names():
    return sorted(p.name for p in EXAMPLES.iterdir() if (p / "build.py").is_file() or (p / "analysis.py").is_file()) \
        if EXAMPLES.is_dir() else []


def is_analysis(name):
    """An analysis example: analysis.py, run with PythonRunTool, instead of a build.py."""
    return (EXAMPLES / name / "analysis.py").is_file() and not (EXAMPLES / name / "build.py").is_file()


def expected(name):
    return json.loads((EXAMPLES / name / "expected.json").read_text(encoding="utf-8"))


def script_for(name, options):
    """The example's build.py (analysis.py for an analysis example) with its OPTIONS line set to `options` (what the agent does to
    choose a variant)."""
    file = "analysis.py" if is_analysis(name) else "build.py"
    text = (EXAMPLES / name / file).read_text(encoding="utf-8")
    if not _OPTIONS.search(text):
        raise AssertionError(f"{name}/{file} has no OPTIONS = {{...}} line")
    return _OPTIONS.sub(lambda m: "OPTIONS = " + json.dumps(options), text, count=1)


def analyse(name, options, folder):
    """Run an analysis example in `folder` (as PythonRunTool would, in the conversation's folder); returns (RESULTS, its output)."""
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "analysis.py").write_text(script_for(name, options), encoding="utf-8")
    p = subprocess.run([sys.executable, "-I", "analysis.py"], capture_output=True, text=True, timeout=1800,
                       env={**os.environ, "MPLBACKEND": "Agg"}, cwd=str(folder))
    lines = [l for l in p.stdout.splitlines() if l.startswith("RESULTS ")]
    if p.returncode != 0 or not lines:
        raise AssertionError(f"{name}/analysis.py failed ({p.returncode}): {(p.stdout + p.stderr)[-1500:]}")
    return json.loads(lines[-1][len("RESULTS "):]), p.stdout


def build(name, case, options):
    from tools import conversation
    from tools.openmc import authoring
    with conversation.inside(None, "public", f"examples-{name}", None):
        plan = authoring.plan_build(case, script_for(name, options), overwrite=True, timeout_s=900)
        return authoring.start_build(plan, approved_by="examples test")


def run(case, threads=4, timeout_s=3600, script=None):
    """Run a built case through the job service (as a script run when `script` is given) and wait; returns the job's summary."""
    from tools import conversation
    from tools.openmc import runs
    with conversation.inside(None, "public", "examples", None):
        plan = runs.plan_run(case=case, threads=threads, script=script, timeout_s=timeout_s if script else None)
    job = runs.start(plan, approved_by="examples test")
    t0 = time.time()
    while True:
        out = runs.check(job_id=job["job_id"])
        if out["status"] not in ("queued", "running", "paused"):
            return out
        if time.time() - t0 > timeout_s:
            runs.cancel(job["job_id"])
            raise TimeoutError(f"{case} did not finish in {timeout_s} s")
        time.sleep(1.0)


def post(name, folder, *extra):
    """Run the example's post.py on a run (or case) folder; returns its RESULTS dict."""
    p = subprocess.run([sys.executable, str(EXAMPLES / name / "post.py"), str(folder), *extra], capture_output=True, text=True,
                       timeout=900, env={**os.environ, "MPLBACKEND": "Agg"}, cwd=str(folder))
    lines = [l for l in p.stdout.splitlines() if l.startswith("RESULTS ")]
    if p.returncode != 0 or not lines:
        raise AssertionError(f"{name}/post.py failed ({p.returncode}): {p.stderr[-1500:]}")
    return json.loads(lines[-1][len("RESULTS "):]), p.stdout


def judge(results, checks):
    """[(check, passed, why)] for each check of a variant."""
    out = []
    for c in checks:
        key = c["result"]
        if key not in results:
            out.append((c, False, f"post.py gave no {key}"))
            continue
        v = results[key]
        if "equals" in c:
            ok, why = v == c["equals"], f"{v!r} vs {c['equals']!r}"
        elif "n_sigma" in c:
            s = math.hypot(results.get(key + "_sigma", 0.0) or 0.0, c.get("sigma", 0.0))
            ok = abs(v - c["expect"]) <= c["n_sigma"] * s if s > 0 else v == c["expect"]
            why = f"{v:.6g} vs {c['expect']:.6g} (within {c['n_sigma']} x {s:.3g}: {abs(v - c['expect']) / s if s else 0:.2f} sigma)"
        elif "rel" in c:
            ok = abs(v - c["expect"]) <= c["rel"] * abs(c["expect"])
            why = f"{v:.6g} vs {c['expect']:.6g} (relative {abs(v - c['expect']) / abs(c['expect']):.3g}, allowed {c['rel']})"
        else:
            ok = (c.get("min", -math.inf) <= v <= c.get("max", math.inf))
            why = f"{v:.6g} in [{c.get('min', '-inf')}, {c.get('max', 'inf')}]"
        out.append((c, bool(ok), why))
    return out


def run_variant(name, variant, threads=4):
    """Build, check, (run) and post-process one variant in the current workspace. Returns {build, check, job, results, verdicts}."""
    from tools.openmc import authoring
    case = f"{name}/{variant['name']}"
    if is_analysis(name):
        results, text = analyse(name, variant["options"], Path(automc_paths.WORKSPACE).resolve() / case)
        return {"check": {"findings": []}, "results": results, "post_text": text, "verdicts": judge(results, variant.get("checks", []))}
    built = build(name, case, variant["options"])
    if not built.get("built"):
        raise AssertionError(f"{case}: build failed: {built.get('error')}\n{built.get('log_tail')}")
    checked = authoring.check_case(case)
    out = {"build": built, "check": checked}
    folder = Path(automc_paths.WORKSPACE).resolve() / case
    if variant.get("transport", True):
        script = (EXAMPLES / name / variant["script"]).read_text(encoding="utf-8") if variant.get("script") else None
        env = {k: v.replace("@example/", str(EXAMPLES / name) + "/") for k, v in (variant.get("env") or {}).items()}
        saved = {k: os.environ.get(k) for k in env}
        os.environ.update(env)                           # the job's worker starts with this environment (for example a chain file)
        try:
            job = run(case, threads=threads, script=script)
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        out["job"] = job
        if job["status"] != "success":
            raise AssertionError(f"{case}: the run ended {job['status']}: {job.get('error')}")
        folder = Path(automc_paths.WORKSPACE).resolve() / job["run_folder"]
    results, text = post(name, folder, *variant.get("post_args", []))
    out.update(results=results, post_text=text, verdicts=judge(results, variant.get("checks", [])))
    return out
