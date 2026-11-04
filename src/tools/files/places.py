"""
Where the file tools may read and write, what they always refuse, and the facility label of a place (devops/AutoMC_General_Tools_Plan.md,
GT-2; folders and modes from devops/AutoMC_Workspace_Plan.md, WD-2). Standard library and automc_settings.

Read roots: the workspace, the AutoMC folder, the facility-pack folders (shipped and restricted), the folders the workspace's settings
declare (automc_settings: settings.yaml and AUTOMC_PUBLIC_ROOTS, AUTOMC_RESTRICTED_ROOTS, AUTOMC_ROOTS), and the linked workspaces
(read only). Writes: only inside the workspace, never into a case folder, a run folder or AutoMC's own bookkeeping (.automc/), and
never under the name of one of AutoMC's records (result.json and the like), because a written result.json would turn its folder into a
"case" with whatever label the file claims.

Always refused, whatever the roots: the keys file AUTOMC_ENV_FILE names, .env files, keys and credentials (*.pem, *.key, id_rsa and
the like, .ssh, .gnupg, .aws, .netrc, credentials*), and .automc/models.

The label of a place, most restricted first:
  restricted   inside a restricted facility pack's folder (AUTOMC_FACILITY_PACKS, or AutoMC_restricted/facility_packs) or a folder
               declared restricted
  a case's     inside a case of the workspace or of a linked one: the facility label its builder recorded (result.json), else unknown
  a KB's       inside a knowledge base's folder (.automc/documents/<kb>/): the label its record holds (kb.json), else unknown
  public       inside src/ (AutoMC's code, skills, prompts and the public pack) or a folder declared public
  public       anywhere else in open mode (nothing restricted declared: automc_settings.facility_mode)
  None         anywhere else in facility mode: the result carries no label, so the data guard withholds it from a cloud model
               (agent/guard.py)

A place recorded in a file (a knowledge base's source folder) is written as a reference that survives moving the workspace or mounting
it elsewhere (to_ref, from_ref): ws:<path in the workspace>, folder:<name>:<path in a declared folder>, else the absolute path.
"""
from __future__ import annotations

import fnmatch
import json
import os
from pathlib import Path

import automc_paths
import automc_settings

PUBLIC_ENV, RESTRICTED_ENV, ROOTS_ENV = (env for env, _ in automc_settings.ROOT_ENVS)
SETTINGS_HINT = (f"declare its folder in {automc_paths.STATE}/{automc_settings.FILE} (folders:) or in AUTOMC_PUBLIC_ROOTS, "
                 f"AUTOMC_RESTRICTED_ROOTS or AUTOMC_ROOTS")
SECRET_NAMES = (".env", ".env.*", "*.env", "*.pem", "*.key", "*.p12", "*.pfx", "*.keystore", "id_rsa*", "id_dsa*", "id_ecdsa*",
                "id_ed25519*", ".netrc", "_netrc", "credentials*", ".pgpass", ".git-credentials", "authorized_keys")
SECRET_DIRS = (".ssh", ".gnupg", ".aws", ".azure", ".kube")
SECRET_STATE = ("models",)                 # inside .automc/: model checks
NOT_SECRET = ("*.example", "*.sample", "*.template")
SKIP_DIRS = (".git", "__pycache__", "node_modules", ".ipynb_checkpoints", ".venv", ".mypy_cache", ".pytest_cache")
RECORDS = ("result.json", "sweep.json", "events.jsonl", "statepoint.*.h5", "summary.h5", "pack.yaml", "job.json")
ORDER = {"public": 0, None: 1, "unknown": 2, "restricted": 3}
DOCUMENTS = f"{automc_paths.STATE}/documents"   # tools/documents/parse.py: one folder per knowledge base, its label in kb.json


class Refused(Exception):
    """A path the tools must not touch; the message says why, never what the file holds."""

    def __init__(self, field, message, kind="NotAllowed", exit_code=3):
        self.field, self.kind, self.exit_code = field, kind, exit_code
        super().__init__(f"{field}: {message}")

    def payload(self):
        return {"type": self.kind, "field": self.field, "message": str(self), "exit_code": self.exit_code}


