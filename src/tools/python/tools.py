"""
The Python tool (devops/AutoMC_General_Tools_Plan.md, GT-5). PythonRunTool never runs anything: it returns the plan, and the chat shows
the user the code and asks (as OpenMCRunStartTool does for transport). The kernel, the output rules and the labels: tools/python/run.py.
"""
from __future__ import annotations

from tools.files.tools import as_tool

from . import run

RUN_SCHEMA = {"type": "object", "required": ["code"], "additionalProperties": False, "properties": {
    "code": {"type": "string", "description": "the Python code to run in this conversation's kernel (its variables, imports and files "
                                              "from earlier runs are still there)"},
    "timeout_s": {"type": "integer", "minimum": 1, "maximum": run.MAX_TIMEOUT, "description": f"default {run.DEFAULT_TIMEOUT}"},
}}


@as_tool(RUN_SCHEMA)
def python_run(args):
    plan = run.plan_run(args["code"], args.get("timeout_s"))
    return {"started": False, "needs_approval": True, "plan": plan, "facility_data": plan["facility_data"],
            "message": "The chat shows the user the code and asks; nothing runs before they say yes."}


TOOLS = [
    {"name": "PythonRunTool", "handler": python_run, "parameters": RUN_SCHEMA,
     "description": "Run Python in this conversation's own Jupyter kernel, for analysis, plots and calculations on files the tools can "
                    "reach (numpy, pandas, matplotlib, h5py and openmc's Python API are there). Variables are kept between runs. Every "
                    "run is shown to the user and runs only after they say yes. The kernel works in its own workspace folder, without "
                    "keys, with a time limit; a matplotlib figure is saved as a PNG; the whole output is saved, and a long one is "
                    "shortened for you (start and end). Never use it to run transport: OpenMCRunStartTool does that."},
]
