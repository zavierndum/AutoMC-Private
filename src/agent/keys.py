"""
Keys from a file outside AutoMC (devops/AutoMC_General_Tools_Plan.md, GT-1).

AUTOMC_ENV_FILE names a .env-style file (NAME=value lines), for example ~/.config/automc/keys.env, readable only by its owner
(config/keys.env.example lists the names). AutoMC keeps from it only the variables it uses (used(): each provider's api_key_env in
config/llm.yaml, the web search key, the LangSmith settings, the model a chat starts with); the others are dropped as the file is
read, and only their number is
kept. The values stay in this module and never go into os.environ, so no child process (a run, a worker) inherits them; the one
process given a key is a parse of a public document that uses the chat model's service (tools/documents/parse.py). A variable set
in the shell wins over the file. A file inside the AutoMC folder is refused, because that folder is published.

value(NAME) is how AutoMC reads a key; source(NAME) says where it came from; status() describes the file. No value is ever printed,
written or put into a message.
"""
from __future__ import annotations

import os
from pathlib import Path

import automc_paths

ENV_VAR = "AUTOMC_ENV_FILE"
FIXED = ("TAVILY_API_KEY", "LANGSMITH_API_KEY", "LANGCHAIN_API_KEY", "LANGSMITH_ENDPOINT", "AUTOMC_TRACING", "AUTOMC_LANGSMITH_PROJECT",
         "AUTOMC_LLM_PROVIDER")
SHELL, FILE = "shell", "keys file"
_cache = {}                                                     # the AUTOMC_ENV_FILE value -> what was read from it


def used():
    """The variables AutoMC takes from the keys file: FIXED and every api_key_env in config/llm.yaml."""
    names = set(FIXED)
    try:
        from agent import llm
        names |= {p["api_key_env"] for p in llm.load_config()["providers"].values() if isinstance(p, dict) and p.get("api_key_env")}
    except Exception:                                           # a broken llm.yaml is reported by `models list`, not here
        pass
    return sorted(names)


def _read(path):
    st = {"file": None, "values": {}, "other": 0, "error": None, "warning": None}
    if not path:
        return st
    p = Path(path).expanduser()
    st["file"] = str(p)
    try:
        real = p.resolve(strict=True)
    except OSError:
        st["error"] = f"{ENV_VAR} names {p}, which does not exist"
        return st
    root = automc_paths.AUTOMC_ROOT.resolve()
    if real == root or root in real.parents:
        st["error"] = (f"refused: {p} is inside the AutoMC folder, which is published; keep it outside, for example "
                       f"~/.config/automc/keys.env")
        return st
    try:
        from dotenv import dotenv_values
    except ImportError:
        st["error"] = "python-dotenv is not installed (pip install python-dotenv==1.2.3 in the automc environment)"
        return st
    try:
        raw = dotenv_values(real)
    except (OSError, UnicodeDecodeError) as e:
        st["error"] = f"cannot read {p}: {type(e).__name__}"
        return st
    names = set(used())
    st["values"] = {k: v for k, v in raw.items() if k in names and v}
    st["other"] = sum(1 for k in raw if k not in names)
    if os.name == "posix" and not str(real).startswith("/mnt/") and real.stat().st_mode & 0o077:
        st["warning"] = f"{p} can be read by other users of this machine: chmod 600 {p}"
    return st


def _state():
    path = os.environ.get(ENV_VAR, "").strip()
    if path not in _cache:
        _cache.clear()
        _cache[path] = _read(path)
    return _cache[path]


def value(name):
    """The variable's value: the shell's if it is set there, else the keys file's, else ""."""
    if not name:
        return ""
    return os.environ.get(name) or _state()["values"].get(name, "")


def source(name):
    """shell, keys file, or None (not set)."""
    if name and os.environ.get(name):
        return SHELL
    if name and _state()["values"].get(name):
        return FILE
    return None


def status():
    """{env, file, error, warning, used: the names the file gave AutoMC, other: how many other variables it holds}."""
    st = _state()
    return {"env": ENV_VAR, "file": st["file"], "error": st["error"], "warning": st["warning"], "used": sorted(st["values"]),
            "other": st["other"]}


def reset():
    """Read the file again at the next value() (after it was edited)."""
    _cache.clear()
