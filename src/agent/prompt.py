"""
The agent's system prompt: prompts/system_prompt_automc.yml (sections and examples), then the bundled skills, then a runtime block
written by code.

The prompt file has a closed schema, checked on every load: a keyed mapping of sections, each with a title, the channels it is for
(`local`, a model on this machine; `cloud`, one outside it) and text, items or subsections; and a list of examples (few-shots), each
with the user's message, the tools a good answer calls, the answer, what it teaches, and the phrases its answer must and must not hold
(checked by tests against the answer itself, never rendered). An unknown key, a wrong type or a key given twice stops the load: plain
YAML keeps the second of two equal keys and drops the first without a word. A provider sees the sections and examples of its channel,
in file order.

The bundled skills (src/skills, public, checked against MANIFEST.json) are in the prompt. The facility packs' skills (`provides:
skills`) are not: the runtime block lists them by name and the model fetches one with SkillLookupTool when a question needs it, so a
restricted skill enters a conversation only when used, with its label (agent/skill_tool.py). A cloud model is not told about restricted
skills at all. Nothing is templated: text reaches the model exactly as written.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

import automc_paths

PROMPT_PATH = automc_paths.SRC / "prompts" / "system_prompt_automc.yml"
SKILLS_DIR = automc_paths.SRC / "skills"
MANIFEST = "MANIFEST.json"
_FRONT = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)

CHANNELS = ("local", "cloud")
_TOP_KEYS = {"sections", "few_shots"}
_SECTION_KEYS = {"title", "channels", "text", "items", "subsections"}
_SUB_KEYS = {"title", "text", "items"}
_SHOT_KEYS = {"id", "title", "channels", "user", "tools", "answer", "teaches", "must_include", "must_not_include"}
EXAMPLES_NOTE = ("Each example below happened: its numbers came from tool calls made when this prompt was written. Never reuse them; "
                 "every number in an answer comes from a tool call made for that answer.")


class PromptError(Exception):
    pass


@dataclass(frozen=True)
class Subsection:
    title: str
    text: str
    items: tuple


@dataclass(frozen=True)
class Section:
    id: str
    title: str
    channels: tuple
    text: str
    items: tuple
    subsections: tuple


@dataclass(frozen=True)
class FewShot:
    id: str
    title: str
    channels: tuple
    user: str
    tools: tuple
    answer: str
    teaches: str
    must_include: tuple            # each entry a tuple of alternatives, one of which the answer holds
    must_not_include: tuple


@dataclass(frozen=True)
class PromptDoc:
    sections: tuple
    few_shots: tuple


@dataclass
class Skill:
    name: str
    description: str
    source: str            # "bundled" or the pack id
    label: str             # public or restricted
    path: str              # relative to its root
    text: str


def _keys(node, allowed, where):
    if not isinstance(node, dict):
        raise PromptError(f"{where}: expected a mapping")
    unknown = sorted(set(node) - allowed)
    if unknown:
        raise PromptError(f"{where}: unknown key(s) {unknown}; allowed {sorted(allowed)}")


def _text(node, where, required=True):
    if node is None and not required:
        return ""
    if not isinstance(node, str) or not node.strip():
        raise PromptError(f"{where}: expected non-empty text")
    return node.strip()


def _texts(node, where, required=False):
    """A list of non-empty strings. An item holding `key: value` unquoted reads as a mapping, so it is refused here, by name."""
    if node is None and not required:
        return ()
    if not isinstance(node, list) or (required and not node) or not all(isinstance(x, str) and x.strip() for x in node):
        raise PromptError(f"{where}: expected a list of non-empty strings (quote an item that holds a colon and a space)")
    return tuple(x.strip() for x in node)


def _channels(node, where):
    got = _texts(node, where, required=True)
    if set(got) - set(CHANNELS):
        raise PromptError(f"{where}: {list(got)} must be a non-empty subset of {list(CHANNELS)}")
    return got


def _body(node, where):
    """text and items of a section or subsection: at least one of them."""
    text, items = _text(node.get("text"), f"{where}.text", required=False), _texts(node.get("items"), f"{where}.items")
    return text, items


def _section(sid, node):
    where = f"sections.{sid}"
    _keys(node, _SECTION_KEYS, where)
    text, items = _body(node, where)
    subs = []
    for i, sub in enumerate(node.get("subsections") or []):
        swhere = f"{where}.subsections[{i}]"
        _keys(sub, _SUB_KEYS, swhere)
        stext, sitems = _body(sub, swhere)
        if not (stext or sitems):
            raise PromptError(f"{swhere}: needs text or items")
        subs.append(Subsection(title=_text(sub.get("title"), f"{swhere}.title"), text=stext, items=sitems))
    if not (text or items or subs):
        raise PromptError(f"{where}: needs text, items or subsections")
    return Section(id=sid, title=_text(node.get("title"), f"{where}.title"), channels=_channels(node.get("channels"), f"{where}.channels"),
                   text=text, items=items, subsections=tuple(subs))


def _phrases(node, where):
    """must_include: each entry a phrase or a list of alternative phrases."""
    out = []
    for i, x in enumerate(node or []):
        alts = (x,) if isinstance(x, str) else _texts(x, f"{where}[{i}]", required=True) if isinstance(x, list) else None
        if not alts or not all(a.strip() for a in alts):
            raise PromptError(f"{where}[{i}]: expected a phrase or a list of alternative phrases")
        out.append(tuple(a.strip() for a in alts))
    return tuple(out)


def _few_shot(i, node):
    where = f"few_shots[{i}]"
    _keys(node, _SHOT_KEYS, where)
    return FewShot(id=_text(node.get("id"), f"{where}.id"), title=_text(node.get("title"), f"{where}.title"),
                   channels=_channels(node.get("channels"), f"{where}.channels"), user=_text(node.get("user"), f"{where}.user"),
                   tools=_texts(node.get("tools"), f"{where}.tools"), answer=_text(node.get("answer"), f"{where}.answer"),
                   teaches=_text(node.get("teaches"), f"{where}.teaches"),
                   must_include=_phrases(node.get("must_include"), f"{where}.must_include"),
                   must_not_include=_texts(node.get("must_not_include"), f"{where}.must_not_include"))


def load_prompt(path=None):
    """The checked prompt file as a PromptDoc. Raises PromptError naming the node at fault."""
    import yaml
    import automc_settings
    path = Path(path or PROMPT_PATH)
    try:
        raw = yaml.load(path.read_text(encoding="utf-8"), Loader=automc_settings.unique_keys_loader())
    except yaml.YAMLError as e:
        raise PromptError(f"{path.name}: {e}") from None
    _keys(raw, _TOP_KEYS, path.name)
    if not isinstance(raw.get("sections"), dict) or not raw["sections"]:
        raise PromptError(f"{path.name}: sections must be a non-empty mapping of id to section (the prompt's version is the package's, "
                          f"automc_version.py)")
    if not isinstance(raw.get("few_shots") or [], list):
        raise PromptError(f"{path.name}: few_shots must be a list")
    shots = tuple(_few_shot(i, n) for i, n in enumerate(raw.get("few_shots") or []))
    ids = [f.id for f in shots]
    if len(set(ids)) != len(ids):
        raise PromptError(f"{path.name}: few_shots ids must be unique, got {ids}")
    return PromptDoc(sections=tuple(_section(str(k), v) for k, v in raw["sections"].items()), few_shots=shots)


def _bullets(items):
    return "\n".join("- " + x.replace("\n", "\n  ") for x in items)


def _render_section(s):
    blocks = [f"## {s.title}"] + [b for b in (s.text, _bullets(s.items)) if b]
    for sub in s.subsections:
        blocks += [f"### {sub.title}"] + [b for b in (sub.text, _bullets(sub.items)) if b]
    return "\n\n".join(blocks)


def _render_shot(n, f):
    return (f"### Example {n}: {f.title}\nUser: {f.user}\nTools a good answer calls: {', '.join(f.tools) if f.tools else 'none'}\n"
            f"A good answer:\n{f.answer}\nWhat this example shows: {f.teaches}")


def render(doc, cloud):
    """The sections and examples of the provider's channel, in file order, each string exactly as authored."""
    channel = "cloud" if cloud else "local"
    parts = [_render_section(s) for s in doc.sections if channel in s.channels]
    shots = [f for f in doc.few_shots if channel in f.channels]
    if shots:
        parts.append("## Examples\n\n" + EXAMPLES_NOTE)
        parts += [_render_shot(i + 1, f) for i, f in enumerate(shots)]
    return "\n\n".join(parts)


