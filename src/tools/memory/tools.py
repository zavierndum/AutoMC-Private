"""
The memory tools (devops/AutoMC_Workspace_Plan.md, section 6, WD-4). MemoryWriteTool never writes: it returns the note as a plan, and the
chat shows it to the user and asks (as a run is asked); only an approved note is saved, with the conversation's label, which the model
cannot set. MemoryReadTool reads one note whole, with its label for the data guard.
"""
from __future__ import annotations

import hashlib
import json

import console
from tools import conversation
from tools.files.places import Refused
from tools.files.tools import as_tool

from . import store

WRITE_SCHEMA = {"type": "object", "required": ["title", "text"], "additionalProperties": False, "properties": {
    "title": {"type": "string", "description": "a short title; a note with the same title is replaced, not added twice"},
    "text": {"type": "string", "description": f"one fact or preference worth keeping for later conversations of this workspace, at most "
                                              f"{store.MAX_NOTE:,} characters"},
}}
READ_SCHEMA = {"type": "object", "required": ["name"], "additionalProperties": False, "properties": {
    "name": {"type": "string", "description": "the note's name, as the memory index in your prompt lists it"},
}}


def _label():
    return (conversation.current() or {}).get("label") or "public"


def plan_note(title, text):
    if not isinstance(title, str) or not title.strip() or not isinstance(text, str) or not text.strip():
        raise Refused("text", "a note needs a short title and its text", "InvalidArgument", 2)
    if len(text) > store.MAX_NOTE:
        raise Refused("text", f"a note is one fact, at most {store.MAX_NOTE:,} characters; shorten it", "InvalidArgument", 2)
    name = store.slug(title)
    old = store.get(name)
    plan = {"kind": "memory", "title": title.strip(), "note": text.strip(), "name": name, "replaces": bool(old), "label": _label(),
            "facility_data": "public"}                   # the plan goes to the user's question, not to the model
    plan["plan_id"] = _plan_id(plan)
    plan["text"] = describe(plan, old)                    # what the chat shows the user
    return plan


def _plan_id(plan):
    return hashlib.sha256(json.dumps([plan["title"], plan["note"]]).encode()).hexdigest()[:16]


def describe(plan, old=None):
    lines = [console.banner("Save a note to this workspace's memory"),
             console.fields([("Title", plan["title"]), ("Name", f"{plan['name']} (.automc/memory/{plan['name']}.md)"),
                             ("Label", f"{plan['label']} (this conversation's; you can mark a note public later: /memory public NAME)"),
                             ("Replaces", f"the note saved {old.get('updated')}: {old['text'][:120]}" if old else None),
                             ("Seen by", "later conversations of this workspace, in their prompt (a cloud model only if public)")]),
             "", "  Note:"] + [f"    {line}" for line in plan["note"].split("\n")]
    return "\n".join(lines)


def start(plan, approved_by, send=False):
    """Save an approved note. The label is the conversation's as it is now, never lower than when the user saw the plan."""
    if not isinstance(approved_by, str) or not approved_by.strip():
        raise Refused("approved_by", "a note is saved only with the user's approval", "NeedsApproval", 3)
    if plan.get("kind") != "memory":
        raise Refused("plan", "not a memory plan", "InvalidArgument", 2)
    if _plan_id(plan) != plan.get("plan_id"):
        raise Refused("plan", "the note changed since the user saw it; ask again", "NotAllowed", 3)
    label = max((plan.get("label") or "public", _label()), key=store.ORDER.get)
    try:
        note = store.save(plan["title"], plan["note"], label, chat=(conversation.current() or {}).get("thread_id"))
    except store.MemoryError_ as e:
        raise Refused("text", str(e), "InvalidArgument", 2) from None
    return {"saved": note["name"], "replaced": note["replaced"], "label": note["label"], "facility_data": "public",
            "message": f"saved as {note['name']} ({note['label']}); later conversations of this workspace will see it"}


@as_tool(WRITE_SCHEMA)
def memory_write(args):
    plan = plan_note(args["title"], args["text"])
    return {"started": False, "needs_approval": True, "plan": plan, "facility_data": "public",
            "message": "The chat shows the user the note and asks; nothing is saved before they say yes."}


@as_tool(READ_SCHEMA)
def memory_read(args):
    note = store.get(args["name"])
    if note is None:
        raise Refused("name", f"no note {args['name']!r}; the memory index in your prompt lists them", "NotFound", 2)
    return {"note": store.as_json(note), "facility_data": note["label"]}


TOOLS = [
    {"name": "MemoryWriteTool", "handler": memory_write, "parameters": WRITE_SCHEMA,
     "description": "Propose a note for this workspace's memory: one fact or preference that later conversations should know (a "
                    "decision, a setting the user always wants, a finding they asked you to remember). The user sees it and decides; "
                    "only then is it saved, with this conversation's label. Do not save what the tools can find again, or anything "
                    "the user did not want kept."},
    {"name": "MemoryReadTool", "handler": memory_read, "parameters": READ_SCHEMA,
     "description": "Read one note of this workspace's memory whole, by the name the memory index in your prompt gives."},
]
