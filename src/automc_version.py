"""
AutoMC's one version (devops/AutoMC_Workspace_Plan.md, section 7, WD-1): the package's. It names the code, the system prompt, the
skills and the evaluation cases together; their history is in AutoMC/CHANGELOG.md. Between releases nothing is bumped by hand:
fingerprint() says exactly which prompt and skills (and cases) a chat or an evaluation saw, computed from their files.

File formats (a case's result.json, a knowledge base's kb.json, the job registry) carry their own small format numbers: they tell a
newer AutoMC how to read an older file, and change only when a file's layout does.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import automc_paths

VERSION = "0.8.0"


def _files(include_cases):
    src = Path(automc_paths.SRC)
    files = [src / "prompts" / "system_prompt_automc.yml"]
    files += sorted(p for p in (src / "skills").rglob("*") if p.is_file() and p.name != "MANIFEST.json" and "__pycache__" not in p.parts)
    if include_cases:
        files.append(src / "evals" / "cases.yml")
    return files, src


def fingerprint(include_cases=False):
    """12 hex characters from the system prompt file and every bundled skill file (and the evaluation cases): two chats with the same
    fingerprint saw the same prompt and skills. A missing file counts as empty."""
    files, src = _files(include_cases)
    h = hashlib.sha256()
    for p in files:
        h.update(p.relative_to(src).as_posix().encode() + b"\0")
        h.update(p.read_bytes() if p.is_file() else b"")
        h.update(b"\0")
    return h.hexdigest()[:12]


def describe(include_cases=False):
    """'AutoMC 0.8.0 (prompt and skills 1a2b3c4d5e6f)', as reports and the chat print it."""
    what = "prompt, skills and cases" if include_cases else "prompt and skills"
    return f"AutoMC {VERSION} ({what} {fingerprint(include_cases)})"
