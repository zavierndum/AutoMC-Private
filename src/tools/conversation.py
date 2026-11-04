"""
What a tool may know about the conversation it is called in, set by the agent core around every tool call (automc_core.Session._tools)
so the tools stay "arguments in, result out": the chat model (a provider), the conversation's label so far (public, unknown or
restricted: the most restricted of what the tool results put into it), the conversation's id and the model's context window in tokens.

Outside a conversation (the command line, `python -m automc tool`) there is none: the user typed the call, and no conversation's data
can leave with it.
"""
from __future__ import annotations

import contextlib
import contextvars

_CURRENT = contextvars.ContextVar("automc_conversation", default=None)


@contextlib.contextmanager
def inside(provider=None, label="public", thread_id=None, window=None):
    token = _CURRENT.set({"provider": provider, "label": label or "public", "thread_id": thread_id, "window": window})
    try:
        yield
    finally:
        _CURRENT.reset(token)


def current():
    """The conversation a tool runs in ({provider, label, thread_id, window}), or None outside one."""
    return _CURRENT.get()


def label():
    c = current()
    return c["label"] if c else "public"
