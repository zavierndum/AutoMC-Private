"""
The context guard: keep what is sent to the model inside its window. Ollama drops the oldest context silently when a request is longer
than the model's num_ctx, so with the 32k default a long conversation would lose its system prompt without any message. Before every
model step the guard estimates the request's size; it never changes the conversation itself (the checkpointed messages and the audit
keep everything), only what is sent in that step:

  - above WARN of the window: a notice for the user ("start /new soon, or switch to a model with a larger window");
  - above TRIM: the oldest tool results (never those of the current turn) are shortened to their first lines, oldest first, until the
    request is under TARGET;
  - if the system prompt, the tool schemas and the current turn alone do not fit under FULL: the step is refused with a message.

Sizes are estimated from characters at CHARS_PER_TOKEN, a little under what was measured for AutoMC's prompt and tool schemas (about
3.9 characters per token, 2026-10-26), so the estimate errs on the large side.
"""
from __future__ import annotations

import json

from langchain_core.messages import HumanMessage, ToolMessage

CHARS_PER_TOKEN = 3.5
WARN, TRIM, TARGET, FULL = 0.70, 0.85, 0.75, 0.95
KEEP_CHARS = 300                       # what is left of a shortened tool result


def _chars(m):
    c = m.content
    return len(c) if isinstance(c, str) else len(json.dumps(c, default=str))


def estimate(messages, extra_chars=0):
    """Estimated tokens of a request: the messages plus extra characters (the tool schemas)."""
    return int((sum(_chars(m) for m in messages) + extra_chars) / CHARS_PER_TOKEN) + 4 * len(messages)


def _current_turn_start(messages):
    for i in range(len(messages) - 1, -1, -1):
        if isinstance(messages[i], HumanMessage):
            return i
    return 0


def fit(messages, window, extra_chars=0):
    """
    (messages to send, info). info: {"tokens", "window", "fraction", "shortened", "notice", "refuse"}. `messages` is the full list with
    the system message first; nothing in it is changed.
    """
    info = {"tokens": estimate(messages, extra_chars), "window": window, "shortened": 0, "notice": None, "refuse": None}
    info["fraction"] = info["tokens"] / window
    if info["fraction"] <= TRIM:
        if info["fraction"] > WARN:
            info["notice"] = (f"The conversation fills about {info['fraction']:.0%} of the model's {window:,}-token window. Start /new soon, "
                              f"or switch to a model with a larger window (/model).")
        return messages, info
    out = list(messages)
    keep_from = _current_turn_start(out)
    for i in range(1, keep_from):
        if estimate(out, extra_chars) <= TARGET * window:
            break
        m = out[i]
        if isinstance(m, ToolMessage) and _chars(m) > KEEP_CHARS:
            out[i] = ToolMessage(content=m.content[:KEEP_CHARS] + " ... [older tool result shortened to keep the conversation inside the "
                                 "model's window; ask again if you need it]", tool_call_id=m.tool_call_id, name=m.name)
            info["shortened"] += 1
    info["tokens"] = estimate(out, extra_chars)
    info["fraction"] = info["tokens"] / window
    if info["fraction"] > FULL:
        info["refuse"] = (f"This conversation no longer fits the model's {window:,}-token window (about {info['tokens']:,} tokens even "
                          f"after shortening older tool results). Start /new, or switch to a model with a larger window (/model).")
    elif info["shortened"]:
        info["notice"] = (f"{info['shortened']} older tool result(s) were shortened to keep the conversation inside the model's "
                          f"{window:,}-token window; the full results stay in the audit.")
    return out, info