def _skill_from(path, root, source, label):
    text = path.read_text(encoding="utf-8")
    m = _FRONT.match(text)
    meta = {}
    if m:
        import yaml
        meta = yaml.safe_load(m.group(1)) or {}
        text = text[m.end():]
    if not meta.get("name") or not meta.get("description"):
        raise PromptError(f"{path} needs front matter with name and description")
    return Skill(name=meta["name"], description=meta["description"], source=source, label=label,
                 path=path.relative_to(root).as_posix(), text=text.strip())


def bundled_skills(root=None):
    root = Path(root or SKILLS_DIR)
    core = root / "core" / "SKILL.md"
    others = sorted(p for p in root.rglob("SKILL.md") if p != core)
    return [_skill_from(p, root, "bundled", "public") for p in ([core] if core.is_file() else []) + others]


def pack_skills(cloud):
    """Skills from the installed facility packs; a restricted pack's only for a local model. Returns (skills, skipped pack ids)."""
    from tools.reactors.triga import packs
    out, skipped = [], []
    for p in packs.installed().packs:
        folder = p.skills_folder()
        if folder is None or not folder.is_dir():
            continue
        if cloud and p.distribution != "public":
            skipped.append(p.id)
            continue
        out += [_skill_from(f, folder, p.id, p.distribution) for f in sorted(folder.rglob("SKILL.md"))]
    return out, skipped


