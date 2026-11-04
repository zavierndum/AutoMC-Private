"""
build_input: the public entry point. Runs in the calling process with NO CoreForge or OpenMC import. It resolves and checks the
spec, picks the library profile, preflights the environment, then runs the writer in a fresh worker process (TRIGA_Input_Builder_Plan.md D1) with
a timeout (D11) and writes the output atomically (D10).

Overwrite policy: an identical existing build is reused; a different existing folder is refused; `force=True` rebuilds and replaces
the folder, and a failed forced rebuild leaves the old folder untouched.
"""
from __future__ import annotations

import datetime
import json
import os
import platform
import shutil
import signal
import subprocess
import tempfile
import time
from pathlib import Path

from . import __version__, advice, libraries
from .capabilities import check_supported
from .errors import BuildTimeout, InvalidSpec, MissingLibrary, TrigaError, UnsupportedFeature
from .presets import active_assumptions, facility_data
from .result import RESULT_VERSION, BuildResult
from .safety import confined_outdir
from .spec import SPEC_VERSION, resolve

DEFAULT_TIMEOUT_S = 600
_ERROR_TYPES = {"InvalidSpec": InvalidSpec, "UnsupportedFeature": UnsupportedFeature, "MissingLibrary": MissingLibrary,
                "BuildTimeout": BuildTimeout, "TrigaError": TrigaError}


def _raise_from_payload(err, log_tail):
    kind, field, message = err.get("type", "TrigaError"), err.get("field"), err.get("message", "")
    cls = _ERROR_TYPES.get(kind, TrigaError)
    if cls is InvalidSpec:
        raise InvalidSpec(field or "spec", message.split(": ", 1)[-1])
    if cls is UnsupportedFeature:
        raise UnsupportedFeature(field or "feature", message.split(": ", 1)[-1])
    raise cls(message + (f"\n--- end of build.log ---\n{log_tail}" if log_tail and cls is TrigaError else ""))


def _tail(path, lines=25):
    try:
        return "\n".join(Path(path).read_text(encoding="utf-8", errors="replace").splitlines()[-lines:])
    except OSError:
        return ""


def _file_entries(folder, names):
    out = []
    for n in names:
        p = Path(folder) / n
        out.append({"path": n, "bytes": p.stat().st_size, "sha256": libraries.sha256_file(p)})
    return out


def _reusable(outdir, spec, canonical_preset, profile_info, nuclear, fdata):
    """An existing build with the same spec hash, profile, preset, facility data, tool version and unchanged files, or None."""
    rp = Path(outdir) / "result.json"
    if not rp.is_file():
        return None
    try:
        old = json.loads(rp.read_text(encoding="utf-8"))
        same = (old["spec_hash"] == spec.spec_hash() and old["profile"] == profile_info and old["preset"] == canonical_preset
                and old["triga_version"] == __version__ and old["result_version"] == RESULT_VERSION
                and old["nuclear_data"]["sha256"] == nuclear["sha256"] and old.get("facility_data") == fdata)
        if same:
            for f in old["files"]:
                if libraries.sha256_file(Path(outdir) / f["path"]) != f["sha256"]:
                    return None
            return old
    except (OSError, KeyError, ValueError):
        return None
    return None


def _kill_group(proc):
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        pass
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        proc.wait()


def _log_header(spec, profile, nuclear, python, timeout, pythonpath, fdata):
    lines = [
        f"triga {__version__} build log",
        f"started      {datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds')}",
        f"case         {spec.name}",
        f"spec hash    {spec.spec_hash()}",
        f"preset       {spec.preset}",
        f"profile      {profile}",
        f"facility     {fdata['distribution']} data, packs {', '.join(p['pack'] for p in fdata['packs']) or 'none'}",
        f"python       {python}  (parent {platform.python_version()} on {platform.system()})",
        f"timeout      {timeout:g} s",
        f"cross sects  {nuclear['path']}  sha256 {nuclear['sha256'][:16]}...",
        "import path  " + "  ".join(p for p in pythonpath.split(os.pathsep)[:3]),
        "---- worker output ----",
    ]
    return ("\n".join(lines) + "\n").encode("utf-8")


