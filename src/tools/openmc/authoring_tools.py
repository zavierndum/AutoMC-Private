"""
The authoring tools (devops/AutoMC_Authoring_Plan.md, AU-1): build any OpenMC model from a Python script the agent writes, plot its
geometry for the user to inspect, and check it before a run. OpenMCModelBuildTool only plans: the chat shows the script and asks (it runs
code). Plotting and checking run OpenMC in plot mode and read the model: no transport, no user code, no question. The rules the agent
writes by are in the system prompt (its "OpenMC essentials" and "Writing a model" sections), the working examples in
skills/working_examples, which SkillLookupTool reads by path.
"""
from __future__ import annotations

from . import authoring
from .tools import _check, as_tool

BUILD_SCHEMA = {"type": "object", "required": ["case", "script"], "additionalProperties": False, "properties": {
    "case": {"type": "string", "description": "the case folder in the workspace, up to four folders deep, for example studies/water-sphere"},
    "script": {"type": "string", "description": "the whole build.py: the OpenMC Python API (materials, geometry, settings with the "
                                                "source, tallies), ending with model.export_to_model_xml(); it must not run transport"},
    "overwrite": {"type": "boolean", "description": "replace an existing case that has no runs (default false)"},
    "timeout_s": {"type": "integer", "minimum": 1, "maximum": authoring.MAX_TIMEOUT, "description": f"default {authoring.DEFAULT_TIMEOUT}"},
}}
VIEW = {"type": "object", "additionalProperties": False, "properties": {
    "basis": {"type": "string", "enum": ["xy", "xz", "yz"]},
    "origin": {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3, "description": "the slice's centre (cm)"},
    "width": {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2, "description": "width and height (cm)"},
}}
PLOT_SCHEMA = {"type": "object", "required": ["case"], "additionalProperties": False, "properties": {
    "case": {"type": "string", "description": "a case folder in the workspace that holds model.xml"},
    "views": {"type": "array", "items": VIEW, "maxItems": 8,
              "description": "slices to draw (default: xy, xz and yz through the middle of the geometry, all of it in view)"},
    "pixels": {"type": "integer", "minimum": 100, "maximum": 3000, "description": "pixels across (default 800)"},
    "color_by": {"type": "string", "enum": ["material", "cell"], "description": "default material"},
}}
CHECK_SCHEMA = {"type": "object", "required": ["case"], "additionalProperties": False, "properties": {
    "case": {"type": "string", "description": "a case folder in the workspace that holds model.xml"},
}}


@as_tool
def model_build(args):
    a = _check(args, BUILD_SCHEMA)
    plan = authoring.plan_build(a["case"], a["script"], bool(a.get("overwrite", False)), a.get("timeout_s"))
    return {"started": False, "needs_approval": True, "plan": plan, "facility_data": "public",
            "message": "The chat shows the user the script and asks; nothing is built before they say yes."}


@as_tool
def geometry_plot(args):
    a = _check(args, PLOT_SCHEMA)
    return authoring.plot_case(a["case"], a.get("views"), a.get("pixels", 800), a.get("color_by", "material"))


@as_tool
def model_check(args):
    a = _check(args, CHECK_SCHEMA)
    return authoring.check_case(a["case"])


TOOLS = [
    {"name": "OpenMCModelBuildTool", "handler": model_build, "parameters": BUILD_SCHEMA,
     "description": "Build any OpenMC model from a Python script you write (build.py, the OpenMC Python API, ending with "
                    "model.export_to_model_xml()). The user sees the script and decides; it then runs in its own process, without "
                    "keys, and the case folder gets model.xml, build.py and result.json with the model's label. A script that runs "
                    "transport or other programs is refused: runs go through OpenMCRunStartTool. Read the nearest working example "
                    "first (SkillLookupTool with file, from working_examples/index.md). For the NETL TRIGA, use the TRIGA tools instead."},
    {"name": "OpenMCGeometryPlotTool", "handler": geometry_plot, "parameters": PLOT_SCHEMA,
     "description": "Draw slices of a case's geometry as PNG files in its plots/ folder (OpenMC's plot mode: no transport), coloured by "
                    "material or cell, with overlapping cells drawn red, so the user can inspect the model before any run. Tell the "
                    "user where the pictures are."},
    {"name": "OpenMCModelCheckTool", "handler": model_check, "parameters": CHECK_SCHEMA,
     "description": "Check a case before a run, without transport: the model reads back, every nuclide is in the nuclear data, the "
                    "settings fit the run mode, there is a vacuum boundary, the source lies inside the geometry, no cells overlap "
                    "(OpenMC's plot mode on slices through the middle and the source). Each finding has a severity and a fix."},
]
