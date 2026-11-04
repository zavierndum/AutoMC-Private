"""
Where AutoMC keeps things. Every module asks here instead of counting parent folders, so code can move without breaking paths.

    AUTOMC_ROOT     the AutoMC folder (src/, library_snapshots/, workspace/)
    SRC             the import root: run AutoMC from here (python -m automc ...) or put it on PYTHONPATH
    WORKSPACE       the project AutoMC works in: cases, runs and everything it keeps; AUTOMC_WORKSPACE (or --workspace, use_workspace)
                    overrides the default AutoMC/workspace
    STATE           the one hidden folder in a workspace that holds everything AutoMC keeps there (devops/AutoMC_Workspace_Plan.md,
                    section 3): settings.yaml, format.json, chats/, jobs/, documents/, python/, web/, models/; state() gives a path in it
    SNAPSHOTS_DIR   pinned copies of other people's libraries, never edited by hand (library_snapshots/make_snapshots.py)
    PUBLIC_PACKS_DIR               facility packs that ship with AutoMC (public data only)
    DEFAULT_RESTRICTED_PACKS_DIR   where restricted facility packs are looked for when AUTOMC_FACILITY_PACKS is not set: next to
                                   the AutoMC folder, never inside it

Standard library only.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

SRC = Path(__file__).resolve().parent
AUTOMC_ROOT = SRC.parent
WORKSPACE = Path(os.environ["AUTOMC_WORKSPACE"]).expanduser() if os.environ.get("AUTOMC_WORKSPACE") else AUTOMC_ROOT / "workspace"
STATE = ".automc"
SNAPSHOTS_DIR = AUTOMC_ROOT / "library_snapshots"
PUBLIC_PACKS_DIR = SRC / "facility_packs"
DEFAULT_RESTRICTED_PACKS_DIR = AUTOMC_ROOT.parent / "AutoMC_restricted" / "facility_packs"
_WINDOWS_PATH = re.compile(r"^([A-Za-z]):[\\/](.*)$")


def state(*parts):
    """A path inside the workspace's .automc/ folder (read at call time, so a workspace set later counts)."""
    return Path(WORKSPACE) / STATE / Path(*parts) if parts else Path(WORKSPACE) / STATE


def use_workspace(path):
    """Make `path` the workspace of this process and of every process it starts (AUTOMC_WORKSPACE). Call it before other AutoMC
    modules are imported: a few keep the workspace they found at import."""
    global WORKSPACE
    WORKSPACE = Path(host_path(str(path))).expanduser().resolve()
    os.environ["AUTOMC_WORKSPACE"] = str(WORKSPACE)
    return WORKSPACE


def host_path(text):
    """A path as this system writes it: a Windows path (C:\\...) becomes /mnt/c/... under Linux (WSL); anything else is unchanged."""
    raw = (text or "").strip()
    m = _WINDOWS_PATH.match(raw)
    if m and os.name == "posix":
        return f"/mnt/{m.group(1).lower()}/{m.group(2).replace(chr(92), '/')}"
    return raw
