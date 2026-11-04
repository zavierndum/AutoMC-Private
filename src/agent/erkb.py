"""
The error knowledge base (skills/ERKB.yaml), matched in code. A failed tool result gets the entries whose detection_hint occurs in
its text, as `known_errors` (the signature, the suggestion and the confidence of each), so that a known error is recognised every
time rather than left to the model's memory. The whole file is not in the system prompt; the model reads it on demand
(SkillLookupTool, file ERKB.yaml). Standard library plus PyYAML.
"""
from __future__ import annotations

from pathlib import Path

PATH = Path(__file__).resolve().parent.parent / "skills" / "ERKB.yaml"
KEYS = ("detection_hint", "signature", "background", "suggestion", "confidence", "origin")
CONFIDENCE = ("high", "medium", "low")
ORIGINS = ("seen", "source")       # seen: met in this project; source: from OpenMC's source or documentation, not met yet
FAILED = {"failed", "interrupted", "error", "timed_out"}           # a job's or a Python run's status that means it went wrong
MAX_TEXT = 200_000                                                  # characters of a result searched; a long log's end is kept
_CACHE = {}


class ERKBError(ValueError):
    pass


def entries(path=None):
    """The entries, checked: exactly the keys of SKILL_POLICY.md, known confidence and origin, every hint unique. Read again when the
    file changes."""
    import yaml
    p = Path(path or PATH)
    stamp = p.stat().st_mtime_ns
    if _CACHE.get(p, (None,))[0] == stamp:
        return _CACHE[p][1]
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or []
    if not isinstance(raw, list):
        raise ERKBError(f"{p.name} must be a list of entries")
    hints = set()
    for i, e in enumerate(raw):
        where = f"{p.name} entry {i + 1}"
        if not isinstance(e, dict) or set(e) != set(KEYS):
            raise ERKBError(f"{where}: needs exactly {', '.join(KEYS)}")
        if not all(isinstance(e[k], str) and e[k].strip() for k in KEYS):
            raise ERKBError(f"{where}: every field is a non-empty string")
        if e["confidence"] not in CONFIDENCE or e["origin"] not in ORIGINS:
            raise ERKBError(f"{where}: confidence is one of {CONFIDENCE}, origin one of {ORIGINS}")
        if e["detection_hint"] in hints:
            raise ERKBError(f"{where}: the hint {e['detection_hint']!r} is given twice")
        hints.add(e["detection_hint"])
    _CACHE[p] = (stamp, raw)
    return raw


def match(text, path=None):
    """The entries whose detection_hint occurs in `text`, in the file's order."""
    return [e for e in entries(path) if e["detection_hint"] in (text or "")]


def failed(result):
    """Whether a tool result reports something that went wrong: ok false, a failed job (also as the result's "job", as
    OpenMCRunCheckTool gives it) or Python run, a failed build or check."""
    if not isinstance(result, dict):
        return False
    if (result.get("ok") is False or result.get("status") in FAILED or result.get("built") is False
            or result.get("passed") is False or bool(result.get("error"))):
        return True
    job = result.get("job")
    return isinstance(job, dict) and job.get("status") in FAILED


def _texts(value, out, budget):
    if budget[0] <= 0:
        return
    if isinstance(value, str):
        out.append(value[-budget[0]:])
        budget[0] -= len(out[-1])
    elif isinstance(value, dict):
        for v in value.values():
            _texts(v, out, budget)
    elif isinstance(value, (list, tuple)):
        for v in value:
            _texts(v, out, budget)


def annotate(result, path=None):
    """A failed result with `known_errors` added when its text holds a known error's hint; any other result unchanged."""
    if not failed(result) or "known_errors" in result:
        return result
    parts = []
    _texts(result, parts, [MAX_TEXT])
    found = match("\n".join(parts), path)
    if found:
        result["known_errors"] = [{"signature": e["signature"], "suggestion": " ".join(e["suggestion"].split()),
                                   "confidence": e["confidence"], "matched": e["detection_hint"]} for e in found]
    return result
