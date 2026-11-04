"""
Running Python for the agent (devops/AutoMC_General_Tools_Plan.md, GT-5): what AutoSAM's PythonREPLTool gives its agent, rebuilt for
AutoMC's rules. AutoSAM runs the model's code inside the agent's own process, without asking, without a time limit, with every key of
its .env in the environment. Here:

  - one Jupyter kernel per conversation (jupyter_client, ipykernel), so variables persist between runs, in its own folder,
    workspace/.automc/python/<conversation>/, started with a cleaned environment: no variable whose name looks like a key, a token, a
    secret or a password, no AUTOMC_ENV_FILE, matplotlib drawing to files (Agg);
  - every run is shown to the user and runs only after they say yes (decision T1); the plan warns when the code would start OpenMC
    transport (that belongs to the job service and its approval), other programs, or reach the network. It is not a sandbox: approved
    code can do what the user can, which is why it asks;
  - a time limit (default 120 s): the run is interrupted when it is reached; a kernel that does not stop is restarted (its variables
    are then lost, and the result says so). Ctrl-C in the chat interrupts the kernel too;
  - the output (revised 2026-11-01): the whole of it is saved in the run's file (runs/run-NNN.txt, the code beside it), and the model
    reads at most a share of its context window (about 10,000 characters for a 32k window), the start and the end, the cut marked;
  - a matplotlib figure left open is saved as a PNG (figure-NNN-N.png), and the files the run wrote are listed;
  - the output carries no facility label: the code can read anything, so a cloud model reads it only in open mode (nothing restricted
    declared), or when the user typed 'yes, send' while approving (then it counts as public). Otherwise the user still sees it in the
    chat (for_the_user) and the model is told it was shown to the user only (WITHHELD_NOTE).

    plan_run(code, timeout_s)            the question for the user; starts nothing
    start(plan, approved_by, send)       runs it in this conversation's kernel
    shutdown(thread=None)                stops one conversation's kernel, or all (also at exit)
"""
from __future__ import annotations

import atexit
import base64
import hashlib
import json
import os
import queue
import re
import shutil
import tempfile
import time
from pathlib import Path

import automc_paths
import console
from tools import conversation
from tools.files.places import Refused

FOLDER = f"{automc_paths.STATE}/python"
DEFAULT_TIMEOUT, MAX_TIMEOUT = 120, 1800
MAX_CODE = 100_000
# The share of the model's context window one run's output may take (in tokens), at 3.5 characters a token: about 10,000 characters for
# a 32k window, the old fixed limit; never under 4,000 or over 40,000. Without a window (the command line) 10,000.
WINDOW_SHARE, CHARS_PER_TOKEN, MIN_CHARS, MAX_CHARS, DEFAULT_CHARS = 0.09, 3.5, 4_000, 40_000, 10_000
HEAD_SHARE = 0.2                                     # of the shown output, from the start; the rest from the end
SECRETISH = re.compile(r"KEY|TOKEN|SECRET|PASSW|CREDENTIAL|AUTH", re.IGNORECASE)
DROPPED = ("AUTOMC_ENV_FILE", "OPENAI_BASE_URL", "OPENAI_API_BASE", "LANGSMITH_ENDPOINT", "LANGCHAIN_ENDPOINT")
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
WARNINGS = [
    (re.compile(r"\bopenmc\s*\.\s*(run|lib\s*\.\s*run|calculate_volumes|plot_geometry|search_for_keff)\b|\bopenmc\.lib\.init\b|"
                r"['\"]openmc['\"]\s*[\],]|\bopenmc\s+-s\b"),
     "the code would run OpenMC itself: a run started this way bypasses the job service (not tracked, paused or labelled, and not "
     "approved as a run). Transport belongs to OpenMCRunStartTool (a script that runs OpenMC itself: its script form)"),
    (re.compile(r"\bsubprocess\b|\bos\s*\.\s*(system|popen|exec\w*|spawn\w*)\b"), "the code starts other programs"),
    (re.compile(r"\b(requests|urllib|httpx|socket|http\.client|aiohttp|ftplib|smtplib)\b"),
     "the code may reach the network: what it sends leaves this machine"),
    (re.compile(r"\b(shutil\s*\.\s*rmtree|os\s*\.\s*(remove|unlink|rmdir)|\.unlink\s*\(|\.rmdir\s*\()"), "the code deletes files"),
    (re.compile(r"(?<![\w.])(input|getpass\s*\.\s*getpass)\s*\("),
     "the code asks for typed input (input()), which a run cannot give: it will fail. The values belong in the code"),
]
_KERNELS = {}                                        # conversation -> {"km", "kc", "folder"}
SHOWN_CHARS = 3_000                                  # of a withheld output, shown to the user in the chat (the start and the end)
# What a cloud model is told instead of an output it may not read (agent/guard.withheld); the user sees the output in the chat.
WITHHELD_NOTE = ("The user has seen this output in the chat; you have not. It is saved in {file}, which is withheld from you by the same "
                 "rule, so do not offer to read it. Tell the user the result was shown to them and not to you, and do not guess it. If "
                 "the code read only public data, the user can type 'yes, send' when approving the next run so that you can read its "
                 "output, or tell you the value.")


