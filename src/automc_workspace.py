"""
The workspace's layout (devops/AutoMC_Workspace_Plan.md, sections 3 and 8, WD-2): everything AutoMC keeps in a workspace lives in one
hidden folder, .automc/, beside the user's cases and runs. Workspaces made before 0.8.0 kept eight hidden items at their root instead;
`python -m automc workspace upgrade` moves them in.

    python -m automc workspace upgrade --dry-run    what would move and change; nothing is touched
    python -m automc workspace upgrade              the same, asks, then does it
    python -m automc workspace usage                what takes space: cases and runs, and each part of .automc/
    python -m automc config show [--json]           every setting in effect and where it came from (automc_settings)

The upgrade:
  - moves each old item into .automc/ (chats, documents, the run registry and its sequence folders, Python, web, model checks); old
    chat audits go to .automc/chats/legacy/ (kept, not loadable as conversations);
  - rewrites what pointed at the old places: each job's folder in the run registry; each knowledge base's source folder becomes a
    reference (tools/files/places.to_ref) and, when it lies outside the workspace, its folder is declared in settings.yaml with the
    label it had (from AUTOMC_PUBLIC_ROOTS, AUTOMC_FILE_ROOTS or the knowledge base's own label);
  - writes mode: facility into settings.yaml when the workspace holds anything restricted or of unknown origin (a case, a knowledge
    base, a job) or a restricted pack is installed, so that removing a pack later never opens the workspace;
  - writes .automc/format.json;
  - refuses while a run or a parse is active, deletes nothing, and can be run again safely (it moves only what is still there).
Close every AutoMC chat of the workspace first: a running chat's Python kernel works in the old folder.

Standard library and PyYAML (for settings.yaml).
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

import automc_paths
import automc_settings
import automc_version

FORMAT = 1
FORMAT_FILE = "format.json"
# old item at the workspace root -> its place in .automc/
LEGACY = {".automc_chats": "chats/legacy", ".automc_documents": "documents", ".automc_jobs.json": "jobs/registry.json",
          ".automc_jobs.lock": "jobs/registry.lock", ".automc_jobs": "jobs", ".automc_models": "models", ".automc_python": "python",
          ".automc_web": "web"}
DOC_LEGACY = {".automc_jobs.json": "registry.json", ".automc_jobs.lock": "registry.lock"}       # inside the documents folder
CASE_RECORDS = ("result.json", "sweep.json")


class LayoutError(Exception):
    """The workspace still has the layout of an older AutoMC; the message says how to upgrade it."""


def legacy_items(workspace=None):
    ws = Path(workspace or automc_paths.WORKSPACE)
    return [name for name in LEGACY if (ws / name).exists()]


def check(workspace=None):
    """Raise LayoutError when the workspace still holds the old hidden items."""
    old = legacy_items(workspace)
    if old:
        raise LayoutError(f"the workspace {Path(workspace or automc_paths.WORKSPACE)} has the layout of AutoMC before 0.8.0 "
                          f"({', '.join(old)}). Close every AutoMC chat of it, then run: python -m automc workspace upgrade --dry-run "
                          f"(to see what moves), and python -m automc workspace upgrade")


def ensure_format(workspace=None):
    """Write .automc/format.json for a workspace that exists and has none yet."""
    ws = Path(workspace or automc_paths.WORKSPACE)
    path = ws / automc_paths.STATE / FORMAT_FILE
    if ws.is_dir() and not path.is_file() and not legacy_items(ws):
        _write_json(path, {"format": FORMAT, "automc_version": automc_version.VERSION, "created_at": _now()})


def _now():
    from tools.openmc import jobs
    return jobs.now()


def _write_json(path, data):
    from tools.openmc import jobs
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    jobs.atomic_write_json(path, data)


def _read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


# ----------------------------------------------------------------------------------------------------------------------
# what the workspace holds
# ----------------------------------------------------------------------------------------------------------------------
def _cases(ws):
    """(relative folder, label) of every case and sweep in the workspace (folders with result.json or sweep.json)."""
    out = []
    for root, dirs, files in os.walk(ws):
        rel = Path(root).relative_to(ws)
        dirs[:] = [d for d in dirs if not d.startswith(".automc") and not d.startswith(".")]
        if any(f in files for f in CASE_RECORDS):
            label = "unknown"
            for name in CASE_RECORDS:
                data = _read_json(Path(root) / name, {})
                fd = data.get("facility_data") if isinstance(data, dict) else None
                if isinstance(fd, dict) and fd.get("distribution"):
                    label = fd["distribution"]
                    break
                if isinstance(fd, str) and fd:
                    label = fd
                    break
            out.append((rel.as_posix(), label))
            dirs[:] = [d for d in dirs if not d.startswith("run-")]
    return out


def _kb_records(docs):
    return [(f, rec) for f in sorted(docs.iterdir()) if f.is_dir() for rec in [_read_json(f / "kb.json")] if isinstance(rec, dict)] \
        if docs.is_dir() else []


def _not_public(label):
    return label not in ("public",)


# ----------------------------------------------------------------------------------------------------------------------
# the upgrade
# ----------------------------------------------------------------------------------------------------------------------
def plan_upgrade(workspace=None):
    """What `workspace upgrade` would do: {workspace, moves, rewrites, settings, blockers, notes, needed}. Touches nothing."""
    ws = Path(workspace or automc_paths.WORKSPACE).resolve()
    state = ws / automc_paths.STATE
    moves, rewrites, blockers, notes = [], [], [], []
    for old, new in LEGACY.items():
        src = ws / old
        if not src.exists():
            continue
        if old == ".automc_jobs" or old == ".automc_chats":
            for child in sorted(src.iterdir()):
                dest = state / ("chats" if child.name == ".history" else new) / child.name
                moves.append((src / child.name, dest))
        else:
            moves.append((src, state / new))
    docs_old, docs_new = ws / ".automc_documents", state / "documents"
    docs = docs_old if docs_old.is_dir() else docs_new
    for old, new in DOC_LEGACY.items():
        if (docs / old).exists():
            rewrites.append(f"documents/{old} becomes documents/{new}")
    registry = ws / ".automc_jobs.json" if (ws / ".automc_jobs.json").is_file() else state / "jobs" / "registry.json"
    jobs_list = _read_json(registry, []) or []
    active = [j for j in jobs_list if j.get("status") in ("queued", "running", "paused")]
    if active:
        blockers.append(f"{len(active)} run(s) active ({', '.join(j['job_id'] for j in active[:5])}): let them finish, or stop them")
    parses = _read_json(docs / ".automc_jobs.json", None) or _read_json(docs / "registry.json", []) or []
    active_parses = [j for j in parses if j.get("status") in ("queued", "running", "paused")]
    if active_parses:
        blockers.append(f"{len(active_parses)} document parse(s) active: let them finish, or cancel them")
    old_folders = [j for j in jobs_list if str(j.get("job_folder") or "").startswith(".automc_jobs/")]
    if old_folders:
        rewrites.append(f"{len(old_folders)} job(s) in the run registry point at .automc_jobs/: they will point at .automc/jobs/")
    # the settings: the knowledge bases' source folders, and the mode
    current = automc_settings.raw(ws)
    folders = list(current.get("folders") or [])
    declared = {f.get("name") for f in folders}
    env_folders, _ = automc_settings._env_folders()
    kb_changes = []
    labels = []
    for f, rec in _kb_records(docs):
        labels.append(rec.get("label") or "unknown")
        src_text = str(rec.get("folder") or "")
        if not src_text or src_text.startswith(("ws:", "folder:")):
            continue
        src = Path(src_text)
        if src == ws or ws in src.parents:
            kb_changes.append((f.name, "ws:" + src.relative_to(ws).as_posix()))
            continue
        holder = next((e for e in sorted(env_folders, key=lambda e: -len(e.path.parts)) if src == e.path or e.path in src.parents), None)
        base, label = (holder.path, holder.label) if holder else (src, rec.get("label") if rec.get("label") in ("public", "restricted")
                                                                   else None)
        existing = next((x for x in folders if Path(automc_paths.host_path(str(x.get("path")))) == base), None)
        if existing is None:
            name = folder_name(base.name, declared)
            declared.add(name)
            existing = {"name": name, "path": str(base), **({"label": label} if label else {})}
            folders.append(existing)
        rel = src.relative_to(base).as_posix() if src != base else "."
        kb_changes.append((f.name, f"folder:{existing['name']}:{rel}"))
    for name, ref in kb_changes:
        rewrites.append(f"knowledge base {name}: its source folder is recorded as {ref}")
    labels += [lab for _, lab in _cases(ws)] + [str((j.get("facility_data") or {}).get("distribution")
                                                    if isinstance(j.get("facility_data"), dict) else j.get("facility_data") or "unknown")
                                                for j in jobs_list]
    facility = automc_settings.restricted_pack_installed() or any(_not_public(lab) for lab in labels)
    new_settings = dict(current)
    if folders != list(current.get("folders") or []):
        new_settings["folders"] = folders
    if facility and current.get("mode") != "facility":
        new_settings["mode"] = "facility"
        held = sorted({lab for lab in labels if _not_public(lab)})
        notes.append("mode: facility is written into settings.yaml, because " + (
            f"the workspace holds data labelled {', '.join(held)}" if held else "a restricted facility pack is installed"))
    if any(e.source == automc_settings.OLD_ROOTS_ENV for e in env_folders):
        notes.append(f"{automc_settings.OLD_ROOTS_ENV} is set: rename it AUTOMC_ROOTS in ~/.bashrc (or declare the folders in "
                     f"settings.yaml and remove the export)")
    if (ws / ".automc_python").is_dir():
        notes.append("close every AutoMC chat of this workspace before the upgrade: a running chat's Python kernel works in the old folder")
    needed = bool(moves or kb_changes or new_settings != current or not (state / FORMAT_FILE).is_file())
    return {"workspace": str(ws), "moves": [(a.relative_to(ws).as_posix(), b.relative_to(ws).as_posix()) for a, b in moves],
            "rewrites": rewrites, "settings": new_settings if new_settings != current else None, "kb_folders": kb_changes,
            "blockers": blockers, "notes": notes, "needed": needed}


def folder_name(base, taken):
    """A settings folder name made from a folder's name, unique among `taken`."""
    import re
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", base).strip("._-")[:60] or "folder"
    if not name[0].isalnum():
        name = "f" + name
    out, n = name, 2
    while out in taken:
        out, n = f"{name}-{n}", n + 1
    return out


