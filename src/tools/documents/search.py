"""
Searching knowledge bases (GT-3R): by meaning, with the embedding model each knowledge base was built with (index.py), as the retrieval
experiment chose (devops/validation/GT3R_RETRIEVAL.md); by full text when no embedding model answers. Every row comes back once, with
its knowledge base, file, kind (text or visual), page and locator; a visual (a figure, a table, an image) with its whole description,
until the result's budget is spent. The result carries the worst label of the knowledge bases its rows come from. The linked workspaces'
knowledge bases (devops/AutoMC_Workspace_Plan.md, WD-2) are searched too, read only, named LINK:KB, each with its own label.
"""
from __future__ import annotations

from tools.files import places
from tools.files.places import Refused

from . import index, parse

MAX_RESULTS = 20
KINDS = index.KINDS


def search(query, kb=None, kinds=None, max_results=8):
    if not isinstance(query, str) or not query.strip():
        raise Refused("query", "must be the question or the words to look for", "InvalidArgument", 2)
    kinds = list(kinds) if kinds else None
    bad = [k for k in kinds or [] if k not in KINDS]
    if bad:
        raise Refused("kinds", f"unknown kind {bad[0]!r}; choose from {list(KINDS)}", "InvalidArgument", 2)
    every = [(r["kb"], parse.kb_dir(r["kb"]), r) for r in parse.kbs()] + parse.linked_kbs()
    if kb:
        every = [e for e in every if e[0] == kb]
        if not every:
            raise Refused("kb", f"no knowledge base {kb!r}; DocumentCheckTool with no arguments lists them", "NotFound", 2)
    records = [{**rec, "kb": name} for name, _, rec in every]
    opened = []
    for name, folder, _ in every:
        if (folder / index.INDEX).is_file():
            opened.append((name, index.Index(folder)))
    if not opened:
        making = [r["kb"] for r in records if r.get("status") == "ingesting"]
        return {"query": query, "results": [], "searched": [], "facility_data": "public",
                "note": (f"the knowledge base {', '.join(making)} is still being made; search when its run ends (DocumentCheckTool)"
                         if making else "no knowledge base yet; DocumentParseTool makes one from a folder (the user approves)")}
    try:
        rows, notes = index.search(opened, query, kinds, max(1, min(int(max_results or 8), MAX_RESULTS)))
    finally:
        for _, idx in opened:
            idx.close()
    label = {r["kb"]: r.get("label") or "unknown" for r in records}
    out = {"query": query, "results": rows, "searched": [n for n, _ in opened],
           "facility_data": places.worst([label[r["kb"]] for r in rows]) if rows else "public"}
    if notes:
        out["notes"] = notes
    if not rows:
        out["note"] = "nothing found; try other words, or DocumentCheckTool to see what the knowledge bases hold"
    else:
        out["next"] = "answer from these rows and cite the file and page; a visual's text is the vision model's description of it"
    return out
