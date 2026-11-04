"""
Rules for names and output locations, so a spec coming from a person or an agent cannot write outside the workspace.
"""
from __future__ import annotations

import re
from pathlib import Path

import automc_paths

from .errors import InvalidSpec

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
MAX_DEPTH = 4                                    # study-8/configs/c8: a case may sit up to four folders deep in the workspace

WORKSPACE = automc_paths.WORKSPACE               # AutoMC/workspace unless AUTOMC_WORKSPACE says otherwise


def validate_name(name):
    """A case name becomes its folder in the workspace: one to four folder names separated by /, each of letters, digits, dot,
    underscore and hyphen, starting alphanumeric, at most 64 characters, not ending with a dot (so never .., never AutoMC's .automc)."""
    parts = name.split("/") if isinstance(name, str) else []
    if not parts or len(parts) > MAX_DEPTH or not all(NAME_RE.match(p) and not p.endswith(".") for p in parts):
        raise InvalidSpec("name", f"must be one to {MAX_DEPTH} folder names separated by /, each matching [A-Za-z0-9][A-Za-z0-9._-]{{0,63}} "
                                  f"and not ending with a dot, got {name!r}")
    return name


def confined_outdir(name, outdir=None, workspace=None, allow_outside=False):
    """
    The folder a build may write. Default is <workspace>/<name>. An explicit outdir must resolve (symlinks and ".." included)
    to a place inside the workspace unless `allow_outside` is true, which only a trusted local caller (the CLI) may pass.
    """
    validate_name(name)
    ws = Path(workspace or WORKSPACE).resolve()
    target = (ws / name) if outdir is None else Path(outdir)
    target = target.resolve()
    if not allow_outside and not (target == ws or ws in target.parents):
        raise InvalidSpec("outdir", f"must be inside the workspace {ws}, got {target}")
    if target == ws:
        raise InvalidSpec("outdir", "must be a folder inside the workspace, not the workspace itself")
    return target
