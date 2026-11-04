"""
Saved conversations (devops/AutoMC_Workspace_Plan.md, section 5, WD-3): one JSONL file per conversation, .automc/chats/<id>.jsonl, named
by a random UUID as AutoSAM and Claude Code name theirs. It is also the conversation's audit (W8): one file, not two.

    line 1    {"event": "session", "format", "id", "created_at", "automc_version", "fingerprint", "user", "workspace", "provider", ...}
    then      one line per event, as the agent goes: "user", "assistant", "final", "approval", "switch", "tool_result", "withheld",
              "context", "cap", "model_error", "retry", "rewind", "repair", ...; and "message" lines, each one LangChain message as the
              model saw it (a tool result withheld from a cloud model is saved as the note the model got), with the label it put in
Keys are never written. The file is created with the conversation's first event after the session line, so a chat left without a
word leaves nothing behind.

A conversation that holds restricted data is saved only if the workspace's settings say conversations.save_restricted: true (W3, NETL's
data owners decide; default false). Until then, from the moment restricted data enters, the file gets no more content: the events keep
their names, labels and counts, and one "not_saved" line says why. What came before stays.

What left the machine, whatever happens to the chats, goes to .automc/egress.jsonl: when, which conversation, which service (a cloud
model, LangSmith, the web, Visual-Parser's cloud vision) and the label of what was sent; never content.

    ChatFile(chat_id, meta)          a conversation's file, written as it goes
    list_chats(include_links=True)   the saved conversations of this workspace (and the linked ones, read only)
    read(path)                       a file's events
    copy_here(path)                  a linked conversation copied into this workspace as a new one ("continue here")
    search(query, exclude, k)        the past turns that best match the words (ChatSearchTool)
    egress(service, label, ...)      one line in egress.jsonl
Standard library only.
"""
from __future__ import annotations

import json
import re
import time
import uuid
from pathlib import Path

import automc_paths

FORMAT = 1
FOLDER = "chats"
EGRESS = "egress.jsonl"
LABEL_ORDER = {"public": 0, "unknown": 1, "restricted": 2}
# The fields of an event that hold what was said (text, tool arguments); left out once a conversation's content is no longer saved.
CONTENT = ("text", "tool_calls", "message", "args")
TITLE_CHARS = 80


def now():
    import datetime as _dt
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def folder(workspace=None):
    return Path(workspace or automc_paths.WORKSPACE) / automc_paths.STATE / FOLDER


def new_id():
    return str(uuid.uuid4())


def worst(labels):
    return max(labels, key=lambda x: LABEL_ORDER.get(x, 1), default="public")


def save_restricted():
    import automc_settings
    try:
        return bool(automc_settings.load()["conversations"]["save_restricted"])
    except automc_settings.SettingsError:
        return False


class ChatFile:
    """A conversation's file. `meta` becomes the session line, written with the first other event. `existing`: append to a saved
    conversation's file (a loaded one); its session line is kept."""

    def __init__(self, chat_id, meta, existing=False):
        self.id = chat_id
        self.path = folder() / f"{chat_id}.jsonl"
        self.meta = None if existing else {"event": "session", "format": FORMAT, "id": chat_id, "created_at": now(),
                                           "user": _user(), "workspace": str(Path(automc_paths.WORKSPACE).resolve()), **meta}
        self.content = True                       # False once restricted data entered and the settings say not to save it
        self.said_not_saved = False

    def stop_content(self, reason):
        """From now on, events keep no content; one not_saved line says why."""
        if self.content:
            self.content = False
            self._write({"t": now(), "event": "not_saved", "reason": reason})

    def event(self, name, /, **fields):
        """One event; its fields may use any name, `kind` and `name` included (positional-only `name`)."""
        line = {"t": now(), "event": name, **fields}
        if not self.content:
            line = {k: v for k, v in line.items() if k not in CONTENT}
            line["content"] = "not saved"
        self._write(line)

    def message(self, message_dict, label):
        if self.content:
            self._write({"t": now(), "event": "message", "label": label, "message": message_dict})

    def _write(self, line):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as fh:
            if self.meta is not None:
                fh.write(json.dumps(self.meta, default=str) + "\n")
                self.meta = None
            fh.write(json.dumps(line, default=str) + "\n")


def _user():
    try:
        import getpass
        return getpass.getuser()
    except Exception:
        return None


def read(path):
    """A conversation file's events; a line cut short (a crash while writing) is skipped."""
    out = []
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue
    except OSError:
        pass
    return out


