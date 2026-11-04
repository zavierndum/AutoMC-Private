"""
The workspace's memory (devops/AutoMC_Workspace_Plan.md, section 6, WD-4), like Claude Code's CLAUDE.md and memory, kept small and
labelled:

  - AUTOMC.md, at the workspace root, written by the user: what the project is and how to work in it ("configuration 8 only", "16
    threads"). Every conversation's system prompt holds it. In facility mode its first line may say `label: public`; otherwise it counts
    as unknown origin and a cloud model is not given it (it is told the file exists).
  - notes, in .automc/memory/: one small file per fact (front matter: name, title, label, created, updated, chat), and MEMORY.md, the
    index, one line per note. The agent proposes a note with MemoryWriteTool and the user approves it (as a run); the user writes one
    with /remember. A note's label is the conversation's label when it was written, set here, never by the model; only the user can
    mark a note public (/memory public NAME). A note with the same title replaces the old one rather than adding a second.
  - the system prompt gets AUTOMC.md and the index lines the model may see (a cloud model: public ones only); a note is read whole with
    MemoryReadTool, which carries its label to the data guard.

Size: AUTOMC.md and the index together stay under about 2,000 tokens (MAX_AUTOMC_MD, MAX_INDEX characters), because the 32k local model
has about 21,000 tokens left after the prompt; past that the prompt says what was left out. Standard library only.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import automc_paths

FOLDER = "memory"
INDEX = "MEMORY.md"
AUTOMC_MD = "AUTOMC.md"
MAX_AUTOMC_MD, MAX_INDEX = 4_000, 3_000            # characters; together about 2,000 tokens
MAX_NOTE = 2_000                                   # one fact
LABELS = ("public", "unknown", "restricted")
ORDER = {"public": 0, "unknown": 1, "restricted": 2}
_FRONT = re.compile(r"^---\s*\n(.*?)\n---\s*\n?", re.DOTALL)


class MemoryError_(Exception):
    pass


def folder(workspace=None):
    return Path(workspace or automc_paths.WORKSPACE) / automc_paths.STATE / FOLDER


def slug(title):
    s = re.sub(r"[^a-z0-9]+", "-", (title or "").lower()).strip("-")[:60]
    return s or "note-" + hashlib.sha256((title or "").encode()).hexdigest()[:8]


def _now():
    import datetime as _dt
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def _parse(path):
    text = Path(path).read_text(encoding="utf-8")
    m = _FRONT.match(text)
    meta = {}
    if m:
        for line in m.group(1).splitlines():
            k, _, v = line.partition(":")
            if k.strip():
                meta[k.strip()] = v.strip()
        text = text[m.end():]
    return {**meta, "name": meta.get("name") or Path(path).stem, "label": meta.get("label") if meta.get("label") in LABELS else "unknown",
            "text": text.strip()}


def notes(workspace=None):
    """Every note, oldest first."""
    d = folder(workspace)
    out = [_parse(p) for p in sorted(d.glob("*.md")) if p.name != INDEX] if d.is_dir() else []
    return sorted(out, key=lambda n: n.get("created") or "")


def get(name, workspace=None):
    """A note by its name, or by the title it was saved under."""
    for candidate in (name, slug(name)):
        p = folder(workspace) / f"{candidate}.md"
        if candidate and p.is_file() and p.name != INDEX:
            return _parse(p)
    return None


def _write_note(note, workspace=None):
    d = folder(workspace)
    d.mkdir(parents=True, exist_ok=True)
    head = "\n".join(f"{k}: {note[k]}" for k in ("name", "title", "label", "created", "updated", "chat") if note.get(k) is not None)
    (d / f"{note['name']}.md").write_text(f"---\n{head}\n---\n{note['text'].strip()}\n", encoding="utf-8")


def _index(workspace=None):
    """Rewrite MEMORY.md from the notes: one line per note, its label shown."""
    lines = ["# Memory index (written by AutoMC from the notes in this folder; edit a note, not this file)", ""]
    for n in notes(workspace):
        first = re.split(r"(?<=[.!?])\s", n["text"].replace("\n", " "), maxsplit=1)[0][:120]
        lines.append(f"- [{n.get('title') or n['name']}]({n['name']}.md) ({n['label']}): {first}")
    d = folder(workspace)
    d.mkdir(parents=True, exist_ok=True)
    (d / INDEX).write_text("\n".join(lines) + "\n", encoding="utf-8")


def _locked(workspace=None):
    from tools.openmc import jobs
    return jobs._locked(folder(workspace), "memory.lock")


def save(title, text, label, chat=None, workspace=None):
    """Write a note (a new one, or the one with this title: replaced) and the index. Returns the note."""
    if not isinstance(title, str) or not title.strip():
        raise MemoryError_("a note needs a short title")
    if not isinstance(text, str) or not text.strip():
        raise MemoryError_("a note needs its text: one fact")
    if len(text) > MAX_NOTE:
        raise MemoryError_(f"a note is one fact, at most {MAX_NOTE:,} characters (this one has {len(text):,}); split it or shorten it")
    if label not in LABELS:
        raise MemoryError_(f"label must be one of {LABELS}")
    with _locked(workspace):
        name = slug(title)
        old = get(name, workspace)
        note = {"name": name, "title": title.strip(), "label": label, "created": (old or {}).get("created") or _now(), "updated": _now(),
                "chat": chat, "text": text.strip()}
        if old and ORDER.get(old.get("label"), 1) > ORDER[label]:
            note["label"] = old["label"]                    # replacing never lowers a label; only the user marks a note public
        _write_note(note, workspace)
        _index(workspace)
    return {**note, "replaced": bool(old)}


def forget(name, workspace=None):
    with _locked(workspace):
        p = folder(workspace) / f"{name}.md"
        if not p.is_file():
            return False
        p.unlink()
        _index(workspace)
        return True


def set_label(name, label, workspace=None):
    """The user's decision on a note's label (/memory public NAME)."""
    with _locked(workspace):
        n = get(name, workspace)
        if n is None:
            return None
        n["label"], n["updated"] = label, _now()
        _write_note(n, workspace)
        _index(workspace)
        return n


# ----------------------------------------------------------------------------------------------------------------------
# AUTOMC.md and the prompt
# ----------------------------------------------------------------------------------------------------------------------
def automc_md(workspace=None):
    """(text, label) of the workspace's AUTOMC.md, or (None, None). A first line `label: public` (or restricted) is its label;
    otherwise unknown."""
    p = Path(workspace or automc_paths.WORKSPACE) / AUTOMC_MD
    if not p.is_file():
        return None, None
    text = p.read_text(encoding="utf-8", errors="replace")
    first = text.lstrip().split("\n", 1)[0].strip().lower()
    m = re.match(r"^label:\s*(public|restricted|unknown)\s*$", first)
    if m:
        text = text.lstrip().split("\n", 1)[1] if "\n" in text.lstrip() else ""
        return text.strip(), m.group(1)
    return text.strip(), "unknown"


def prompt_block(cloud, facility):
    """(text for the system prompt, info). What a model may see: a cloud model in facility mode gets only public things."""
    def visible(label):
        return not cloud or not facility or label == "public"
    parts, info = [], {"automc_md": None, "notes_shown": 0, "notes_left_out": 0, "cut": []}
    text, label = automc_md()
    if text is not None:
        label = label if facility else "public"
        if visible(label):
            if len(text) > MAX_AUTOMC_MD:
                info["cut"].append(f"AUTOMC.md: {len(text) - MAX_AUTOMC_MD:,} characters past {MAX_AUTOMC_MD:,} left out; shorten it")
                text = text[:MAX_AUTOMC_MD] + "\n[... the rest of AUTOMC.md is left out: it is longer than AutoMC gives it ...]"
            parts.append(f"## Project instructions (AUTOMC.md, written by the user; {label})\n{text}")
            info["automc_md"] = label
        else:
            parts.append("## Project instructions\nThe workspace has an AUTOMC.md, which is not marked public, so it is not given to a "
                         "cloud model. Its rules still apply: when unsure, ask the user.")
            info["automc_md"] = "left out"
    shown, left = [], 0
    for n in notes():
        label = n["label"] if facility else "public"
        if not visible(label):
            left += 1
            continue
        first = re.split(r"(?<=[.!?])\s", n["text"].replace("\n", " "), maxsplit=1)[0][:160]
        shown.append(f"- {n['name']} ({label}): {n.get('title') or ''}: {first}")
    body = "\n".join(shown)
    if len(body) > MAX_INDEX:
        info["cut"].append(f"memory index: {len(body) - MAX_INDEX:,} characters past {MAX_INDEX:,} left out; forget old notes")
        body = body[:MAX_INDEX].rsplit("\n", 1)[0] + "\n- [... more notes, left out: the index is longer than AutoMC gives it ...]"
    if shown or left:
        parts.append("## Memory (notes saved in this workspace; read one whole with MemoryReadTool)\n" + (body or "(none you may see)")
                     + (f"\n- {left} more note(s) not given to a cloud model" if left else ""))
    info.update(notes_shown=len(shown), notes_left_out=left)
    return "\n\n".join(parts), info


def as_json(note):
    return json.loads(json.dumps(note, default=str))