def _thread():
    c = conversation.current()
    return re.sub(r"[^A-Za-z0-9_-]+", "_", (c or {}).get("thread_id") or "command-line")


def folder_for(thread):
    return Path(automc_paths.WORKSPACE).resolve() / FOLDER / thread


def output_limit(window=None):
    if not window:
        return DEFAULT_CHARS
    return int(min(MAX_CHARS, max(MIN_CHARS, window * WINDOW_SHARE * CHARS_PER_TOKEN)))


def _rel(p):
    return Path(p).relative_to(Path(automc_paths.WORKSPACE).resolve()).as_posix()


# ----------------------------------------------------------------------------------------------------------------------
# planning
# ----------------------------------------------------------------------------------------------------------------------
def plan_run(code, timeout_s=None):
    if not isinstance(code, str) or not code.strip():
        raise Refused("code", "must be the Python code to run", "InvalidArgument", 2)
    if len(code) > MAX_CODE:
        raise Refused("code", f"is {len(code):,} characters; at most {MAX_CODE:,}. Write it to a file in the workspace and run that",
                      "InvalidArgument", 2)
    timeout = DEFAULT_TIMEOUT if timeout_s is None else timeout_s
    if not isinstance(timeout, int) or not 1 <= timeout <= MAX_TIMEOUT:
        raise Refused("timeout_s", f"must be a whole number of seconds from 1 to {MAX_TIMEOUT}", "InvalidArgument", 2)
    c = conversation.current() or {}
    provider = c.get("provider")
    thread = _thread()
    from agent import guard
    send_option = bool(provider is not None and getattr(provider, "cloud", False) and guard.facility_mode())
    warnings = [w for rx, w in WARNINGS if rx.search(code)]
    limit = output_limit(c.get("window"))
    plan = {"kind": "python", "code": code, "timeout_s": timeout, "thread": thread, "folder": f"{FOLDER}/{thread}",
            "kernel": "running" if thread in _KERNELS else "new", "output_limit": limit, "warnings": warnings,
            "send_option": send_option, "needs_send": False,
            "facility_data": "public"}       # the plan holds the model's code and goes to the user's question, not to the model
    plan["plan_id"] = hashlib.sha256(json.dumps([code, timeout, thread], sort_keys=True).encode()).hexdigest()[:16]
    plan["text"] = describe(plan)
    return plan


def describe(plan):
    reader = ("a cloud model reads the output only if you type 'yes, send'; with 'y' you see the output and the model is told it was "
              "withheld" if plan["send_option"] else "the chat model reads it")
    lines = plan["code"].rstrip("\n").split("\n")
    width = len(str(len(lines)))
    code = "\n".join(f"  {i:>{width}} | {line}" for i, line in enumerate(lines, 1))
    return "\n".join([
        console.banner("Run Python code in this conversation's kernel"),
        console.fields([("Folder", f"workspace/{plan['folder']}/ (the kernel's working folder)"),
                        ("Kernel", "already running: its variables are kept" if plan["kernel"] == "running" else
                         "a new one, for this conversation; no keys in its environment"),
                        ("Time", f"at most {plan['timeout_s']} s, then the run is interrupted"),
                        ("Output", f"saved whole in the folder; the model reads up to {plan['output_limit']:,} characters of it "
                                   f"(start and end); {reader}"),
                        ("Note", "not a sandbox: the code can do what you can, so read it before you say yes")]
                       + [("Warning", w) for w in plan["warnings"]]),
        "", f"  Code ({len(lines)} line(s)):", code])


# ----------------------------------------------------------------------------------------------------------------------
# the kernel
# ----------------------------------------------------------------------------------------------------------------------
def _clean_env():
    env = {k: v for k, v in os.environ.items() if k not in DROPPED and not SECRETISH.search(k)}
    env.update(MPLBACKEND="Agg", PYTHONUNBUFFERED="1")
    return env


