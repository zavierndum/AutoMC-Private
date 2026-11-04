"""
A workspace's settings (devops/AutoMC_Workspace_Plan.md, section 4, WD-2): which folders AutoMC may read besides the workspace and
how public each is, which other workspaces it may read, and a few policies. One file, <workspace>/.automc/settings.yaml:

    format: 1
    mode: auto                    # auto | facility (below)
    folders:                      # every folder listed is readable; the workspace itself always is, and is the only place written
      - {name: vp_test, path: /mnt/c/UTA-Research/KBs/vp_test, label: public}
      - {name: netl, path: /mnt/c/NETL/reports, label: restricted}
      - {name: papers, path: ../papers}                       # no label: unknown; a relative path starts at the workspace
    links:                        # other workspaces, read only: their cases, runs and knowledge bases, with their own labels
      - {name: projectA, path: /mnt/c/projects/projectA}
    python:
      approval: ask               # ask | auto (auto only inside a container: WD-5)
    conversations:
      save_restricted: false      # whether a conversation holding restricted data is saved (W3: NETL's data owners decide)

Two labels for a folder, public and restricted; a folder without one is of unknown origin. Three environment variables add folders for
one session without editing the file: AUTOMC_PUBLIC_ROOTS, AUTOMC_RESTRICTED_ROOTS and AUTOMC_ROOTS (unknown), each a list separated by
os.pathsep. AUTOMC_FILE_ROOTS, the old name of AUTOMC_ROOTS, is still read for one release, with a warning (W5).

The user settings, ~/.config/automc/settings.yaml (or AUTOMC_USER_SETTINGS), take the same keys and apply to every workspace; the
workspace's file comes first. Order: environment folders, then the workspace's settings, then the user's, then the defaults;
`python -m automc config show` prints every setting and where it came from.

Modes. open: nothing restricted is declared (no restricted facility pack installed, no restricted folder, mode auto); every place counts
as public and the data guard withholds only results labelled restricted. facility: a restricted pack or folder is declared, or the
workspace says mode: facility; places nobody declared are of unknown origin and cloud models see only public results. A workspace that
has held restricted data keeps mode: facility (workspace upgrade writes it), so removing a pack never opens it.

Standard library and PyYAML (PyYAML only when a settings file exists).
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path

import automc_paths

FILE = "settings.yaml"
FORMAT = 1
USER_ENV = "AUTOMC_USER_SETTINGS"
ROOT_ENVS = (("AUTOMC_PUBLIC_ROOTS", "public"), ("AUTOMC_RESTRICTED_ROOTS", "restricted"), ("AUTOMC_ROOTS", None))
OLD_ROOTS_ENV = "AUTOMC_FILE_ROOTS"                 # read as AUTOMC_ROOTS until the next release (W5)
LABELS = ("public", "restricted")
DEFAULTS = {"mode": "auto", "python": {"approval": "ask"}, "conversations": {"save_restricted": False}}
CHOICES = {"mode": ("auto", "facility"), ("python", "approval"): ("ask", "auto"), ("conversations", "save_restricted"): (True, False)}
TOP_KEYS = {"format", "mode", "folders", "links", "python", "conversations"}
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_CACHE = {}


class SettingsError(Exception):
    """A settings file AutoMC cannot use; the message names the file and the entry."""


@dataclass(frozen=True)
class Folder:
    name: str | None          # None for a folder from an environment variable
    path: Path                # resolved
    label: str | None         # public, restricted, or None (unknown origin)
    source: str               # where it was declared: the settings file, or the variable


@dataclass(frozen=True)
class Link:
    name: str
    path: Path
    source: str


def workspace_file(workspace=None):
    return Path(workspace or automc_paths.WORKSPACE) / automc_paths.STATE / FILE


def user_file():
    return Path(os.environ.get(USER_ENV) or Path.home() / ".config" / "automc" / FILE).expanduser()


def _path(text, base, where):
    if not isinstance(text, str) or not text.strip():
        raise SettingsError(f"{where}: path must be a folder (absolute, ~, or relative to the workspace)")
    p = Path(automc_paths.host_path(text)).expanduser()
    return (p if p.is_absolute() else Path(base) / p).resolve()


def unique_keys_loader():
    """PyYAML's safe loader, refusing a key given twice in one mapping (plain YAML keeps the last silently, so a second `folders:` would
    hide the first). The system prompt's loader uses it too (agent/prompt.py)."""
    import yaml

    class Loader(yaml.SafeLoader):
        pass

    def mapping(loader, node, deep=False):
        seen = set()
        for key_node, _ in node.value:
            key = loader.construct_object(key_node, deep=deep)
            if key in seen:
                raise yaml.constructor.ConstructorError(None, None, f"{key!r} is given twice; keep one", key_node.start_mark)
            seen.add(key)
        return loader.construct_mapping(node, deep=deep)

    Loader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, mapping)
    return Loader


