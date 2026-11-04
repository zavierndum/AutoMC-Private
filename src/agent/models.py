"""
The models from the command line (from AutoMC/src):

    python -m automc models list                 every provider in config/llm.yaml: local or cloud, whose account, ready or what it needs;
                                                 where each key comes from (the shell, the keys file, not set) and LangSmith's state
    python -m automc models discover [--refresh] the models you can choose (as /model in the chat shows them): each provider's own
                                                 model and the ones it discovers, filtered (agent/discovery.py)
    python -m automc models check NAME           one short message, no system prompt, no tools: does the provider answer, how fast,
                                                 how many tokens. NAME is a model as `discover` shows it (cloud-portkey/gpt-5.4),
                                                 a provider, or PROVIDER/MODEL.
    python -m automc models check NAME --tools   also one call with a single tiny tool and its round trip (does tool calling work
                                                 through this service), and the hello once more with all of AutoMC's tool definitions
                                                 bound, as the agent sends them (does the service accept them). A discovered cloud
                                                 model is used only after this has passed.

A check sends only the fixed text below and the tool definitions; nothing from the workspace or the facility packs. The hello and
the tool round trip cost a few hundred tokens; the hello with the tool definitions about 5,500.
Keys are read from the variables the providers name, in the shell or in the keys file AUTOMC_ENV_FILE names, and are never printed.
"""
from __future__ import annotations

import argparse
import json
import sys
import time

HELLO = "Reply with the single word: ready"
TOOL_PROMPT = "Call the tool echo_number with value 7, then tell me what it returned."
ECHO_TOOL = {"type": "function", "function": {
    "name": "echo_number", "description": "Return the number it is given, doubled.",
    "parameters": {"type": "object", "properties": {"value": {"type": "number", "description": "any number"}}, "required": ["value"],
                   "additionalProperties": False}}}


def _usage(msg):
    u = getattr(msg, "usage_metadata", None) or {}
    return {k: u.get(k) for k in ("input_tokens", "output_tokens", "total_tokens") if u.get(k) is not None}


def _text(msg):
    from agent.llm import content_text
    return content_text(msg.content).strip()


def check(name, tools=False, model=None):
    """{ok, provider, answer, seconds, usage[, tool_call, tool_answer, tool_seconds, tool_usage, schemas_answer, schemas_seconds,
    schemas_usage]} or {ok: false, error}. A real check (no `model` given) with tools is recorded for discovery.passed()."""
    from agent import discovery, errors, llm, schemas
    out = {"provider": name}
    p = None
    try:
        p = discovery.resolve(name, require_checked=False)
        from langchain_core.messages import HumanMessage, ToolMessage
        out["describe"] = p.describe()
        chat = model or llm.chat_model(p)
        t = time.time()
        msg = chat.invoke([HumanMessage(HELLO)])
        out.update(answer=_text(msg)[:200], seconds=round(time.time() - t, 2), usage=_usage(msg))
        out["ok"] = "ready" in out["answer"].lower()
        if not out["ok"]:
            out["error"] = "the provider answered, but not with the word asked for"
        if tools:
            bound = chat.bind_tools(schemas.for_backend([ECHO_TOOL], p.backend))
            t = time.time()
            first = bound.invoke([HumanMessage(TOOL_PROMPT)])
            calls = getattr(first, "tool_calls", []) or []
            out["tool_call"] = calls[0] if calls else None
            if not calls or calls[0]["name"] != "echo_number":
                out.update(ok=False, error="no echo_number tool call came back", tool_answer=_text(first)[:200])
                return out
            value = calls[0]["args"].get("value")
            second = bound.invoke([HumanMessage(TOOL_PROMPT), first,
                                   ToolMessage(content=json.dumps({"result": (value or 0) * 2}), tool_call_id=calls[0]["id"])])
            out.update(tool_answer=_text(second)[:200], tool_seconds=round(time.time() - t, 2),
                       tool_usage={"first": _usage(first), "second": _usage(second)})
            if "14" not in out["tool_answer"]:
                out.update(ok=False, error="the tool round trip did not come back with the tool's result (14)")
                return out
            t = time.time()
            defs = schemas.for_backend(schemas.definitions(), p.backend)
            msg = chat.bind_tools(defs).invoke([HumanMessage(HELLO)])
            out["schemas_count"] = len(defs)
            out.update(schemas_answer=_text(msg)[:200], schemas_seconds=round(time.time() - t, 2), schemas_usage=_usage(msg))
            if "ready" not in out["schemas_answer"].lower() or getattr(msg, "tool_calls", None):
                out.update(ok=False, error="with AutoMC's tool definitions bound, the provider did not answer the hello as asked")
    except Exception as e:                                    # a refused key, a wrong address, a rate limit: report, never raise
        if p is None:
            out.update(ok=False, error=f"{type(e).__name__}: {str(e)[:300]}")
        else:
            out.update(ok=False, error=errors.explain(e, p)[1])
    if tools and model is None and p is not None:
        discovery.record_check(p.choice, out, tools)
    return out