def restricted_dirs():
    """The restricted facility packs' folders and the folders declared restricted."""
    from tools.reactors.triga import packs
    out = [Path(d).resolve() for d in packs.restricted_dirs()]
    try:
        out += [Path(p.folder).resolve() for p in packs.installed().packs if p.distribution == "restricted"]
    except Exception:                                   # a broken pack: its configured folders still count as restricted
        pass
    return out + [f.path for f in automc_settings.folders() if f.label == "restricted"]


def link_roots():
    """The linked workspaces (read only)."""
    return [lk.path for lk in automc_settings.links()]


def read_roots():
    roots = ([Path(automc_paths.WORKSPACE).resolve(), automc_paths.AUTOMC_ROOT.resolve()] + restricted_dirs()
             + [f.path for f in automc_settings.folders()] + link_roots())
    return list(dict.fromkeys(r for r in roots if r.exists()))


def to_ref(path):
    """How a place is written into a record: ws:<rel> inside the workspace, folder:<name>:<rel> inside a declared folder (the deepest),
    else the absolute path."""
    p = Path(path).resolve()
    ws = Path(automc_paths.WORKSPACE).resolve()
    if _inside(p, ws):
        return "ws:" + (p.relative_to(ws).as_posix() if p != ws else ".")
    named = sorted((f for f in automc_settings.folders() if f.name and _inside(p, f.path)), key=lambda f: len(f.path.parts))
    if named:
        f = named[-1]
        return f"folder:{f.name}:" + (p.relative_to(f.path).as_posix() if p != f.path else ".")
    return str(p)


def from_ref(ref):
    """The place a reference written by to_ref names now (an absolute path stays as it is)."""
    text = str(ref or "")
    if text.startswith("ws:"):
        return (Path(automc_paths.WORKSPACE) / text[3:]).resolve()
    if text.startswith("folder:"):
        name, _, rel = text[len("folder:"):].partition(":")
        match = [f for f in automc_settings.folders() if f.name == name]
        if not match:
            raise Refused("folder", f"the folder {name!r} is no longer declared ({SETTINGS_HINT})", kind="NotFound", exit_code=2)
        return (match[0].path / rel).resolve()
    return Path(text)


def _inside(p, root):
    return p == root or root in p.parents


def resolve(path, field="path"):
    """The real absolute path of what the model named: relative to the workspace, absolute, ~, or a Windows path (C:\\...) under WSL."""
    if not isinstance(path, str) or not path.strip():
        raise Refused(field, "must be a path: relative to the workspace, or absolute", kind="InvalidArgument", exit_code=2)
    p = Path(automc_paths.host_path(path)).expanduser()
    if not p.is_absolute():
        p = Path(automc_paths.WORKSPACE) / p
    return p.resolve()


def secret_reason(p):
    """Why `p` is never read or written (a keys file, a credential), or None."""
    env_file = os.environ.get("AUTOMC_ENV_FILE", "").strip()
    if env_file and p == Path(env_file).expanduser().resolve():
        return "it is AutoMC's keys file"
    for part in p.parts[:-1] + (p.name,):
        if part in SECRET_DIRS:
            return f"it is inside {part}/, which holds keys or credentials"
    parts = p.parts
    for i, part in enumerate(parts[:-1]):
        if part == automc_paths.STATE and parts[i + 1] in SECRET_STATE:
            return f"it is inside {automc_paths.STATE}/{parts[i + 1]}/, AutoMC's model checks"
    name = p.name
    if any(fnmatch.fnmatch(name, pat) for pat in NOT_SECRET):
        return None
    if any(fnmatch.fnmatch(name, pat) for pat in SECRET_NAMES):
        return "its name is that of a keys or credentials file"
    return None


def check_read(path, field="path"):
    """The resolved path if the tools may read it; Refused otherwise."""
    p = resolve(path, field)
    why = secret_reason(p)
    if why:
        raise Refused(field, f"{p} is never read, because {why}")
    if not any(_inside(p, r) for r in read_roots()):
        raise Refused(field, f"{p} is outside the folders AutoMC may read (the workspace, AutoMC, the facility packs, the declared "
                             f"folders and the linked workspaces); to allow it, {SETTINGS_HINT}")
    if not p.exists():
        hint = ""
        if not Path(str(path).strip()).expanduser().is_absolute():
            hint = (f". A relative path starts in the workspace ({Path(automc_paths.WORKSPACE).resolve()}); the AutoMC folder is "
                    f"{automc_paths.AUTOMC_ROOT.resolve()}, its code and config are in {automc_paths.SRC.resolve()}")
        raise Refused(field, f"{p} does not exist{hint}. Folders you may read: {', '.join(str(r) for r in read_roots())}",
                      kind="NotFound", exit_code=2)
    return p