def upgrade(workspace=None):
    """Carry out plan_upgrade. Returns the plan with what was done. Raises LayoutError when something blocks it."""
    ws = Path(workspace or automc_paths.WORKSPACE).resolve()
    plan = plan_upgrade(ws)
    if plan["blockers"]:
        raise LayoutError("not upgraded: " + "; ".join(plan["blockers"]))
    state = ws / automc_paths.STATE
    state.mkdir(parents=True, exist_ok=True)
    done = []
    for old, new in plan["moves"]:
        src, dest = ws / old, ws / new
        if not src.exists():
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            raise LayoutError(f"not moved: {new} already exists (move {old} by hand, or remove one of them after checking)")
        shutil.move(str(src), str(dest))
        done.append(f"moved {old} to {new}")
    for old in (".automc_jobs", ".automc_chats"):                       # emptied by the moves above
        if (ws / old).is_dir() and not any((ws / old).iterdir()):
            (ws / old).rmdir()
    docs = state / "documents"
    for old, new in DOC_LEGACY.items():
        if (docs / old).exists() and not (docs / new).exists():
            (docs / old).replace(docs / new)
            done.append(f"renamed documents/{old} to documents/{new}")
    registry = state / "jobs" / "registry.json"
    jobs_list = _read_json(registry, None)
    if isinstance(jobs_list, list):
        changed = 0
        for j in jobs_list:
            jf = str(j.get("job_folder") or "")
            if jf.startswith(".automc_jobs/"):
                j["job_folder"] = f"{automc_paths.STATE}/jobs/" + jf[len(".automc_jobs/"):]
                changed += 1
        if changed:
            _write_json(registry, jobs_list)
            done.append(f"{changed} job folder(s) in the run registry now point into .automc/jobs/")
    if plan["settings"] is not None:
        automc_settings.write(plan["settings"], ws)
        done.append("wrote .automc/settings.yaml")
    for name, ref in plan["kb_folders"]:
        path = docs / name / "kb.json"
        rec = _read_json(path, None)
        if isinstance(rec, dict):
            rec["folder"] = ref
            _write_json(path, rec)
            done.append(f"knowledge base {name}: source folder recorded as {ref}")
    _write_json(state / FORMAT_FILE, {"format": FORMAT, "automc_version": automc_version.VERSION, "upgraded_at": _now(),
                                      "moved": [m for m, _ in plan["moves"]]})
    done.append("wrote .automc/format.json")
    return {**plan, "done": done}