def _manifest_files(root):
    return sorted(p for p in Path(root).rglob("*") if p.is_file() and p.name != MANIFEST and "__pycache__" not in p.parts)


def build_manifest(root=None):
    root = Path(root or SKILLS_DIR)
    files = {p.relative_to(root).as_posix(): "sha256:" + hashlib.sha256(p.read_bytes()).hexdigest() for p in _manifest_files(root)}
    data = {"version": 1, "generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"), "file_count": len(files),
            "files": files}
    (root / MANIFEST).write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return data


def check_manifest(root=None):
    """Differences between the bundled skills and MANIFEST.json: [(file, "changed" | "new" | "missing")]."""
    root = Path(root or SKILLS_DIR)
    try:
        listed = json.loads((root / MANIFEST).read_text(encoding="utf-8"))["files"]
    except (OSError, ValueError, KeyError):
        return [(MANIFEST, "missing")]
    out = []
    present = {p.relative_to(root).as_posix(): p for p in _manifest_files(root)}
    for rel, p in present.items():
        if rel not in listed:
            out.append((rel, "new"))
        elif listed[rel] != "sha256:" + hashlib.sha256(p.read_bytes()).hexdigest():
            out.append((rel, "changed"))
    out += [(rel, "missing") for rel in listed if rel not in present]
    return out


