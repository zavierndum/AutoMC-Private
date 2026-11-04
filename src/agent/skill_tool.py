"""
SkillLookupTool: the facility packs' skills, fetched when a question needs them instead of being loaded at the start of every
conversation (decision D2 of AutoMC_Agent_Models_Plan.md). A fetched skill is a tool result with its pack's label, so a restricted skill
enters the conversation (and marks it restricted) only when it is used, and a cloud model never receives it (agent/guard.py).
The bundled skills (src/skills) are public and stay in the system prompt. Their other files, the working examples above all (README.md,
build.py, run.py, post.py of each), are not in the prompt: the tool reads one by its path under src/skills (`file`), and a folder lists
its files, so the agent can open the example nearest to the user's problem before it writes a build script (devops/AutoMC_Prompt_Plan.md,
PR-3). Nothing outside src/skills is reachable this way.
"""
from __future__ import annotations

import functools
from pathlib import PurePosixPath

MAX_CHARS = 30_000             # the largest example file is about 15,000; a longer file says how to read the rest

SCHEMA = {"type": "object", "additionalProperties": False, "required": [], "properties": {
    "name": {"type": "string", "description": "a facility pack's skill by its name; leave out name and file to list them"},
    "file": {"type": "string", "description": "a bundled skill file or folder by its path under the skills folder, for example "
                                              "working_examples/index.md, working_examples/jezebel (lists its files) or "
                                              "working_examples/jezebel/build.py"}}}


def _as_tool(fn):
    @functools.wraps(fn)
    def handler(args=None):
        args = args if args is not None else {}
        if not isinstance(args, dict) or set(args) - set(SCHEMA["properties"]):
            bad = sorted(set(args) - set(SCHEMA["properties"])) if isinstance(args, dict) else ["arguments"]
            return {"ok": False, "error": {"type": "InvalidArgument", "field": bad[0], "exit_code": 2,
                                           "message": f"{bad[0]}: unknown argument; allowed: {sorted(SCHEMA['properties'])}"}}
        try:
            return {"ok": True, **fn(args)}
        except LookupError as e:
            field = "file" if args.get("file") else "name"
            return {"ok": False, "error": {"type": "NotFound", "field": field, "message": str(e), "exit_code": 2}}
        except ValueError as e:
            return {"ok": False, "error": {"type": "InvalidArgument", "field": str(e).split(":")[0], "message": str(e), "exit_code": 2}}
    return handler


def _worst(labels):
    return "restricted" if "restricted" in labels else ("public" if labels else "public")


def _bundled(rel):
    """A file or folder under src/skills by its relative path; anything that would leave the folder is refused."""
    from agent.prompt import SKILLS_DIR
    rel = rel.strip().replace("\\", "/").strip("/")
    parts = PurePosixPath(rel).parts
    if not rel or rel.startswith("~") or ":" in parts[0] or ".." in parts or "__pycache__" in parts:
        raise ValueError(f"file: {rel!r} must be a path under the skills folder, such as working_examples/jezebel/README.md")
    root = SKILLS_DIR.resolve()
    p = (root / rel).resolve()
    if p != root and root not in p.parents:
        raise ValueError(f"file: {rel!r} is outside the skills folder")
    if not p.exists():
        near = sorted(q.name for q in p.parent.iterdir()) if p.parent.is_dir() and root in (p.parent, *p.parent.parents) else []
        raise LookupError(f"no bundled skill file {rel!r}" + (f"; that folder holds {near}" if near else
                                                              "; working_examples/index.md lists the examples"))
    if p.is_dir():
        files = sorted(q.relative_to(p).as_posix() for q in p.rglob("*") if q.is_file() and "__pycache__" not in q.parts)
        return {"file": rel, "folder": True, "files": files, "facility_data": {"distribution": "public"}}
    try:
        text = p.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raise ValueError(f"file: {rel!r} is not a text file") from None
    out = {"file": rel, "text": text[:MAX_CHARS], "chars": len(text), "facility_data": {"distribution": "public"}}
    if len(text) > MAX_CHARS:
        out["truncated"] = f"the first {MAX_CHARS} of {len(text)} characters; FileReadTool reads the rest (src/skills/{rel})"
    return out


@_as_tool
def skill_lookup(args):
    from agent.prompt import pack_skills
    # empty means not given: a service that fills every argument (OpenAI's strict tool calls) sends name "" beside a file, file "" beside a name
    if args.get("file") is not None and not isinstance(args["file"], str):
        raise ValueError("file: expected a path under the skills folder")
    if (args.get("name") or "").strip() and (args.get("file") or "").strip():
        raise ValueError("file: give name (a facility pack's skill) or file (a bundled file), not both")
    if (args.get("file") or "").strip():
        return _bundled(args["file"])
    skills, _ = pack_skills(cloud=False)
    if not args.get("name"):
        return {"skills": [{"name": s.name, "description": s.description, "pack": s.source, "distribution": s.label} for s in skills],
                "bundled": "the bundled skills are in your prompt; read their other files with file, starting at working_examples/index.md",
                "facility_data": {"distribution": _worst({s.label for s in skills})}}
    match = [s for s in skills if s.name.lower() == args["name"].strip().lower()]
    if not match:
        raise LookupError(f"no facility-pack skill {args['name']!r}; skills: {[s.name for s in skills]}")
    s = match[0]
    return {"name": s.name, "description": s.description, "pack": s.source, "text": s.text, "facility_data": {"distribution": s.label}}


TOOLS = [
    {"name": "SkillLookupTool", "handler": skill_lookup, "parameters": SCHEMA,
     "description": "Read what the prompt does not hold. With name, a facility pack's skill (leave out name and file to list them): what "
                    "a facility pack knows that the bundled skills do not, for example a reference model's known bias; a skill from a "
                    "restricted pack is restricted data. With file, a bundled file by its path under the skills folder: the working "
                    "examples (working_examples/index.md lists them; each folder has README.md, build.py, post.py and sometimes run.py), "
                    "and a folder lists its files. Read the nearest working example before writing a build script."},
]