# ----------------------------------------------------------------------------------------------------------------------
# usage
# ----------------------------------------------------------------------------------------------------------------------
def _size(path):
    total = 0
    for root, _, files in os.walk(path):
        for f in files:
            try:
                total += os.lstat(os.path.join(root, f)).st_size
            except OSError:
                pass
    return total


def usage(workspace=None):
    """[(what, bytes, note)]: the cases (with their runs), each part of .automc/, and the rest."""
    ws = Path(workspace or automc_paths.WORKSPACE).resolve()
    state = ws / automc_paths.STATE
    notes = {"chats": "saved conversations and audits", "documents": "knowledge bases (their indexes can be rebuilt; their parses take "
                                                                      "hours)",
             "jobs": "the run registry and sequence folders", "python": "Python runs: code, output, figures, files the code wrote",
             "web": "web downloads (can be fetched again)", "models": "model checks"}
    rows = []
    cases = _cases(ws)
    tops = sorted({c.split("/")[0] for c, _ in cases if c != "."})
    rows.append(("cases and their runs", sum(_size(ws / t) for t in tops), f"{len(cases)} case(s) and sweep(s); never deleted by AutoMC"))
    for name in ("chats", "documents", "jobs", "python", "web", "models"):
        if (state / name).exists():
            rows.append((f".automc/{name}", _size(state / name), notes[name]))
    other = [p for p in ws.iterdir() if p.name not in tops and p.name != automc_paths.STATE] if ws.is_dir() else []
    rows.append(("everything else", sum(_size(p) if p.is_dir() else p.stat().st_size for p in other), "your own files"))
    return rows


