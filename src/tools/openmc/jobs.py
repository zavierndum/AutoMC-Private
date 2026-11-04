"""
The job registry: one JSON file, <workspace>/.automc/jobs/registry.json, the source of truth about runs (DESIGN.md section 6). Ported
from AutoSAM's sam_job_registry (create, update, read, list, the done sentinel, startup reconciliation), with AutoMC's fields and
statuses. The registry functions take the folder that holds registry.json and registry.lock: home(workspace) for runs, a knowledge
base's documents folder for parses (tools/documents/parse.py).

Only the job service (runs.py) writes the registry, under a file lock, so the command line, the agent and a UI can work at the same time.
Workers and sequence supervisors write only their own files (status.json, events.jsonl and the run.log sentinel), and the service settles
each job from those files when it is checked.

Standard library; psutil, when installed, for the process check (with a /proc fallback on Linux).
"""
from __future__ import annotations

import atexit
import contextlib
import datetime as _dt
import json
import os
import re
import uuid
from pathlib import Path

import automc_paths

REGISTRY_NAME = "registry.json"
LOCK_NAME = "registry.lock"
JOB_FOLDERS = f"{automc_paths.STATE}/jobs"         # <workspace>/.automc/jobs/: the registry, and <job_id>/ for sequence jobs
SENTINEL = "AUTOMC_JOB_DONE"
# Anchored to a whole line, so a sentinel-looking substring in OpenMC's own output can never count as completion.
_SENTINEL_RE = re.compile(r"^AUTOMC_JOB_DONE rc=(-?\d+) status=([a-z_]+) segment=(\d+)\s*$", re.MULTILINE)

STATUSES = ("queued", "running", "paused", "stopped", "success", "failed", "non_converged", "cancelled", "interrupted")
ACTIVE = frozenset({"queued", "running", "paused"})
TERMINAL = frozenset(set(STATUSES) - ACTIVE)
RUN_RE = re.compile(r"^run-(\d+)$")
CHILDREN = {}                        # pid -> Popen of the processes this process started, polled so they are reaped cleanly


def reap_children():
    """Poll every child this process started: a finished one is reaped (a run that is meant to outlive us is only polled)."""
    for proc in list(CHILDREN.values()):
        proc.poll()


atexit.register(reap_children)


def now():
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def run_name(n):
    return f"run-{n:03d}"


def run_folders(case_folder):
    """The run-NNN folders of a case, sorted by number."""
    out = []
    for p in Path(case_folder).iterdir() if Path(case_folder).is_dir() else ():
        m = RUN_RE.match(p.name)
        if m and p.is_dir():
            out.append((int(m.group(1)), p))
    return [p for _, p in sorted(out)]


def next_run_name(case_folder):
    existing = [int(RUN_RE.match(p.name).group(1)) for p in run_folders(case_folder)]
    return run_name(max(existing, default=0) + 1)


# ----------------------------------------------------------------------------------------------------------------------
# files
# ----------------------------------------------------------------------------------------------------------------------
def atomic_write_json(path, data):
    path = Path(path)
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def read_json(path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default


def read_events(path, last=None):
    """The events of a run (events.jsonl), oldest first; `last` keeps only the newest ones. A partly written last line is skipped."""
    out = []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except (FileNotFoundError, OSError):
        return []
    return out[-last:] if last else out


def read_sentinel(log_path):
    """(rc, status, segment) of the last AUTOMC_JOB_DONE line near the end of a log, or None."""
    try:
        with open(log_path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - 8192))
            tail = fh.read().decode("utf-8", errors="replace")
    except (FileNotFoundError, OSError):
        return None
    found = _SENTINEL_RE.findall(tail)
    if not found:
        return None
    rc, status, segment = found[-1]
    return int(rc), status, int(segment)


def sentinel_line(rc, status, segment):
    return f"{SENTINEL} rc={rc} status={status} segment={segment}"


# ----------------------------------------------------------------------------------------------------------------------
# processes
# ----------------------------------------------------------------------------------------------------------------------
def process_started_at(pid):
    """Start time of a process (seconds since the epoch, or clock ticks from /proc), to tell a reused process id apart."""
    try:
        import psutil
        return psutil.Process(pid).create_time()
    except ImportError:
        pass
    except Exception:
        return None
    try:
        with open(f"/proc/{pid}/stat", "r") as fh:
            return float(fh.read().rsplit(")", 1)[1].split()[19])
    except (OSError, IndexError, ValueError):
        return None