def _read(path, base):
    """The checked contents of one settings file, or {} when it does not exist."""
    path = Path(path)
    try:
        st = path.stat()
    except OSError:
        return {}
    key = (str(path), st.st_mtime_ns, st.st_size, str(base))
    if key in _CACHE:
        return _CACHE[key]
    import yaml
    try:
        raw = yaml.load(path.read_text(encoding="utf-8"), Loader=unique_keys_loader()) or {}
    except (OSError, yaml.YAMLError) as e:
        raise SettingsError(f"{path}: cannot be read ({e})") from None
    if not isinstance(raw, dict):
        raise SettingsError(f"{path}: must be a mapping of settings")
    unknown = sorted(set(raw) - TOP_KEYS)
    if unknown:
        raise SettingsError(f"{path}: unknown settings {unknown}; allowed: {sorted(TOP_KEYS)}")
    if raw.get("format", FORMAT) != FORMAT:
        raise SettingsError(f"{path}: format {raw.get('format')} is not one this AutoMC reads ({FORMAT})")
    out = {"folders": [], "links": []}
    names = set()
    for i, f in enumerate(raw.get("folders") or []):
        where = f"{path}: folders[{i + 1}]"
        if not isinstance(f, dict) or set(f) - {"name", "path", "label"}:
            raise SettingsError(f"{where}: a folder is {{name, path, label}}")
        if not isinstance(f.get("name"), str) or not _NAME.match(f["name"]) or f["name"] in names:
            raise SettingsError(f"{where}: name must be unique, letters, digits, dot, underscore or hyphen, got {f.get('name')!r}")
        names.add(f["name"])
        label = f.get("label")
        if label in (None, "unknown"):
            label = None
        elif label not in LABELS:
            raise SettingsError(f"{where}: label must be public or restricted (or left out for unknown), got {label!r}")
        out["folders"].append(Folder(f["name"], _path(f.get("path"), base, where), label, str(path)))
    for i, link in enumerate(raw.get("links") or []):
        where = f"{path}: links[{i + 1}]"
        if not isinstance(link, dict) or set(link) != {"name", "path"} or not isinstance(link["name"], str) or not _NAME.match(link["name"]):
            raise SettingsError(f"{where}: a link is {{name, path}}, with a name of letters, digits, dot, underscore or hyphen")
        out["links"].append(Link(link["name"], _path(link["path"], base, where), str(path)))
    if "mode" in raw:
        if raw["mode"] not in CHOICES["mode"]:
            raise SettingsError(f"{path}: mode must be auto or facility, got {raw['mode']!r}")
        out["mode"] = raw["mode"]
    for section in ("python", "conversations"):
        sec = raw.get(section)
        if sec is None:
            continue
        allowed = {k for (s, k) in [c for c in CHOICES if isinstance(c, tuple)] if s == section}
        if not isinstance(sec, dict) or set(sec) - allowed:
            raise SettingsError(f"{path}: {section} may hold only {sorted(allowed)}")
        for k, v in sec.items():
            if v not in CHOICES[(section, k)]:
                raise SettingsError(f"{path}: {section}.{k} must be one of {list(CHOICES[(section, k)])}, got {v!r}")
            out.setdefault(section, {})[k] = v
    _CACHE[key] = out
    return out


def _env_folders():
    out, warnings = [], []
    sources = list(ROOT_ENVS)
    if os.environ.get(OLD_ROOTS_ENV, "").strip():
        sources.append((OLD_ROOTS_ENV, None))
        warnings.append(f"{OLD_ROOTS_ENV} is the old name of AUTOMC_ROOTS; rename it (it is read until the next release)")
    for env, label in sources:
        for raw in os.environ.get(env, "").split(os.pathsep):
            if raw.strip():
                out.append(Folder(None, Path(automc_paths.host_path(raw)).expanduser().resolve(), label, env))
    return out, warnings


def load(workspace=None):
    """{mode, folders, links, python, conversations, files, warnings}: the workspace's settings, the user's, the environment's folders
    and the defaults, merged. Raises SettingsError for a file AutoMC cannot use."""
    ws = Path(workspace or automc_paths.WORKSPACE)
    mine = _read(workspace_file(ws), ws)
    user = _read(user_file(), ws)
    env, warnings = _env_folders()
    folders = env + mine.get("folders", []) + user.get("folders", [])
    links = mine.get("links", []) + [lk for lk in user.get("links", []) if lk.name not in {x.name for x in mine.get("links", [])}]
    out = {"mode": mine.get("mode") or user.get("mode") or DEFAULTS["mode"], "folders": folders, "links": links,
           "files": [str(p) for p, d in ((workspace_file(ws), mine), (user_file(), user)) if d], "warnings": warnings}
    for section in ("python", "conversations"):
        out[section] = {**DEFAULTS[section], **user.get(section, {}), **mine.get(section, {})}
    return out


