"""
What may leave the machine through the web tools (devops/AutoMC_General_Tools_Plan.md, GT-4). A query or a URL is written by the model,
so it can carry whatever the conversation holds. The conversation's label decides (tools/conversation.py), with any model, local or
cloud:

  public       sent
  unknown      sent only after the user types 'yes, send' (the chat asks; the same rule as switching to a cloud model, decision D1)
  restricted   never sent: start a new conversation without restricted data for the web

What comes back from the web is public. Outside a conversation (the command line) the user typed the request, so it is sent.

    gate(field)                     the label, or Refused for a restricted conversation
    plan(action, args)              the question for an unknown conversation; start(plan, approved_by, send) after the user's answer
"""
from __future__ import annotations

import hashlib
import json

import console
from tools import conversation
from tools.files.places import Refused

ACTIONS = {"search": "a web search", "fetch": "a web page request"}


def gate(field):
    lab = conversation.label()
    if lab == "restricted":
        raise Refused(field, "not sent: this conversation holds restricted facility data, and nothing written in it may leave this "
                             "machine, through the web either (with a local model too). Ask in a new conversation without restricted data",
                      "NotAllowed", 3)
    return lab


def plan(action, args):
    """The question the chat puts to the user before a request leaves a conversation that holds data of unknown origin."""
    what = args.get("query") or args.get("url")
    plan = {"kind": "web", "action": action, "args": args, "label": "unknown", "needs_send": True,
            "plan_id": hashlib.sha256(json.dumps([action, args], sort_keys=True).encode()).hexdigest()[:16],
            "facility_data": "public"}       # the plan holds what the model wrote and goes to the user's question, not to the model
    plan["text"] = "\n".join([
        console.banner(f"Send {ACTIONS[action]} from this conversation"),
        console.fields([("Query" if action == "search" else "URL", what),
                        ("Goes to", "Tavily (api.tavily.com)" if action == "search" else "the site named in the URL"),
                        ("Why ask", "this conversation holds results of unknown origin; if any of it is restricted, it must not leave this "
                                    "machine, and the model wrote this request"),
                        ("Answer", "type 'yes, send' to send it; anything else sends nothing")])])
    return plan


def start(plan, approved_by, send=False):
    """Send what a plan describes, after the user typed 'yes, send' (send=True) through a surface."""
    if not isinstance(approved_by, str) or not approved_by.strip():
        raise Refused("approved_by", "a web request from this conversation needs the user's answer, given through a surface",
                      "NeedsApproval", 3)
    if not send:
        raise Refused("approval", "not sent: a request from a conversation holding data of unknown origin needs 'yes, send' typed",
                      "NeedsApproval", 3)
    from . import fetch, search
    a = plan["args"]
    if plan["action"] == "search":
        return search.search(a["query"], a.get("max_results") or search.DEFAULT_RESULTS)
    return fetch.fetch(a["url"], a.get("max_chars") or fetch.DEFAULT_CHARS, a.get("start") or 0)