def _kernel(thread, folder):
    k = _KERNELS.get(thread)
    if k and k["km"].is_alive():
        return k
    try:
        from jupyter_client import KernelManager
    except ImportError:
        raise Refused("python", "jupyter_client and ipykernel are not installed in this environment", "MissingLibrary", 4) from None
    folder.mkdir(parents=True, exist_ok=True)
    # Linux and WSL: local sockets (no network port at all), by an absolute path in /tmp (a Windows drive under /mnt cannot hold
    # sockets, and a relative path would differ between the kernel's folder and ours). Windows, or if that fails: TCP on 127.0.0.1,
    # signed with the connection file's key.
    sockets = tempfile.mkdtemp(prefix="automc-kernel-") if os.name == "posix" else None
    for transport in (["ipc", "tcp"] if sockets else ["tcp"]):
        km = KernelManager(kernel_name="python3", transport=transport, **({"ip": f"{sockets}/kernel"} if transport == "ipc" else {}))
        km.start_kernel(env=_clean_env(), cwd=str(folder))
        kc = km.client()
        kc.start_channels()
        try:
            kc.wait_for_ready(timeout=60)
            break
        except RuntimeError:
            kc.stop_channels()
            km.shutdown_kernel(now=True)
            if transport == "tcp":
                raise Refused("python", "the Python kernel did not start (no answer in 60 s)", "Unavailable", 5) from None
    _KERNELS[thread] = {"km": km, "kc": kc, "folder": folder, "sockets": sockets}
    return _KERNELS[thread]


def shutdown(thread=None):
    for t in [thread] if thread else list(_KERNELS):
        k = _KERNELS.pop(t, None)
        if not k:
            continue
        try:
            k["kc"].stop_channels()
            k["km"].shutdown_kernel(now=True)
        except Exception:
            pass
        if k.get("sockets"):
            shutil.rmtree(k["sockets"], ignore_errors=True)


atexit.register(shutdown)


def _execute(k, code, timeout):
    """(text, error, images, timed_out, restarted): everything the kernel printed for this code, in order."""
    kc, km = k["kc"], k["km"]
    msg_id = kc.execute(code, store_history=True, allow_stdin=False)
    parts, images, error = [], [], None
    deadline, timed_out, restarted = time.monotonic() + timeout, False, False
    try:
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                if timed_out:                            # interrupted, and still not idle: restart (its variables are lost)
                    km.restart_kernel(now=True)
                    kc.wait_for_ready(timeout=60)
                    restarted = True
                    break
                km.interrupt_kernel()
                timed_out, deadline = True, time.monotonic() + 15
                continue
            try:
                msg = kc.get_iopub_msg(timeout=min(1.0, left))
            except queue.Empty:
                continue
            if (msg.get("parent_header") or {}).get("msg_id") != msg_id:
                continue
            t, c = msg["msg_type"], msg["content"]
            if t == "stream":
                parts.append(c.get("text", ""))
            elif t in ("execute_result", "display_data"):
                data = c.get("data") or {}
                if "image/png" in data:
                    images.append(base64.b64decode(data["image/png"]))
                elif "text/plain" in data:
                    parts.append(data["text/plain"] + "\n")
            elif t == "error":
                parts.append(ANSI.sub("", "\n".join(c.get("traceback") or [])) + "\n")
                error = f"{c.get('ename')}: {c.get('evalue')}"
            elif t == "status" and c.get("execution_state") == "idle":
                break
    except KeyboardInterrupt:                            # Ctrl-C in the chat: stop the code too
        km.interrupt_kernel()
        raise
    return "".join(parts), error, images, timed_out, restarted


_FIGURES = """
import sys as _automc_sys
if "matplotlib.pyplot" in _automc_sys.modules:
    import matplotlib.pyplot as _automc_plt
    for _automc_n in _automc_plt.get_fignums():
        _automc_f = "figure-{run:03d}-%d.png" % _automc_n
        _automc_plt.figure(_automc_n).savefig(_automc_f, dpi=120, bbox_inches="tight")
        print(_automc_f)
    _automc_plt.close("all")
"""


def _snapshot(folder):
    out = {}
    for p in folder.rglob("*"):
        if p.is_file() and "runs" not in p.relative_to(folder).parts[:1]:
            st = p.stat()
            out[p.relative_to(folder).as_posix()] = (st.st_size, st.st_mtime_ns)
    return out