# ----------------------------------------------------------------------------------------------------------------------
# the command line
# ----------------------------------------------------------------------------------------------------------------------
def _gb(n):
    return f"{n / 1e9:,.2f} GB" if n >= 1e8 else f"{n / 1e6:,.1f} MB"


def show_plan(plan, out=None):
    import console
    out = out or sys.stdout
    print(console.banner(f"Workspace upgrade   {plan['workspace']}"), file=out)
    if not plan["needed"]:
        print("  nothing to do: the workspace has the current layout", file=out)
        return
    for old, new in plan["moves"]:
        print(f"  move     {old}  ->  {new}", file=out)
    for r in plan["rewrites"]:
        print(f"  rewrite  {r}", file=out)
    if plan["settings"] is not None:
        import yaml
        print("  settings .automc/settings.yaml will hold:", file=out)
        for line in yaml.safe_dump(plan["settings"], sort_keys=False).rstrip().split("\n"):
            print(f"             {line}", file=out)
    for n in plan["notes"]:
        print(f"  note     {n}", file=out)
    for b in plan["blockers"]:
        print(f"  BLOCKED  {b}", file=out)
    print("  nothing is deleted; run results, cases and knowledge bases stay as they are", file=out)


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(prog="python -m automc workspace", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)
    up = sub.add_parser("upgrade")
    up.add_argument("--dry-run", action="store_true", help="show what would move and change; touch nothing")
    up.add_argument("--yes", action="store_true", help="do not ask")
    sub.add_parser("usage")
    a = ap.parse_args(argv)
    try:
        if a.command == "usage":
            import console
            rows = usage()
            print(console.banner(f"Workspace usage   {Path(automc_paths.WORKSPACE).resolve()}"))
            print(console.table(["What", "Size", "Note"], [[w, _gb(b), n] for w, b, n in rows], right={1}))
            return 0
        plan = plan_upgrade()
        show_plan(plan)
        if a.dry_run or not plan["needed"]:
            return 0 if not plan["blockers"] else 3
        if plan["blockers"]:
            return 3
        if not a.yes and input("Upgrade it now? [y/N] ").strip().lower() not in ("y", "yes"):
            print("not upgraded")
            return 1
        out = upgrade()
        for line in out["done"]:
            print(f"  done     {line}")
        return 0
    except (LayoutError, automc_settings.SettingsError) as e:
        print(f"{e}", file=sys.stderr)
        return 3


def config_main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(prog="python -m automc config", description="the settings in effect (automc_settings.py)")
    sub = ap.add_subparsers(dest="command", required=True)
    s = sub.add_parser("show")
    s.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    try:
        rows, warnings = automc_settings.effective()
    except automc_settings.SettingsError as e:
        print(f"{e}", file=sys.stderr)
        return 3
    if a.json:
        print(json.dumps({"settings": [{"setting": k, "value": v, "from": src} for k, v, src in rows], "warnings": warnings},
                         indent=2, default=str))
        return 0
    import console
    print(console.banner("AutoMC settings in effect"))
    print(console.table(["Setting", "Value", "From"], [[k, str(v), src] for k, v, src in rows]))
    for w in warnings:
        print(f"  warning: {w}")
    return 0
