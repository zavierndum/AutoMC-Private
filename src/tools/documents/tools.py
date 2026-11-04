"""
The document tools (devops/AutoMC_General_Tools_Plan.md, GT-3R): knowledge bases made from folders of PDFs and images with
Visual-Parser, embedded and searched. Same shape as the other tools: a dict of arguments in, a JSON-serializable dict out,
{"ok": false, "error": {...}} instead of raising.

DocumentParseTool never parses: it returns the plan, and the chat asks the user (as OpenMCRunStartTool does for transport); a folder
with nothing new comes back at once with its knowledge base instead. The run, its labels and the vision model:
tools/documents/parse.py; the index and the search: tools/documents/index.py and search.py.
"""
from __future__ import annotations

from tools.files.tools import as_tool

from . import parse, search

PARSE_SCHEMA = {"type": "object", "required": ["folder"], "additionalProperties": False, "properties": {
    "folder": {"type": "string", "description": "the folder of PDFs and images (PNG, JPG, TIFF, BMP), subfolders included: relative to "
                                                "the workspace, or absolute. For one file, give the folder that holds it"},
    "name": {"type": "string", "description": "the knowledge base's name (default: the folder's)"},
    "text_mode": {"type": "string", "enum": list(parse.TEXT_MODES),
                  "description": "a new knowledge base only. nougat (default): reads equations and scanned pages, uses the GPU; "
                                 "lightweight: the PDFs' own text layer, fast, no equations"},
    "redo": {"type": "array", "items": {"type": "string"},
             "description": "file names to parse again, when the user asks (a plan lists the files changed since their parse)"},
}}
CHECK_SCHEMA = {"type": "object", "required": [], "additionalProperties": False, "properties": {
    "job_id": {"type": "string", "description": "a run, as DocumentParseTool's start returned it"},
    "kb": {"type": "string", "description": "a knowledge base's name"},
}}
SEARCH_SCHEMA = {"type": "object", "required": ["query"], "additionalProperties": False, "properties": {
    "query": {"type": "string", "description": "the user's question, or what to look for, in plain words"},
    "kb": {"type": "string", "description": "only this knowledge base (default: all of them)"},
    "kinds": {"type": "array", "items": {"type": "string", "enum": list(search.KINDS)},
              "description": "only these: text (text chunks), visual (descriptions of figures, tables and images)"},
    "max_results": {"type": "integer", "minimum": 1, "maximum": search.MAX_RESULTS, "description": "default 8"},
}}
CANCEL_SCHEMA = {"type": "object", "required": ["job_id"], "additionalProperties": False, "properties": {
    "job_id": {"type": "string", "description": "the run to stop"},
}}


@as_tool(PARSE_SCHEMA)
def document_parse(args):
    plan = parse.plan_parse(args["folder"], args.get("name"), args.get("text_mode"), args.get("redo"))
    if plan["nothing_to_do"]:
        out = {"started": False, "nothing_to_do": True, "facility_data": plan["label"],
               "message": "Nothing new to parse or embed in this folder."}
        if parse.record(plan["kb"]):
            out["kb"] = parse.summary(plan["kb"])
        if plan["changed"]:
            out["changed"] = [{"name": f["name"], "pages": f.get("pages"), "drawing_only": f.get("drawing_only")} for f in plan["changed"]]
            out["message"] += " Some files changed since their parse; give their names in redo if the user wants them parsed again."
        return out
    return {"started": False, "needs_approval": True, "plan": plan, "facility_data": plan["facility_data"],
            "message": "The chat asks the user to approve this run; nothing runs before they say yes."}


@as_tool(CHECK_SCHEMA)
def document_check(args):
    return parse.check(job_id=args.get("job_id"), kb=args.get("kb"))


@as_tool(SEARCH_SCHEMA)
def document_search(args):
    return search.search(args["query"], kb=args.get("kb"), kinds=args.get("kinds"), max_results=args.get("max_results") or 8)


@as_tool(CANCEL_SCHEMA)
def document_cancel(args):
    return parse.cancel(args["job_id"])


TOOLS = [
    {"name": "DocumentParseTool", "handler": document_parse, "parameters": PARSE_SCHEMA,
     "description": "Make or update a knowledge base from a folder of PDFs and images with Visual-Parser: the text in chunks with page "
                    "numbers (Nougat reads equations), a vision model's description of every figure, table and image, the title and "
                    "authors; then everything is embedded on this machine for search. Only files not parsed before are parsed, so run it "
                    "again when the user adds files. Returns a plan, and the chat asks the user to approve it; the run then goes on in "
                    "the background (minutes). A public folder may use the chat model's cloud service for the page images; any other "
                    "stays on this machine. Use it when the user points at reports, papers or drawings."},
    {"name": "DocumentCheckTool", "handler": document_check, "parameters": CHECK_SCHEMA,
     "description": "A run's state (job_id), a knowledge base's summary (kb: its folder, files, documents with titles and authors, what "
                    "is embedded, its last run), or, with no arguments, every knowledge base and the run going on now. Returns at once."},
    {"name": "DocumentSearchTool", "handler": document_search, "parameters": SEARCH_SCHEMA,
     "description": "Search the knowledge bases with the user's question: the rows closest in meaning (text chunks and descriptions of "
                    "figures, tables and images), each with its file and page. Answer from these rows and cite the file and page; say "
                    "so when they do not hold the answer."},
    {"name": "DocumentCancelTool", "handler": document_cancel, "parameters": CANCEL_SCHEMA,
     "description": "Stop a run now. What it wrote stays; the next run of the folder parses what this one did not finish (and asks the "
                    "user again)."},
]
