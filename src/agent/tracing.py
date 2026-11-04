"""
LangSmith tracing of what a cloud model may see, and nothing else (devops/AutoMC_General_Tools_Plan.md, GT-1).

LangChain traces every call when LANGCHAIN_TRACING_V2 or LANGSMITH_TRACING is true (AutoSAM sets it whenever a LangChain key is
present). That would send local-model conversations holding facility data to LangSmith's servers, so force_off() sets every such
switch to false when the agent package is imported, before any call (LangSmith reads them once and keeps them). The agent then traces
one model call at a time (Session._agent with call()), only when that call could go to a cloud model as it is: the conversation is
public, and the system prompt lists no restricted pack skill (the model is a cloud one, or no restricted pack is installed). A call
traced this way shows in LangSmith as its own run, with the conversation as thread_id.

On only when AUTOMC_TRACING=langsmith and a key is in LANGSMITH_API_KEY (or LANGCHAIN_API_KEY, its older name), from the shell or the
keys file (agent/keys.py); a value too short to be a key (a placeholder) is passed over for the other name. The project is AUTOMC_LANGSMITH_PROJECT, else automc; LANGSMITH_ENDPOINT chooses another LangSmith region.

LangSmith sends in the background and logs its failures; those are caught here instead of printed (take_failure()), so the chat says
once, in one sentence, that the traces were refused. Model calls are never held up by LangSmith.
"""
from __future__ import annotations

import importlib.util
import logging
import os
import re
from contextlib import contextmanager

from agent import keys

SWITCHES = ("LANGSMITH_TRACING", "LANGCHAIN_TRACING", "LANGSMITH_TRACING_V2", "LANGCHAIN_TRACING_V2")
KEY_NAMES = ("LANGSMITH_API_KEY", "LANGCHAIN_API_KEY")
MIN_KEY = 20                       # LangSmith keys are 36 to 51 characters; a shorter value is a placeholder, and the next name is used
PROJECT = "automc"
_clients = {}
_failures = {"new": None, "said": set()}             # the latest reason LangSmith failed; the reasons already told


class _Failures(logging.Handler):
    """Keeps LangSmith's warnings (a refused upload, an unreachable server) as one short reason, without the key or the payload."""

    def emit(self, record):
        if record.levelno < logging.WARNING:
            return
        text = record.getMessage()
        code = re.search(r"\b([45]\d\d) (?:Client|Server) Error", text)
        if code:
            n = int(code.group(1))
            reason = (f"HTTP {n}: the key was refused" if n in (401, 403) else
                      f"HTTP {n}: LangSmith is limiting traces or the plan's quota is spent" if n == 429 else f"HTTP {n}")
        elif "Connection" in text or "Timeout" in text:
            reason = "LangSmith could not be reached"
        else:
            reason = text.splitlines()[0][:120]
        _failures["new"] = reason


def take_failure():
    """The latest reason LangSmith refused or lost traces, once per reason; None if there is nothing new to tell."""
    reason, _failures["new"] = _failures["new"], None
    if reason is None or reason in _failures["said"]:
        return None
    _failures["said"].add(reason)
    return reason


def _quiet_langsmith():
    log = logging.getLogger("langsmith")
    if not any(isinstance(h, _Failures) for h in log.handlers):
        log.addHandler(_Failures())
        log.propagate = False


def force_off():
    for s in SWITCHES:
        os.environ[s] = "false"


def settings():
    """{requested, on, project, message}: requested is whether AUTOMC_TRACING asks for tracing at all."""
    asked = keys.value("AUTOMC_TRACING").strip().lower()
    out = {"requested": asked not in ("", "off", "false", "0", "no"), "on": False, "project": None}
    if not out["requested"]:
        return {**out, "message": "off (AUTOMC_TRACING=langsmith traces what a cloud model may see)"}
    if asked != "langsmith":
        return {**out, "message": f"off: AUTOMC_TRACING is {asked!r}; the only tracer is langsmith"}
    short = [n for n in KEY_NAMES if 0 < len(keys.value(n)) < MIN_KEY]
    key_name = next((n for n in KEY_NAMES if len(keys.value(n)) >= MIN_KEY), None)
    ignored = f" ({', '.join(short)} ignored: too short to be a LangSmith key)" if short else ""
    if not key_name:
        return {**out, "message": "off: AUTOMC_TRACING=langsmith, but there is no key in LANGSMITH_API_KEY or LANGCHAIN_API_KEY"
                                  + ignored}
    if importlib.util.find_spec("langsmith") is None:
        return {**out, "message": "off: the Python package langsmith is not installed"}
    project = keys.value("AUTOMC_LANGSMITH_PROJECT") or PROJECT
    return {**out, "on": True, "project": project, "key": key_name,
            "message": f"on: project {project}, only what a cloud model may see (key in {key_name}, from the {keys.source(key_name)})"
                       + ignored}


def _client(key_name):
    endpoint = keys.value("LANGSMITH_ENDPOINT") or None
    k = (key_name, endpoint)
    if k not in _clients:
        from langsmith import Client
        _quiet_langsmith()
        _clients[k] = Client(api_key=keys.value(key_name), api_url=endpoint)    # sends what is left when the process ends
    return _clients[k]


@contextmanager
def call(provider):
    """Trace the model calls made inside this block (only Session._agent uses it, after its checks)."""
    from langchain_core.tracers.context import tracing_v2_enabled
    t = settings()
    with tracing_v2_enabled(project_name=t["project"], tags=["automc", provider.name], client=_client(t["key"])):
        yield