def summarize(path, where="this workspace", events=None):
    """{id, title, created_at, updated_at, turns, label, saved, model, where, path} of one file; None for one with no message from the
    user (not a conversation)."""
    events = read(path) if events is None else events
    users = [e for e in events if e.get("event") == "user"]
    if not users:
        return None
    meta = events[0] if events and events[0].get("event") == "session" else {}
    labels = [e.get("label") for e in events if e.get("event") == "message" and e.get("label")]
    labels += [e.get("entered") for e in events if e.get("event") == "tool_result" and e.get("entered")]
    title = next((u.get("text") for u in users if u.get("text")), None)
    model = next((e.get("to") for e in reversed(events) if e.get("event") == "switch"), None) or meta.get("provider_label")
    st = Path(path).stat()
    return {"id": meta.get("id") or Path(path).stem, "title": _title(title) if title else "(not saved)",
            "created_at": meta.get("created_at"), "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(st.st_mtime)),
            "turns": len(users), "label": worst(labels), "saved": not any(e.get("event") == "not_saved" for e in events),
            "model": model, "where": where, "path": str(path), "copied_from": meta.get("copied_from")}


def _title(text):
    text = re.sub(r"\s+", " ", str(text)).strip()
    return text if len(text) <= TITLE_CHARS else text[:TITLE_CHARS - 3] + "..."


def list_chats(include_links=True):
    """The saved conversations, newest first: this workspace's, then each linked workspace's (read only)."""
    out = []
    places = [(folder(), "this workspace")]
    if include_links:
        import automc_settings
        try:
            places += [(lk.path / automc_paths.STATE / FOLDER, f"linked: {lk.name}") for lk in automc_settings.links()]
        except automc_settings.SettingsError:
            pass
    for d, where in places:
        rows = [s for p in (sorted(d.glob("*.jsonl")) if d.is_dir() else []) for s in [summarize(p, where)] if s]
        out += sorted(rows, key=lambda r: r["updated_at"], reverse=True)
    return out


def find(ref, include_links=True):
    """A conversation by its number in list_chats (1-based), its id, or the start of its id."""
    rows = list_chats(include_links)
    if isinstance(ref, str) and ref.isdigit() and 1 <= int(ref) <= len(rows):
        return rows[int(ref) - 1]
    match = [r for r in rows if r["id"] == ref] or [r for r in rows if isinstance(ref, str) and len(ref) >= 6 and r["id"].startswith(ref)]
    return match[0] if len(match) == 1 else None


def copy_here(path):
    """A conversation of a linked workspace copied into this one as a new conversation (its label kept); the original is not touched."""
    events = read(path)
    new = new_id()
    dest = folder() / f"{new}.jsonl"
    dest.parent.mkdir(parents=True, exist_ok=True)
    with open(dest, "w", encoding="utf-8") as fh:
        for i, e in enumerate(events):
            if i == 0 and e.get("event") == "session":
                e = {**e, "id": new, "copied_from": str(path), "copied_at": now()}
            fh.write(json.dumps(e, default=str) + "\n")
    return new


def delete(chat_id):
    """Delete one of this workspace's conversations; what egress.jsonl recorded stays."""
    p = folder() / f"{chat_id}.jsonl"
    if p.is_file():
        p.unlink()
        return True
    return False


# ----------------------------------------------------------------------------------------------------------------------
# searching past conversations
# ----------------------------------------------------------------------------------------------------------------------
_WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9.+-]*")
STOP = frozenset("a an and are as at be by did do does for from how i in is it me my of on or our so that the this to was we what "
                 "when where which who why with you your".split())


def _terms(text):
    return {w.lower().strip(".") for w in _WORD.findall(text or "")} - STOP


def _turns(events):
    """(question, answer) pairs from the user and final events."""
    out, q = [], None
    for e in events:
        if e.get("event") == "user" and e.get("text"):
            q = e["text"]
        elif e.get("event") == "final" and q is not None:
            out.append((q, e.get("text") or ""))
            q = None
    return out


def search(query, exclude=None, k=5):
    """The past turns (a question and its answer) whose words best match `query`, from this workspace's conversations and the linked
    ones, each with its conversation's label; the conversation `exclude` (the current one) is left out."""
    want = _terms(query)
    if not want:
        return []
    hits = []
    for row in list_chats():
        if row["id"] == exclude:
            continue
        for q, a in _turns(read(row["path"])):
            have = _terms(q) | _terms(a)
            score = len(want & have) / len(want)
            if score > 0:
                hits.append({"score": round(score, 3), "chat": row["id"], "title": row["title"], "where": row["where"],
                             "when": row["updated_at"], "question": _title(q), "answer": a[:600] + ("..." if len(a) > 600 else ""),
                             "facility_data": row["label"]})
    hits.sort(key=lambda h: (-h["score"], h["when"]), reverse=False)
    return hits[:k]


# ----------------------------------------------------------------------------------------------------------------------
# what left the machine
# ----------------------------------------------------------------------------------------------------------------------
def egress(service, label, chat=None, what=None, workspace=None):
    """One line in .automc/egress.jsonl: when, which conversation, which service, the label of what was sent, what kind of thing it
    was (a model call, a trace, a search, a page, page images). Never content."""
    path = Path(workspace or automc_paths.WORKSPACE) / automc_paths.STATE / EGRESS
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"t": now(), "chat": chat, "service": service, "label": label, "what": what}) + "\n")
    except OSError:                                    # the record must never stop the work it records
        pass


def egress_lines(workspace=None):
    return read(Path(workspace or automc_paths.WORKSPACE) / automc_paths.STATE / EGRESS)