def runtime_block(provider, pack_skill_list=()):
    """Facts of this session, written by code: the date, the model, the facility packs, their skills, the active jobs."""
    lines = [f"Date (UTC): {_dt.datetime.now(_dt.timezone.utc).date().isoformat()}",
             f"Model: {provider.label} ({provider.kind}: " +("it runs on this machine and may see restricted facility data)" if not
                                                             provider.cloud else "it runs outside this machine; restricted results are "
                                                                                 "withheld from it)")]
    try:
        from tools.reactors.triga import packs, presets
        reg = packs.installed()
        lines.append("Facility packs: " + ", ".join(f"{p.id} ({p.distribution})" for p in reg.packs))
        lines.append(f"Default TRIGA preset (used when a request names none): {presets.default_preset('netl')}")
        lines.append("TRIGA presets: " + "; ".join(f"{d['preset']} ({d['distribution']})" for d in presets.describe()))
        if pack_skill_list:
            lines.append("Facility-pack skills, read one with SkillLookupTool when a question concerns it: " + "; ".join(
                f"{s.name} (pack {s.source}, {s.label}): {s.description}" for s in pack_skill_list))
    except Exception as e:
        lines.append(f"Facility packs: could not be read ({type(e).__name__}: {e})")
    try:
        from tools.documents import parse as doc_parse
        from tools.files import places
        import automc_settings
        restricted = set(places.restricted_dirs())
        facility = automc_settings.facility_mode()
        lines.append(f"Workspace: {Path(automc_paths.WORKSPACE).resolve()} (relative paths start here; cases, runs and knowledge bases "
                     f"live here; a case may sit in subfolders, such as study-8/c8)")
        lines.append(f"AutoMC: {automc_paths.AUTOMC_ROOT.resolve()} (its code, skills and public pack are in src/)")
        lines.append("Data mode: " + ("facility (something restricted is declared: only public results reach a cloud model or the web)"
                                      if facility else "open (nothing restricted is declared)"))
        lines.append("Folders the file tools may read, with their labels: " + "; ".join(
            f"{r} ({places.label(r)[0] or 'unknown'})" for r in places.read_roots() if not (provider.cloud and r in restricted)))
        kbs = doc_parse.kbs() + [{**rec, "kb": name} for name, _, rec in doc_parse.linked_kbs()]
        shown = [k for k in kbs if not provider.cloud or k.get("label") == "public"]
        if kbs:
            lines.append("Knowledge bases (search them with DocumentSearchTool): " + "; ".join(
                f"{k['kb']} ({k.get('label')}, {len(k.get('files') or {})} files)" for k in shown)
                + (f"; {len(kbs) - len(shown)} more that a cloud model may not read" if len(shown) < len(kbs) else ""))
        else:
            lines.append("Knowledge bases: none yet (DocumentParseTool makes one from a folder of PDFs and images)")
    except Exception as e:
        lines.append(f"Folders and knowledge bases: could not be read ({type(e).__name__}: {e})")
    try:
        from tools.openmc import runs
        active = runs.list_jobs(active_only=True)
        lines.append(f"Active jobs: {len(active)}" + ("" if provider.cloud or not active else
                     " (" + "; ".join(f"{j['job_id']} {j['case']} {j['status']}" for j in active) + ")"))
    except Exception:
        pass
    return "## This session\n" + "\n".join(f"- {l}" for l in lines)


def _memory(provider):
    """The workspace's AUTOMC.md and memory index, as much as this model may see (tools/memory/store.py)."""
    try:
        import automc_settings
        from tools.memory import store
        return store.prompt_block(provider.cloud, automc_settings.facility_mode())
    except Exception as e:                            # a broken note never stops a conversation
        return f"## Memory\n(could not be read: {type(e).__name__})", {"error": str(e)[:200]}


def system_prompt(provider):
    """(text, info) for a provider. info: AutoMC's version and the prompt and skills fingerprint, the skills loaded, the packs whose
    skills were left out, and manifest differences."""
    import automc_version
    doc = load_prompt()
    skills = bundled_skills()
    available, skipped = pack_skills(provider.cloud)            # listed for the model, read on demand
    parts = [f"# AutoMC {automc_version.VERSION}", render(doc, provider.cloud), "# Skills"]
    for s in skills:
        origin = "" if s.source == "bundled" else f" (from facility pack {s.source}, {s.label})"
        parts.append(f"## Skill: {s.name}{origin}\n{s.description}\n\n{s.text}")
    # the error knowledge base is matched in code (agent/erkb.py: known_errors in failed results), not carried in every prompt
    memory_text, memory_info = _memory(provider)
    if memory_text:
        parts.append("# This workspace\n\n" + memory_text)
    parts.append(runtime_block(provider, available))
    info = {"version": automc_version.VERSION, "fingerprint": automc_version.fingerprint(), "memory": memory_info,
            "skills": [f"{s.name} ({s.source})" for s in skills],
            "pack_skills_on_demand": [f"{s.name} ({s.source}, {s.label})" for s in available], "skipped_packs": skipped,
            "manifest": check_manifest()}
    return "\n\n".join(parts) + "\n", info
