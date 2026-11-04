"""
What the model reads of a tool result. Some results are long (a build result repeats the whole spec, the library provenance and every
file checksum); the model needs the facts, not the echo, and on a slow local model every token read costs time. A view keeps the
answer, the numbers, the errors, the warnings, the assumptions and the facility label, and drops the rest. Tools without a view are
shown whole. The data guard always checks the full result, never the view.
"""
from __future__ import annotations


def _assumptions(items):
    return [f"{a.get('id')} {a.get('title')} ({a.get('status')})" for a in items or []]


def _label(fd):
    return fd.get("distribution") if isinstance(fd, dict) else fd


def build(result):
    r = result.get("result") or {}
    s = r.get("summary") or {}
    return {"ok": True, "case": r.get("name"), "reused": r.get("reused"), "preset": r.get("preset"), "spec_hash": (r.get("spec_hash") or "")[:12],
            "library_profile": (r.get("profile") or {}).get("name"), "facility_data": _label(r.get("facility_data")),
            "model": {k: s.get(k) for k in ("cells", "materials", "tallies")}, "inserts": (s.get("inserts") or {}).get("described"),
            "rods": {k: {u: v.get(u) for u in ("fraction", "console")} for k, v in (s.get("rods") or {}).items()},
            "fuel": s.get("fuel"), "run": s.get("settings"), "run_estimate": (s.get("run_estimate") or {}).get("text"),
            "assumptions": _assumptions(r.get("assumptions")), "warnings": r.get("warnings"),
            "next": "start it with OpenMCRunStartTool (the user approves), or preview it with TRIGAInputPreviewTool"}


def spec(result):
    sp = result.get("spec") or {}
    keep = {k: sp.get(k) for k in ("name", "preset", "config", "pnt", "three_el", "inserts", "conditions", "fuel", "environment", "tallies", "run")}
    return {"ok": True, "spec": keep, "spec_hash": (result.get("spec_hash") or "")[:12], "rods_all_units": result.get("rods_all_units"),
            "run_estimate": (result.get("run_estimate") or {}).get("text"), "warnings": result.get("warnings"),
            "assumptions": _assumptions(result.get("assumptions")), "facility_data": _label(result.get("facility_data")),
            "buildable": result.get("buildable"), "not_buildable_because": result.get("not_buildable_because")}


def sweep(result):
    m = result.get("sweep") or {}
    return {"ok": True, "sweep": m.get("name"), "varied": m.get("varied"), "seed": m.get("seed"), "facility_data": _label(result.get("facility_data")),
            "states": [{"folder": st.get("folder"), "label": st.get("label"), "reused": st.get("reused")} for st in m.get("states", [])],
            "run_estimate": (m.get("estimate") or {}).get("text"),
            "next": "run it with OpenMCRunStartTool and sweep set to this name (the user approves)"}


def assumptions(result):
    return {"ok": True, "facility_data": _label(result.get("facility_data")),
            "assumptions": [{k: a.get(k) for k in ("id", "title", "statement", "fields", "confirm_with", "status")}
                            for a in result.get("assumptions", [])]}


def plan(result):
    p = result.get("plan") or {}
    return {"ok": True, "started": False, "needs_approval": True, "plan_text": p.get("text"), "facility_data": p.get("facility_data"),
            "message": result.get("message")}


VIEWS = {"TRIGAInputBuilderTool": build, "TRIGAInputSpecTool": spec, "TRIGASweepBuilderTool": sweep, "TRIGAAssumptionsTool": assumptions,
         "OpenMCRunStartTool": plan, "DocumentParseTool": plan, "PythonRunTool": plan, "WebSearchTool": plan, "WebFetchTool": plan,
         "MemoryWriteTool": plan, "OpenMCModelBuildTool": plan}
PLANNERS = ("OpenMCRunStartTool", "DocumentParseTool", "PythonRunTool", "WebSearchTool", "WebFetchTool", "MemoryWriteTool",
            "OpenMCModelBuildTool")


def view(tool_name, result):
    """The part of a tool result the model reads. Errors and results of tools without a view are shown whole."""
    if not isinstance(result, dict) or not result.get("ok"):
        return result
    fn = VIEWS.get(tool_name)
    if fn is None:
        return result
    if tool_name in PLANNERS and not result.get("needs_approval"):
        return result
    return fn(result)