def _case_folder(p, ws):
    """The workspace case or sweep a path is in (the nearest folder with result.json or sweep.json), or None."""
    for d in [p] + list(p.parents):
        if d == ws or ws not in d.parents:
            return None
        if (d / "result.json").is_file() or (d / "sweep.json").is_file():
            return d
    return None


def _recorded(folder):
    for name in ("result.json", "sweep.json"):
        try:
            data = json.loads((folder / name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        fd = data.get("facility_data") if isinstance(data, dict) else None
        if isinstance(fd, dict) and fd.get("distribution"):
            return fd["distribution"]
        if isinstance(fd, str) and fd:
            return fd
    return "unknown"


def label(p):
    """(label, origin) of a place: the label goes into a result's facility_data (None: no label), the origin says why."""
    p = Path(p)
    for d in restricted_dirs():
        if _inside(p, d):
            return "restricted", "a restricted facility pack's folder, or a folder declared restricted"
    own = Path(automc_paths.WORKSPACE).resolve()
    for ws, which in [(own, "the workspace")] + [(r, f"the linked workspace {r.name}") for r in link_roots()]:
        docs = ws / DOCUMENTS
        if docs in p.parents and p.parent != docs:             # a knowledge base's folder: the label its record holds
            folder = docs / p.relative_to(docs).parts[0]
            try:
                rec = json.loads((folder / "kb.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                rec = {}
            return ((rec.get("label") if isinstance(rec, dict) and rec.get("label") else "unknown"),
                    f"the knowledge base {folder.name} of {which}")
        if _inside(p, ws):
            case = _case_folder(p, ws)
            if case is not None:
                return _recorded(case), f"the case {case.relative_to(ws).as_posix()} of {which}"
            break
    if _inside(p, automc_paths.SRC.resolve()):
        return "public", "AutoMC's own code, skills and public pack (src/)"
    for f in automc_settings.folders():
        if f.label == "public" and _inside(p, f.path):
            return "public", f"a folder declared public ({f.name or f.source})"
    if not automc_settings.facility_mode():
        return "public", "open mode: nothing restricted is declared"
    if _inside(p, own):
        return None, "the workspace, outside any case"
    return None, "a place not known to be public"


def worst(labels):
    return max(labels, key=lambda x: ORDER.get(x, 2), default="public")


def check_write(path, field="path"):
    """The resolved path if the tools may write it: inside the workspace, outside cases, runs and AutoMC's bookkeeping."""
    p = resolve(path, field)
    ws = Path(automc_paths.WORKSPACE).resolve()
    if not (ws in p.parents):
        raise Refused(field, f"{p} is outside the workspace {ws}; the agent writes only there")
    why = secret_reason(p)
    if why:
        raise Refused(field, f"{p} is never written, because {why}")
    if any(fnmatch.fnmatch(p.name, pat) for pat in RECORDS):
        raise Refused(field, f"{p.name} is the name of one of AutoMC's own records (a case's, a sweep's or a run's); a file of that "
                             f"name would change how the folder is labelled or read. Choose another name")
    rel = p.relative_to(ws).parts
    if any(part.startswith(".automc") for part in rel):
        raise Refused(field, f"{p} is inside AutoMC's own bookkeeping ({next(x for x in rel if x.startswith('.automc'))}/)")
    if _case_folder(p.parent, ws) is not None:
        raise Refused(field, f"{p} is inside the case {_case_folder(p.parent, ws).relative_to(ws).as_posix()}; cases and their "
                             f"runs are never changed by hand. Write next to it instead, for example notes/{p.name}")
    for d in [p.parent] + list(p.parent.parents):
        if d == ws:
            break
        if any(d.glob("statepoint.*.h5")) or (d / "events.jsonl").is_file():
            raise Refused(field, f"{p} is inside a run folder; run results are never overwritten")
    if p.is_dir():
        raise Refused(field, f"{p} is a folder", kind="InvalidArgument", exit_code=2)
    return p