def build_input(code="openmc", facility="netl", *, preset=None, spec=None, outdir=None, workspace=None, timeout_s=None,
                force=False, allow_outside=False, python=None, **fields):
    """
    Build transport-code input from a spec. Returns a BuildResult. See TRIGA_Input_Builder_Spec.md section 2.

        build_input("openmc", "netl", config=0, rods={"S1": {"value": 600, "unit": "console"}})

    `outdir` defaults to <workspace>/<name> and must stay inside the workspace unless `allow_outside` (trusted callers only).
    An identical existing build is reused; a different one is refused unless `force`, which rebuilds and replaces it
    (but never a folder that holds transport runs, run-*).
    `timeout_s` defaults to 600 (or TRIGA_BUILD_TIMEOUT).
    """
    s = resolve(preset=preset, spec=spec, facility=facility, **fields)
    check_supported(s, code)
    if os.name == "nt":                                              # after the spec checks, so those behave the same everywhere
        raise MissingLibrary("build_input must run in Linux or WSL (the worker needs OpenMC). Start it from the WSL automc environment.")
    profile = libraries.profile_for(s)
    profile_info = libraries.provenance(profile, s.facility, s.preset)   # raises MissingLibrary if the snapshots are not built
    fdata = facility_data(s)                                        # which pack items the build reads, with their labels
    nuclear = libraries.nuclear_data_info(s.nuclear_data.library)  # raises MissingLibrary if the cross sections are not usable
    target = confined_outdir(s.name, outdir, workspace, allow_outside)
    timeout = float(timeout_s if timeout_s is not None else os.environ.get("TRIGA_BUILD_TIMEOUT", DEFAULT_TIMEOUT_S))

    if force and target.exists() and any(target.glob("run-*")):
        raise InvalidSpec("outdir", f"{target} holds transport runs (run-*); a forced rebuild would delete them. Move or delete the "
                                    f"runs yourself first, or build under another name")
    existing = None if force else (_reusable(target, s, s.preset, profile_info, nuclear, fdata) if target.exists() else None)
    if existing is not None:
        existing["reused"] = True
        return BuildResult.from_dict(existing)
    if target.exists() and not force:
        raise InvalidSpec("outdir", f"{target} already exists with different contents; choose another name or pass force=True")

    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=f".{target.name}.tmp-", dir=target.parent))
    os.chmod(tmp, 0o755)                                              # mkdtemp makes it private; the result is a normal folder
    t0 = time.time()
    try:
        request, response = tmp / ".request.json", tmp / ".response.json"
        request.write_text(json.dumps({"code": code, "spec": s.to_dict(), "outdir": str(tmp), "profile": profile}), encoding="utf-8")
        env = dict(os.environ)
        env["PYTHONPATH"] = libraries.worker_pythonpath(profile, env.get("PYTHONPATH"), s.facility, s.preset)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env["PYTHONUNBUFFERED"] = "1"                                  # so a crash or a kill still leaves the log up to that point
        env["MPLBACKEND"] = "Agg"
        interpreter = libraries.python_executable(python)
        cmd = [interpreter, "-m", f"{__package__}._worker", "--request", str(request), "--response", str(response)]
        with open(tmp / "build.log", "wb") as log:
            log.write(_log_header(s, profile, nuclear, interpreter, timeout, env["PYTHONPATH"], fdata))
            log.flush()
            proc = subprocess.Popen(cmd, cwd=str(tmp), env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            try:
                rc = proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                _kill_group(proc)
                raise BuildTimeout(f"the build did not finish within {timeout:g} s; the worker and its children were killed") from None
        with open(tmp / "build.log", "ab") as log:
            log.write(f"---- worker exit code {rc} after {time.time() - t0:.1f} s ----\n".encode("utf-8"))
        if not response.is_file():
            raise TrigaError(f"the build worker exited with code {rc} without a result\n--- end of build.log ---\n{_tail(tmp / 'build.log')}")
        payload = json.loads(response.read_text(encoding="utf-8"))
        if not payload.get("ok"):
            _raise_from_payload(payload.get("error", {}), _tail(tmp / "build.log"))
        request.unlink(), response.unlink()

        summary = dict(payload["summary"])
        summary["run_estimate"] = advice.run_time_estimate(s)
        result = BuildResult(
            result_version=RESULT_VERSION, triga_version=__version__, spec_version=SPEC_VERSION,
            built_at=datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
            code=code, facility=s.facility, name=s.name, preset=s.preset, spec=s.to_dict(), spec_hash=s.spec_hash(),
            profile=profile_info, nuclear_data=nuclear, assumptions=active_assumptions(s),
            files=_file_entries(tmp, payload["files"] + ["build.log"]), summary=summary,
            warnings=advice.for_spec(s) + list(payload.get("warnings", [])), outdir=str(target), reused=False,
            build_seconds=round(time.time() - t0, 1), facility_data=fdata)
        (tmp / "result.json").write_text(result.to_json(), encoding="utf-8")

        old = None
        if target.exists():                                          # only reachable with force=True
            old = target.with_name(f".{target.name}.old-{os.getpid()}")
            os.replace(target, old)
        os.replace(tmp, target)
        if old is not None:
            shutil.rmtree(old, ignore_errors=True)
        return result
    finally:
        if tmp.exists():
            shutil.rmtree(tmp, ignore_errors=True)