def process_alive(pid, started_at=None):
    """True if the process exists, is not a zombie, and (when known) started when the registry says it did."""
    if not pid:
        return False
    if pid in CHILDREN:                                    # our own child: Popen is the authority, and polling reaps it
        return CHILDREN[pid].poll() is None
    try:                                                   # reap other finished children, which would otherwise stay zombies
        if os.name == "posix":
            done, _ = os.waitpid(pid, os.WNOHANG)
            if done == pid:
                return False
    except ChildProcessError:
        pass
    except OSError:
        pass
    try:
        import psutil
        try:
            p = psutil.Process(pid)
            if not p.is_running() or p.status() == psutil.STATUS_ZOMBIE:
                return False
            return started_at is None or abs(p.create_time() - float(started_at)) < 2.0
        except psutil.Error:
            return False
    except ImportError:
        pass
    if not Path(f"/proc/{pid}").exists():
        return False
    try:
        with open(f"/proc/{pid}/stat", "r") as fh:
            fields = fh.read().rsplit(")", 1)[1].split()
        if fields[0] == "Z":
            return False
        return started_at is None or abs(float(fields[19]) - float(started_at)) < 1.0
    except (OSError, IndexError, ValueError):
        return False


# ----------------------------------------------------------------------------------------------------------------------
# the registry
# ----------------------------------------------------------------------------------------------------------------------
@contextlib.contextmanager
def _locked(workspace, name=LOCK_NAME):
    """An exclusive lock on the registry (or, with `name`, another file of that folder) across processes (fcntl on Linux, msvcrt on
    Windows)."""
    workspace = Path(workspace)
    workspace.mkdir(parents=True, exist_ok=True)
    fh = open(workspace / name, "a+b")
    try:
        try:
            import fcntl
            fcntl.flock(fh, fcntl.LOCK_EX)
        except ImportError:
            import msvcrt
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_LOCK, 1)
        yield
    finally:
        try:
            try:
                import fcntl
                fcntl.flock(fh, fcntl.LOCK_UN)
            except ImportError:
                import msvcrt
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        finally:
            fh.close()


def home(workspace):
    """The folder of a workspace's run registry: <workspace>/.automc/jobs."""
    return Path(workspace) / JOB_FOLDERS


def registry_path(workspace):
    return Path(workspace) / REGISTRY_NAME


def _load(workspace):
    data = read_json(registry_path(workspace), [])
    return data if isinstance(data, list) else []


def create_job(workspace, **fields):
    """Add a job; returns it. `status` must be one of STATUSES."""
    job = {"job_id": fields.pop("job_id", None) or uuid.uuid4().hex[:12], "created_at": now(), "updated_at": now(),
           "completed_at": None, "cancel_requested": False, "error": None, **fields}
    if job.get("status") not in STATUSES:
        raise ValueError(f"unknown status {job.get('status')!r}")
    with _locked(workspace):
        jobs = _load(workspace)
        if any(j.get("job_id") == job["job_id"] for j in jobs):
            raise ValueError(f"job {job['job_id']} exists")
        jobs.append(job)
        atomic_write_json(registry_path(workspace), jobs)
    return dict(job)


def update_job(workspace, job_id, **fields):
    """Merge fields into a job; sets completed_at when it reaches a terminal status. Returns the job, or None if unknown."""
    if "status" in fields and fields["status"] not in STATUSES:
        raise ValueError(f"unknown status {fields['status']!r}")
    with _locked(workspace):
        jobs = _load(workspace)
        target = next((j for j in jobs if j.get("job_id") == job_id), None)
        if target is None:
            return None
        target.update(fields)
        target["updated_at"] = now()
        if target.get("status") in TERMINAL and not target.get("completed_at"):
            target["completed_at"] = target["updated_at"]
        if target.get("status") in ACTIVE:
            target["completed_at"] = None
        atomic_write_json(registry_path(workspace), jobs)
        return dict(target)


def get_job(workspace, job_id):
    return next((dict(j) for j in _load(workspace) if j.get("job_id") == job_id), None)


def list_jobs(workspace, status=None, active_only=False):
    jobs = _load(workspace)
    if status is not None:
        jobs = [j for j in jobs if j.get("status") == status]
    if active_only:
        jobs = [j for j in jobs if j.get("status") in ACTIVE]
    return [dict(j) for j in jobs]


def find_job(workspace, job_id=None, case=None):
    """A job by id; else the newest job of a case (an active one first); else the newest job overall."""
    if job_id:
        return get_job(workspace, job_id)
    jobs = _load(workspace)
    if case:
        jobs = [j for j in jobs if j.get("case") == case or case in (j.get("cases") or [])]
    if not jobs:
        return None
    active = [j for j in jobs if j.get("status") in ACTIVE]
    return dict((active or jobs)[-1])