def folders(workspace=None):
    return load(workspace)["folders"]


def links(workspace=None):
    return load(workspace)["links"]


def restricted_pack_installed():
    try:
        from tools.reactors.triga import packs
        return any(p.distribution == "restricted" for p in packs.installed().packs)
    except Exception:                                   # a broken pack: be safe
        return True


def declared_facility(workspace=None):
    """True when the settings declare something restricted (a restricted folder) or say mode: facility. A settings file that cannot be
    read counts as facility."""
    try:
        s = load(workspace)
    except SettingsError:
        return True
    return s["mode"] == "facility" or any(f.label == "restricted" for f in s["folders"])


def facility_mode(workspace=None):
    """True when anything restricted is declared: a restricted pack is installed, a folder is declared restricted, or the workspace says
    mode: facility (module docstring)."""
    return declared_facility(workspace) or restricted_pack_installed()


CONTAINER_ENV = "AUTOMC_IN_CONTAINER"


def in_container():
    """True inside AutoMC's container (the image sets AUTOMC_IN_CONTAINER=1; Docker also leaves /.dockerenv)."""
    return os.environ.get(CONTAINER_ENV) == "1" or Path("/.dockerenv").exists()


def python_runs_without_asking(workspace=None):
    """python.approval: auto counts only inside a container, which is then the sandbox (W2); anywhere else every run asks."""
    try:
        return load(workspace)["python"]["approval"] == "auto" and in_container()
    except SettingsError:
        return False


def effective(workspace=None):
    """[(setting, value, where it came from)] for `config show`."""
    ws = Path(workspace or automc_paths.WORKSPACE).resolve()
    s = load(ws)
    mine, user = _read(workspace_file(ws), ws), _read(user_file(), ws)
    def src(section, key=None):
        for name, d in ((str(workspace_file(ws)), mine), (str(user_file()), user)):
            if (key is None and section in d) or (key is not None and key in d.get(section, {})):
                return name
        return "default"
    pack = restricted_pack_installed()
    facility = facility_mode(ws)
    why = ("mode: facility in the settings" if s["mode"] == "facility" else
           "a restricted folder is declared" if any(f.label == "restricted" for f in s["folders"]) else
           "a restricted facility pack is installed" if pack else "nothing restricted is declared")
    rows = [("workspace", str(ws), "AUTOMC_WORKSPACE or --workspace" if os.environ.get("AUTOMC_WORKSPACE") else "default"),
            ("mode", f"{'facility' if facility else 'open'} ({why})", src("mode")),
            ("python.approval", s["python"]["approval"] + ("" if s["python"]["approval"] == "ask" or in_container() else
                                                            " (not in a container, so every run still asks)"), src("python", "approval")),
            ("conversations.save_restricted", s["conversations"]["save_restricted"], src("conversations", "save_restricted"))]
    for f in s["folders"]:
        rows.append((f"folder {f.name or '(unnamed)'}", f"{f.path} ({f.label or 'unknown'}"
                     f"{'' if f.path.is_dir() else ', missing'})", f.source))
    for lk in s["links"]:
        rows.append((f"link {lk.name}", f"{lk.path} (read only{'' if lk.path.is_dir() else ', missing'})", lk.source))
    return rows, s["warnings"]


def write(data, workspace=None):
    """Write a workspace's settings (workspace upgrade, config commands): checked first, then written atomically under the state
    folder's lock. `data` is the plain mapping of the file. Comments in an existing file are not kept."""
    import yaml
    from tools.openmc import jobs
    ws = Path(workspace or automc_paths.WORKSPACE)
    path = workspace_file(ws)
    text = ("# AutoMC workspace settings (src/automc_settings.py explains every key). Checked when read; `python -m automc config show`\n"
            "# prints what is in effect.\n" + yaml.safe_dump({"format": FORMAT, **data}, sort_keys=False, allow_unicode=True))
    tmp = path.with_name(f".{FILE}.{os.getpid()}.{time.time_ns()}.tmp")
    path.parent.mkdir(parents=True, exist_ok=True)
    with jobs._locked(path.parent, "settings.lock"):
        tmp.write_text(text, encoding="utf-8")
        try:
            _read(tmp, ws)                                  # never write a file AutoMC would refuse
        except SettingsError:
            tmp.unlink(missing_ok=True)
            raise
        tmp.replace(path)
    _CACHE.clear()
    return path


def raw(workspace=None):
    """The workspace's settings file as a plain mapping (for changing and writing it back), {} when there is none."""
    path = workspace_file(workspace)
    if not path.is_file():
        return {}
    import yaml
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    data.pop("format", None)
    return data


def summary_json(workspace=None):
    """The settings as JSON-ready data (paths as text), for tools and tests."""
    s = load(workspace)
    return json.loads(json.dumps({**s, "folders": [vars(f) for f in s["folders"]], "links": [vars(lk) for lk in s["links"]]},
                                 default=str))
