"""
Library profiles, provenance and environment preflight. Standard library only: the parent process never imports CoreForge or
OpenMC. A profile is a set of pinned snapshot folders that the worker process puts first on its import path.

    pnt_3el             CoreForge pnt_3el @ dcfc111 + progression_problems 3el_spec @ 1f31371 (a matching pair), with the reflector fix
    pnt_3el+detectors   the same, plus the detector change to the OpenMC reactor builder, rebased onto that pair. Restricted: it comes
                        from a facility pack (the restricted NETL pack), not from this file.

Public profiles are defined here and their snapshots live in AutoMC/library_snapshots. A facility pack can add profiles (pack.yaml,
provides.library_profiles) whose snapshots and patches live in the pack ("pack:") or in AutoMC/library_snapshots ("snapshots:").

The reflector fix (2026-10-23): the library's graphite reflector cell did not exclude the core shroud, so the two cells overlapped from
z = -27.94 to +3.33 cm (found by `openmc -g`). The same definition is upstream on main and pnt_3el; the fix is one clause.

Snapshots are made by library_snapshots/make_snapshots.py and are never edited by hand.
"""
from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

import automc_paths

from . import packs
from .errors import MissingLibrary

SRC = automc_paths.SRC                           # the import root the worker needs
SNAPSHOTS_DIR = automc_paths.SNAPSHOTS_DIR       # tests may point this elsewhere

REFLECTOR_FIX = "snapshots:patches/coreforge_reflector_excludes_shroud_dcfc111.patch"
DETECTOR_PROFILE = "pnt_3el+detectors"              # provided by a facility pack
PROFILES = {
    "pnt_3el": {
        "snapshots": ("snapshots:CoreForge_dcfc111+fixes", "snapshots:progression_problems_1f31371"),
        "commits": {"CoreForge": "pnt_3el@dcfc111 (2026-10-05) + reflector fix", "progression_problems": "3el_spec@1f31371 (2026-10-05)"},
        "patches": (REFLECTOR_FIX,),
    },
}
# the package folder each profile directory must provide, to check a worker really imported from the snapshot
_PACKAGE_DIRS = {0: "coreforge", 1: "progression_problems"}


def profile_for(spec):
    """The profile a spec needs: the detector builder when the barite region and detector cells are wanted, else the plain pair."""
    return DETECTOR_PROFILE if (spec.environment.barite or spec.environment.detector_geometry) else "pnt_3el"


def _profile(profile, facility="netl", preset=None):
    """(definition, pack or None). Code profiles first, then the installed facility packs. Raises MissingLibrary if unknown."""
    if profile in PROFILES:
        return PROFILES[profile], None
    pack, prof = packs.installed().find(facility, preset, "library_profiles", profile)
    if prof is None:
        known = sorted(set(PROFILES) | packs.installed().names(facility, preset, "library_profiles"))
        hint = (" It comes from a restricted facility pack, which is not installed (AUTOMC_FACILITY_PACKS, or "
                "AutoMC_restricted/facility_packs next to AutoMC)." if profile == DETECTOR_PROFILE else "")
        raise MissingLibrary(f"unknown library profile {profile!r}; known: {known}.{hint}")
    return prof, pack


def _locate(ref, pack):
    """A "snapshots:" or "pack:" reference -> (name recorded in provenance, absolute path)."""
    if ref.startswith("snapshots:"):
        rel = ref[len("snapshots:"):]
        return rel, SNAPSHOTS_DIR / rel
    rel = ref[len("pack:"):]
    return rel, pack.folder / rel


