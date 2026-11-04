"""
Searching the web with Tavily (GT-4), as AutoSAM does (AutoSAM-SciMS tools/web_tools.py, TavilySearch): plain requests to
api.tavily.com/search with the key from AutoMC's keys file (TAVILY_API_KEY), a basic search, one chunk per source, and Tavily's short
answer. No Google Custom Search (deprecated and paid, decision of 2026-10-31). When Tavily fails or its quota is spent, the result says so.

Searches are counted per conversation and per month (workspace/.automc/web/usage.json): Tavily's free tier is 1,000 a month.
"""
from __future__ import annotations

import datetime as _dt
from pathlib import Path

import automc_paths
from tools import conversation
from tools.files.places import Refused
from tools.openmc import jobs

URL = "https://api.tavily.com/search"
KEY = "TAVILY_API_KEY"
FREE_TIER = 1000
DEFAULT_RESULTS, MAX_RESULTS = 5, 10
SNIPPET = 800                                       # characters of each result, as AutoSAM cuts them
WEB = f"{automc_paths.STATE}/web"
_PER_CONVERSATION = {}


def _usage_file():
    return Path(automc_paths.WORKSPACE).resolve() / WEB / "usage.json"


def _count():
    """Add one search to this conversation's count and this month's; return both."""
    c = conversation.current()
    key = (c or {}).get("thread_id") or "command line"
    _PER_CONVERSATION[key] = _PER_CONVERSATION.get(key, 0) + 1
    month = _dt.date.today().strftime("%Y-%m")
    path = _usage_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    with jobs._locked(path.parent):                          # the job service's lock, across processes
        usage = jobs.read_json(path) or {}
        usage[month] = usage.get(month, 0) + 1
        jobs.atomic_write_json(path, usage)
    return _PER_CONVERSATION[key], usage[month]


def _post(payload, key):
    import requests
    return requests.post(URL, headers={"Authorization": f"Bearer {key}", "User-Agent": "AutoMC"}, json=payload, timeout=20)


def search(query, max_results=DEFAULT_RESULTS):
    from agent import keys
    key = keys.value(KEY)
    if not key:
        raise Refused(KEY, "is not set: add it to AutoMC's keys file (SETUP.md section 7d)", "MissingKey", 4)
    payload = {"query": query, "max_results": max_results, "search_depth": "basic", "include_raw_content": False,
               "chunks_per_source": 1, "include_answer": "basic"}
    from tools.chats import store as chat_store
    chat_store.egress("web search (Tavily)", conversation.label(), chat=(conversation.current() or {}).get("thread_id"),
                      what="a search query")
    try:
        r = _post(payload, key)
    except Exception as e:                                   # no network, a timeout
        raise Refused("query", f"Tavily did not answer ({type(e).__name__}); the web is not reachable from here now", "Unavailable", 5) from None
    if r.status_code in (401, 403):
        raise Refused(KEY, f"Tavily refused the key (HTTP {r.status_code}); check it in the keys file", "KeyRefused", 4)
    if r.status_code in (429, 432, 433):
        raise Refused("query", f"Tavily's limit is reached (HTTP {r.status_code}): the plan's searches for this month are spent, or "
                               f"too many came at once; try later", "QuotaSpent", 5)
    if r.status_code != 200:
        raise Refused("query", f"Tavily answered HTTP {r.status_code}", "Unavailable", 5)
    data = r.json()
    in_conversation, this_month = _count()
    results = [{"title": x.get("title"), "url": x.get("url"), "score": round(x["score"], 3) if isinstance(x.get("score"), (int, float)) else None,
                "snippet": (x.get("content") or "")[:SNIPPET] + ("..." if len(x.get("content") or "") > SNIPPET else "")}
               for x in (data.get("results") or [])[:max_results]]
    out = {"query": query, "answer": data.get("answer"), "results": results,
           "searches": {"this_conversation": in_conversation, "this_month": this_month, "free_tier_per_month": FREE_TIER},
           "facility_data": "public"}
    out["next"] = ("cite the URLs you use; WebFetchTool reads a page or a PDF whole" if results else
                   "nothing found; try other words")
    if this_month >= FREE_TIER * 0.9:
        out["note"] = f"{this_month} searches this month, near Tavily's free tier of {FREE_TIER}"
    return out
