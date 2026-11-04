"""
The web tools (devops/AutoMC_General_Tools_Plan.md, GT-4). Same shape as the other tools: a dict of arguments in, a JSON-serializable
dict out, {"ok": false, "error": {...}} instead of raising. In a public conversation they answer at once; in one holding data of unknown
origin they return a question for the user (the chat asks for 'yes, send'); in one holding restricted data they refuse (gate.py).
"""
from __future__ import annotations

from tools.files.places import Refused
from tools.files.tools import as_tool

from . import fetch, gate, search

SEARCH_SCHEMA = {"type": "object", "required": ["query"], "additionalProperties": False, "properties": {
    "query": {"type": "string", "description": "what to look for, in plain words; it leaves this machine, so write only what the "
                                               "conversation may show the web"},
    "max_results": {"type": "integer", "minimum": 1, "maximum": search.MAX_RESULTS, "description": f"default {search.DEFAULT_RESULTS}"},
}}
FETCH_SCHEMA = {"type": "object", "required": ["url"], "additionalProperties": False, "properties": {
    "url": {"type": "string", "description": "an http or https address on the public internet"},
    "max_chars": {"type": "integer", "minimum": 1000, "maximum": fetch.MAX_CHARS, "description": f"default {fetch.DEFAULT_CHARS:,}"},
    "start": {"type": "integer", "minimum": 0, "description": "where to go on from, as the last result's next_start gave it"},
}}


def _query(args):
    q = args["query"]
    if not isinstance(q, str) or not q.strip():
        raise Refused("query", "must be the words to search for", "InvalidArgument", 2)
    return q.strip()[:400]


def _ask(action, args):
    return {"started": False, "needs_approval": True, "plan": gate.plan(action, args), "facility_data": "public",
            "message": "This conversation holds results of unknown origin; the chat asks the user to type 'yes, send' before the "
                       "request leaves this machine."}


@as_tool(SEARCH_SCHEMA)
def web_search(args):
    q, n = _query(args), args.get("max_results") or search.DEFAULT_RESULTS
    if gate.gate("query") == "unknown":
        return _ask("search", {"query": q, "max_results": n})
    return search.search(q, n)


@as_tool(FETCH_SCHEMA)
def web_fetch(args):
    label = gate.gate("url")                                  # before anything, even a DNS lookup, leaves the machine
    url = args["url"].strip() if isinstance(args["url"], str) else args["url"]
    if not isinstance(url, str) or not url.startswith(("http://", "https://")):
        raise Refused("url", "must be an http or https address", "InvalidArgument", 2)
    rest = {k: args[k] for k in ("max_chars", "start") if args.get(k) is not None}
    if label == "unknown":
        return _ask("fetch", {"url": url, **rest})
    return fetch.fetch(url, rest.get("max_chars") or fetch.DEFAULT_CHARS, rest.get("start") or 0)


TOOLS = [
    {"name": "WebSearchTool", "handler": web_search, "parameters": SEARCH_SCHEMA,
     "description": "Search the web (Tavily): a short answer and the best pages, each with its title, URL and a snippet. The query leaves "
                    "this machine: a conversation holding restricted facility data cannot search (with any model), and one holding data "
                    "of unknown origin asks the user first. Cite the URLs you use. For facts in the user's own documents, search the "
                    "knowledge bases instead (DocumentSearchTool)."},
    {"name": "WebFetchTool", "handler": web_fetch, "parameters": FETCH_SCHEMA,
     "description": "Read one web page or PDF on the public internet as text, paged (max_chars from start). A PDF is also saved in the "
                    "workspace, where DocumentParseTool can make a knowledge base of it with its figures. The URL leaves this machine, "
                    "under the same rule as WebSearchTool; local and private addresses are refused."},
]
