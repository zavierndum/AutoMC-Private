"""Parent-side wrapper for geometry previews: starts triga._preview in its own process. No OpenMC import here."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from . import libraries
from .errors import BuildTimeout, InvalidSpec, MissingLibrary, TrigaError
from .safety import WORKSPACE

PREVIEW_NAMES = ("core_xy", "world_xy", "core_xz", "world_xz")


def case_folder(case, workspace=None):
    """A case name (resolved inside the workspace) or a path to a case folder; it must contain model.xml."""
    p = Path(case)
    if not (os.sep in str(case) or "/" in str(case) or p.is_dir()):
        p = Path(workspace or WORKSPACE) / case
    p = p.resolve()
    if not (p / "model.xml").is_file():
        raise InvalidSpec("case", f"{p} has no model.xml; build it first (python -m automc triga build ...)")
    return p


def render_previews(case, *, which=None, pixels=1000, workspace=None, timeout_s=300, python=None):
    """Draw geometry previews into <case>/preview/. Returns [{name, path, bytes}]. Runs no transport."""
    names = list(which) if which else list(PREVIEW_NAMES)
    bad = [n for n in names if n not in PREVIEW_NAMES]
    if bad:
        raise InvalidSpec("preview", f"unknown preview {bad}; allowed: {list(PREVIEW_NAMES)}")
    if not isinstance(pixels, int) or isinstance(pixels, bool) or not 100 <= pixels <= 6000:
        raise InvalidSpec("pixels", f"must be an integer between 100 and 6000, got {pixels!r}")
    folder = case_folder(case, workspace)
    if os.name == "nt":                                  # after the input checks, so those behave the same everywhere
        raise MissingLibrary("previews need OpenMC; run from Linux or WSL (the automc environment).")
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([str(libraries.SRC)] + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONUNBUFFERED"] = "1"
    env["MPLBACKEND"] = "Agg"
    cmd = [libraries.python_executable(python), "-m", f"{__package__}._preview", "--case", str(folder), "--which", ",".join(names),
           "--pixels", str(pixels)]
    try:
        proc = subprocess.run(cmd, cwd=str(folder), env=env, capture_output=True, text=True, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        raise BuildTimeout(f"the preview did not finish within {timeout_s:g} s") from None
    if proc.returncode != 0:
        tail = "\n".join((proc.stdout + proc.stderr).splitlines()[-15:])
        raise TrigaError(f"the preview worker exited with code {proc.returncode}\n--- end of output ---\n{tail}")
    return json.loads((folder / "preview" / "previews.json").read_text(encoding="utf-8"))
