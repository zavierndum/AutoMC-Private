"""
The tool definitions as each service accepts them, and the arguments a model sends back put into the types the tools declare
(AutoMC_Agent_Models_Plan.md, MS-4). Each tool keeps one schema (tools/registry.py); this is the only place that adapts it.

google_genai   Gemini takes part of JSON Schema. Built offline on 2026-10-27 with the 15 AutoMC tools, langchain-google-genai 4.4.0
               turned every object parameter without listed properties (spec, inserts, conditions, fuel, environment,
               tallies_options, nuclear_data, run, sweep: 33 parameters) into text without saying so, and dropped additionalProperties
               with a warning per tool. Here those parameters are declared as text holding JSON, said so in their description, and
               additionalProperties is left out; repair() parses the text back.
other backends unchanged.

repair(): a string argument where the tool's own schema expects an object, an array, a number or a boolean is parsed as JSON and kept
when the parsed value has an expected type; otherwise it goes on as it came and the tool reports the wrong type. Top-level arguments
only: the adapter changes nothing deeper. It applies to every model (a local model sometimes sends an object as text too).
"""
from __future__ import annotations

import json

TEXT_NOTE = "Write it as JSON text ({})."


def definitions():
    """Every registry tool in the OpenAI function format that bind_tools takes."""
    from tools import registry
    return [{"type": "function", "function": {"name": t["name"], "description": t["description"], "parameters": t["parameters"]}}
            for t in registry.describe()]


def _types(s):
    """The JSON types a schema allows (through anyOf too); empty when it does not say."""
    if not isinstance(s, dict):
        return set()
    if "anyOf" in s:
        return set().union(*(_types(x) for x in s["anyOf"]))
    t = s.get("type")
    return set(t) if isinstance(t, list) else ({t} if t else set())


def _bare_object(s):
    return isinstance(s, dict) and s.get("type") == "object" and not s.get("properties")


def _gemini(s):
    if not isinstance(s, dict):
        return s
    if _bare_object(s) or any(_bare_object(x) for x in s.get("anyOf", [])):
        note = TEXT_NOTE.format(" or ".join(sorted(_types(s))))
        return {"type": "string", "description": f"{s.get('description', '')} {note}".strip()}
    out = {k: v for k, v in s.items() if k != "additionalProperties"}
    if isinstance(out.get("properties"), dict):
        out["properties"] = {k: _gemini(v) for k, v in out["properties"].items()}
    if "items" in out:
        out["items"] = _gemini(out["items"])
    if "anyOf" in out:
        out["anyOf"] = [_gemini(x) for x in out["anyOf"]]
    return out


def for_backend(schemas, backend):
    """The tool definitions to bind for `backend`; the same list for every backend but google_genai."""
    if backend != "google_genai":
        return schemas
    out = []
    for t in schemas:
        params = {k: v for k, v in t["function"]["parameters"].items() if k != "additionalProperties"}
        params["properties"] = {k: _gemini(v) for k, v in (params.get("properties") or {}).items()}
        out.append({**t, "function": {**t["function"], "parameters": params}})
    return out


def _json_type(v):
    if isinstance(v, bool):
        return "boolean"
    if isinstance(v, int):
        return "integer"
    return {float: "number", dict: "object", list: "array", str: "string", type(None): "null"}.get(type(v))


def repair(params, args):
    """(args with JSON text parsed where the tool's parameters schema `params` expects another type, names of the fields parsed)."""
    props = (params or {}).get("properties") or {}
    out, fixed = dict(args), []
    for k, v in args.items():
        want = _types(props.get(k))
        if not isinstance(v, str) or not want or "string" in want:
            continue
        try:
            parsed = json.loads(v)
        except ValueError:
            continue
        got = _json_type(parsed)
        if got in want or (got == "integer" and "number" in want):
            out[k] = parsed
            fixed.append(k)
    return out, fixed