def picker_lines(cat, now=None):
    """discovery.catalog() as text: the models grouped (this machine, then each cloud provider), numbered, with their window and
    state, `now` marked; then the providers with nothing to choose and what the filters left out."""
    lines, group = [], None
    width = max([36] + [len(e["label"]) for e in cat["entries"]])
    for e in cat["entries"]:
        g = "on this machine (local, self_hosted)" if e["kind"] == "local" else f"{e['provider']} (cloud, {e['account']} account)"
        if g != group:
            lines.append(f"  {g}")
            group = g
        mark = "*" if now in (e["choice"], e["label"]) else " "
        window = f"{e['window']:,} tokens" if e["window"] else "window unknown"
        lines.append(f"  {mark}{e['n']:3d}  {e['label']:{width}s} {window:16s} {e['state']}")
    for u in cat["unavailable"]:
        lines.append(f"  {u['provider']}: {u['reason']}")
    for item in cat["left_out"]:
        if item["kind"] == "local":
            lines.append(f"  left out of {item['provider']}: {', '.join(item['models'])}")
        else:
            lines.append(f"  left out of {item['provider']} by the filters: {len(item['models'])} listed models")
    return lines


def key_lines():
    """Where each key AutoMC uses comes from (the shell, the keys file, or not set), the keys file's state and LangSmith's; never a
    value."""
    from agent import keys, tracing
    st = keys.status()
    if not st["file"]:
        lines = [f"keys file: none ({st['env']} names one, for example ~/.config/automc/keys.env)"]
    elif st["error"]:
        lines = [f"keys file: {st['error']}"]
    else:
        lines = [f"keys file: {st['file']} ({st['env']}): {len(st['used'])} variable(s) used, {st['other']} other(s) ignored"]
    if st["warning"]:
        lines.append(f"  warning: {st['warning']}")
    names = [n for n in keys.used() if n.endswith("_API_KEY")]
    lines.append("keys: " + ", ".join(f"{n} ({keys.source(n) or 'not set'})" for n in names))
    lines.append(f"LangSmith tracing: {tracing.settings()['message']}")
    return lines


def main(argv=None):
    from agent import llm
    ap = argparse.ArgumentParser(prog="python -m automc models", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)
    sub.add_parser("list")
    d = sub.add_parser("discover")
    d.add_argument("--refresh", action="store_true", help="list the cloud services again instead of using the last hour's lists")
    d.add_argument("--json", action="store_true")
    c = sub.add_parser("check")
    c.add_argument("name")
    c.add_argument("--tools", action="store_true", help="also check one tool call and its round trip")
    c.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    try:
        if a.command == "list":
            default = llm.default_name()
            for p in llm.providers():
                miss = p.missing()
                state = "ready" if not miss else "not ready: needs " + "; ".join(miss)
                mark = "*" if p.name == (default or "").split("/", 1)[0] else " "
                print(f"{mark} {p.name:16s} {p.kind:5s} {p.account:11s} {p.backend:17s} {llm.bare_model(p.model) or '-':28s} {state}")
            print(f"* the default: {llm.screen_name(default)} (AUTOMC_LLM_PROVIDER in the shell or the keys file, else default_provider "
                  f"in llm.yaml)")
            print("\n".join(key_lines()))
            return 0
        if a.command == "discover":
            from agent import discovery
            cat = discovery.catalog(refresh=a.refresh)
            if a.json:
                print(json.dumps(cat, indent=2, default=str))
            else:
                print("\n".join(picker_lines(cat, now=llm.default_name())))
                print("* the default. Start with one as shown: python -m automc chat --provider cloud-portkey/gpt-5.4; in the chat, "
                      "/model N switches.")
            return 0
        out = check(a.name, tools=a.tools)
    except llm.LLMConfigError as e:
        print(f"{e}", file=sys.stderr)
        return 4
    if a.json:
        print(json.dumps(out, indent=2, default=str))
    else:
        if out.get("describe"):
            print(out["describe"])
        if "answer" in out:
            print(f"  hello: {out['answer']!r} in {out['seconds']} s, tokens {out.get('usage') or 'not reported'}")
        if "tool_call" in out:
            print(f"  tool call: {out['tool_call']}")
        if "tool_answer" in out:
            print(f"  tool round trip: {out['tool_answer']!r}" + (f" in {out['tool_seconds']} s" if "tool_seconds" in out else ""))
        if "schemas_answer" in out:
            print(f"  hello with AutoMC's {out.get('schemas_count')} tool definitions: {out['schemas_answer']!r} in {out['schemas_seconds']} s, tokens "
                  f"{out.get('schemas_usage') or 'not reported'}")
        print("  OK" if out.get("ok") else f"  FAILED: {out.get('error')}")
    return 0 if out.get("ok") else 1