def profile_paths(profile, facility="netl", preset=None):
    """Absolute snapshot folders for a profile, in import order. Raises MissingLibrary if any is absent."""
    prof, pack = _profile(profile, facility, preset)
    paths = [_locate(r, pack)[1] for r in prof["snapshots"]]
    missing = [p for p in paths if not p.is_dir()]
    if missing:
        how = (f"python {SNAPSHOTS_DIR / 'make_snapshots.py'}" if pack is None else
               f"python {SNAPSHOTS_DIR / 'make_snapshots.py'} --pack {pack.folder}")
        raise MissingLibrary(f"library profile {profile!r} is not built: missing {[str(p) for p in missing]}. Run: {how}")
    return paths


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def provenance(profile, facility="netl", preset=None):
    """
    What a result records about the libraries: profile name, commits, snapshot folders, and every patch with its checksum, in the order
    applied. `patch_sha256` is one fingerprint of all patches together (SHA-256 of their bytes, concatenated in order).
    """
    paths = profile_paths(profile, facility, preset)
    prof, pack = _profile(profile, facility, preset)
    out = {"name": profile, "commits": dict(prof["commits"]), "snapshots": [p.name for p in paths], "patches": [],
           "patch": None, "patch_sha256": None}
    joined = hashlib.sha256()
    for ref in prof["patches"]:
        name, patch = _locate(ref, pack)
        if not patch.is_file():
            raise MissingLibrary(f"patch file for profile {profile!r} not found: {patch}")
        out["patches"].append({"file": name, "sha256": sha256_file(patch)})
        joined.update(patch.read_bytes())
    if out["patches"]:
        out["patch"] = "; ".join(p["file"] for p in out["patches"])
        out["patch_sha256"] = joined.hexdigest()
    return out


def worker_pythonpath(profile, existing=None, facility="netl", preset=None):
    """PYTHONPATH for the worker: the AutoMC import root (src), then the profile's snapshots, then whatever the caller already had."""
    parts = [str(SRC)] + [str(p) for p in profile_paths(profile, facility, preset)]
    if existing:
        parts.append(existing)
    return os.pathsep.join(parts)


def verify_active(profile, paths=None):
    """Run inside the worker: confirm the imported CoreForge and progression_problems come from this profile's snapshots (`paths`, as
    the parent found them; looked up again when not given)."""
    import coreforge
    import progression_problems
    paths = [Path(p) for p in paths] if paths is not None else profile_paths(profile)
    for idx, module in ((0, coreforge), (1, progression_problems)):
        loaded = Path(module.__file__).resolve()
        expected = (paths[idx] / _PACKAGE_DIRS[idx]).resolve()
        if expected not in loaded.parents:
            raise MissingLibrary(f"{module.__name__} was imported from {loaded}, not from {expected}; another version took precedence "
                                 f"(profile {profile!r}). Run the build in a fresh process.")


# ------------------------------------------------------------------------------------------------------------------------
# nuclear data
# ------------------------------------------------------------------------------------------------------------------------
def nuclear_data_info(library="endfb-viii.0", environ=None):
    """
    Locate and fingerprint the cross-section library named by OPENMC_CROSS_SECTIONS. Raises MissingLibrary with advice if it is
    not set or not readable. Returns {library, path, bytes, sha256}.
    """
    environ = os.environ if environ is None else environ
    raw = environ.get("OPENMC_CROSS_SECTIONS")
    if not raw:
        raise MissingLibrary("OPENMC_CROSS_SECTIONS is not set. Example: export OPENMC_CROSS_SECTIONS=~/endfb-viii.0-hdf5/cross_sections.xml")
    path = Path(raw).expanduser()
    if not path.is_file():
        raise MissingLibrary(f"OPENMC_CROSS_SECTIONS points to {path}, which is not a file")
    try:
        with open(path, "rb") as fh:
            head = fh.read(400)
    except OSError as e:
        raise MissingLibrary(f"cannot read the cross-section library {path}: {e}") from e
    if b"cross_sections" not in head:
        raise MissingLibrary(f"{path} does not look like a cross_sections.xml file")
    return {"library": library, "path": str(path), "bytes": path.stat().st_size, "sha256": sha256_file(path)}


def python_executable(explicit=None):
    return explicit or os.environ.get("TRIGA_PYTHON") or sys.executable