def _shorten(text, limit):
    if len(text) <= limit:
        return text, 0
    head = int(limit * HEAD_SHARE)
    tail = limit - head
    left = len(text) - head - tail
    return f"{text[:head]}\n[... {left:,} characters left out here; the whole output is in the run's file ...]\n{text[-tail:]}", left


# ----------------------------------------------------------------------------------------------------------------------
# running
# ----------------------------------------------------------------------------------------------------------------------
def start(plan, approved_by, send=False):
    """Run an approved plan's code in its conversation's kernel. `send`: the user typed 'yes, send' (a cloud model may read it)."""
    if not isinstance(approved_by, str) or not approved_by.strip():
        raise Refused("approved_by", "Python runs only with the user's approval, given through a surface", "NeedsApproval", 3)
    if plan.get("kind") != "python" or not isinstance(plan.get("code"), str):
        raise Refused("plan", "not a Python plan", "InvalidArgument", 2)
    expected = hashlib.sha256(json.dumps([plan["code"], plan["timeout_s"], plan["thread"]], sort_keys=True).encode()).hexdigest()[:16]
    if expected != plan.get("plan_id"):
        raise Refused("plan", "the code changed since the user saw it; make a new plan and ask again", "NotAllowed", 3)
    folder = folder_for(plan["thread"])
    runs = folder / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    k = _kernel(plan["thread"], folder)
    n = 1 + max([int(m.group(1)) for p in runs.glob("run-*.py") for m in [re.match(r"run-(\d+)\.py$", p.name)] if m] or [0])
    (runs / f"run-{n:03d}.py").write_text(plan["code"], encoding="utf-8")
    before = _snapshot(folder)
    t = time.monotonic()
    text, error, images, timed_out, restarted = _execute(k, plan["code"], plan["timeout_s"])
    seconds = round(time.monotonic() - t, 1)
    figures = []
    if not restarted:
        figs, _, more, _, _ = _execute(k, _FIGURES.format(run=n), 30)
        figures = [f for f in figs.split() if f.endswith(".png")]
        images += more
    for i, png in enumerate(images, 1):                  # figures the kernel displayed itself (an inline backend)
        name = f"figure-{n:03d}-d{i}.png"
        (folder / name).write_bytes(png)
        figures.append(name)
    after = _snapshot(folder)
    written = sorted(f for f in after if before.get(f) != after[f] and f not in figures)
    out_file = runs / f"run-{n:03d}.txt"
    out_file.write_text(text, encoding="utf-8")
    shown, left = _shorten(text, plan.get("output_limit") or DEFAULT_CHARS)
    status = "timed_out" if timed_out else "error" if error else "ok"
    result = {"run": n, "status": status, "seconds": seconds, "output": shown, "output_chars": len(text),
              "output_file": _rel(out_file), "code_file": _rel(runs / f"run-{n:03d}.py"), "error": error,
              "files_written": [f"{_rel(folder)}/{f}" for f in written[:50]], "figures": [f"{_rel(folder)}/{f}" for f in figures]}
    if left:
        result["cut"] = f"{left:,} characters were left out of the middle; the whole output is in {result['output_file']} (FileReadTool)"
    if timed_out:
        result["timed_out"] = (f"stopped after {plan['timeout_s']} s" + ("; the kernel did not stop and was restarted, so its "
                                                                          "variables are gone" if restarted else "; the kernel and its "
                                                                          "variables are kept"))
    if send:
        result["facility_data"] = "public"           # the user typed 'yes, send' for this output
    result["next"] = ("answer from the output; quote numbers as printed" if status == "ok" else
                      "read the error, fix the code, and ask to run it again (the user approves each run)")
    return result


def for_the_user(result):
    """A run's result as the chat shows it to the user when a cloud model may not read it: the status, the output (its start and end,
    at most SHOWN_CHARS), the error, the figures and the file that holds the whole output."""
    output, _ = _shorten((result.get("output") or "").rstrip("\n"), SHOWN_CHARS)
    lines = [f"run {result.get('run')}: {result.get('status')} in {result.get('seconds')} s; the whole output is in "
             f"workspace/{result.get('output_file')}"]
    if result.get("timed_out"):
        lines.append(result["timed_out"])
    lines += [output] if output.strip() else ["(no output)"]
    if result.get("error"):
        lines.append(f"error: {result['error']}")
    if result.get("figures"):
        lines.append("figures: " + ", ".join(f"workspace/{f}" for f in result["figures"]))
    return "\n".join(lines)
