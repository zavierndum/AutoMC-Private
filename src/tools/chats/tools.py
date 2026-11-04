"""
ChatSearchTool (devops/AutoMC_Workspace_Plan.md, section 5, WD-3): the past conversations of this workspace and of the linked ones,
searched by their words, as AutoSAM's agent searches its sessions, but only within the workspace and its links, and with each hit
carrying its conversation's label, so the data guard keeps a restricted conversation's words from a cloud model.
"""
from __future__ import annotations

from tools import conversation
from tools.files.places import Refused, worst
from tools.files.tools import as_tool

from . import store

SEARCH_SCHEMA = {"type": "object", "required": ["query"], "additionalProperties": False, "properties": {
    "query": {"type": "string", "description": "words to look for in earlier questions and answers"},
    "max_results": {"type": "integer", "minimum": 1, "maximum": 10, "description": "default 5"},
}}


@as_tool(SEARCH_SCHEMA)
def chat_search(args):
    query = args.get("query")
    if not isinstance(query, str) or not query.strip():
        raise Refused("query", "must be the words to look for", "InvalidArgument", 2)
    current = (conversation.current() or {}).get("thread_id")
    hits = store.search(query, exclude=current, k=int(args.get("max_results") or 5))
    out = {"query": query, "results": hits, "facility_data": worst([h["facility_data"] for h in hits]) if hits else "public"}
    out["next"] = ("answer from these earlier turns and say which conversation and when; they are what was said then, not checked "
                   "since" if hits else "nothing found in earlier conversations of this workspace or its linked ones")
    return out


TOOLS = [
    {"name": "ChatSearchTool", "handler": chat_search, "parameters": SEARCH_SCHEMA,
     "description": "Search the earlier conversations saved in this workspace (and in the linked workspaces) for questions and answers "
                    "that match the words, when the user refers to something said before ('as we found last week'). Returns the best "
                    "turns with their conversation and date. What was said then is not checked since: verify numbers with the tools."},
]
