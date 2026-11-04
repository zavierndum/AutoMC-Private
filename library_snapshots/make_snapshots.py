"""
Build the pinned library snapshots used by the triga builder's library profiles.

    python make_snapshots.py                 # create anything missing
    python make_snapshots.py --force         # rebuild everything
    python make_snapshots.py --pack DIR      # also make the snapshots a facility pack derives (its pack.yaml, derived_snapshots)

Uses `git archive` on the existing clones, so the clones' working trees and branches are never touched (it only reads
objects). Makes, next to this file:

    CoreForge_dcfc111/                  coreforge/ at pnt_3el commit dcfc111 (2026-10-05)
    progression_problems_1f31371/       progression_problems/ at 3el_spec commit 1f31371 (2026-10-05)
    CoreForge_dcfc111+fixes/            the first, plus the reflector fix (patches/coreforge_reflector_excludes_shroud_dcfc111.patch)

A facility pack can derive its own snapshot from these (pack.yaml, provides.derived_snapshots: a base snapshot and patches, each
"snapshots:<path here>" or "pack:<path in the pack>"). The restricted NETL pack derives CoreForge_dcfc111+detectors (the reflector fix
and its detector patch) that way; `--pack DIR` writes it into the pack, never here.

The two commits are a matching pair: the tip of pnt_3el (2d6862d, 2026-10-09) removed CylindricalStack, which the 3el_spec
snapshot still imports. Line endings are normalized to LF in the snapshots. Each snapshot also gets the repository's LICENSE (BSD 3-Clause). If a commit is missing from a clone, run `git fetch origin` there first.
"""
import argparse
import shutil
import subprocess
import sys
import tarfile
import io
from pathlib import Path

HERE = Path(__file__).resolve().parent
# UT-Research-Repos sits in the same Projects folder as the research workspace (AutoMC/library_snapshots -> Projects).
PROJECTS = HERE.parents[3]                                          # .../Projects
CLONES = PROJECTS / "UT-Research-Repos"

SNAPSHOTS = [
    # (output dir, clone, commit, path inside repo to extract)
    ("CoreForge_dcfc111", CLONES / "CoreForge", "dcfc111", "coreforge"),
    ("progression_problems_1f31371", CLONES / "PROGRESSION-PROPS", "1f31371", "progression_problems"),
]
# Patched snapshots: (output dir, base snapshot, patches applied in order). The base snapshots above stay exactly as upstream has them.
REFLECTOR_FIX = "patches/coreforge_reflector_excludes_shroud_dcfc111.patch"      # reflector overlapped the core (found 2026-10-23)
PATCHED = [
    ("CoreForge_dcfc111+fixes", "CoreForge_dcfc111", [REFLECTOR_FIX]),
]


def archive(clone, commit, subpath, dest):
    out = subprocess.run(["git", "-C", str(clone), "archive", commit, subpath], capture_output=True, check=True).stdout
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(out)) as tf:
        try:
            tf.extractall(dest, filter="data")          # Python 3.12+ (and 3.11.4+): safe extraction
        except TypeError:
            tf.extractall(dest)
    for f in dest.rglob("*.py"):                      # the repos store CRLF; normalize so patches apply exactly
        data = f.read_bytes()
        if b"\r\n" in data:
            f.write_bytes(data.replace(b"\r\n", b"\n"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="rebuild everything, the upstream snapshots too")
    ap.add_argument("--rebuild-patched", action="store_true", help="rebuild only the patched snapshots (after a patch changed)")
    ap.add_argument("--pack", action="append", default=[], metavar="DIR",
                    help="a facility pack folder whose derived_snapshots to make (inside the pack); may be repeated")
    args = ap.parse_args()
    for name, clone, commit, sub in SNAPSHOTS:
        dest = HERE / name
        if dest.exists() and not args.force:
            print(f"exists   {name}")
            continue
        if dest.exists():
            shutil.rmtree(dest)
        if not clone.exists():
            sys.exit(f"clone not found: {clone}")
        archive(clone, commit, sub, dest)
        lic = subprocess.run(["git", "-C", str(clone), "show", f"{commit}:LICENSE"], capture_output=True, check=True).stdout
        (dest / "LICENSE").write_bytes(lic.replace(b"\r\n", b"\n"))      # BSD 3-Clause: redistribution must keep the notice
        print(f"created  {name}  ({sum(1 for _ in dest.rglob('*.py'))} python files, from {commit})")
    for name, base, patches in PATCHED:
        dest = HERE / name
        if dest.exists() and not (args.force or args.rebuild_patched):
            print(f"exists   {name}")
            continue
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(HERE / base, dest)
        for patch in patches:
            subprocess.run(["git", "-c", "core.autocrlf=false", "apply", "-p1", str(HERE / patch)], cwd=dest, check=True)
        print(f"created  {name}  ({len(patches)} patch(es): {', '.join(Path(x).name for x in patches)})")
    for pack in args.pack:
        derive_from_pack(Path(pack), rebuild=args.force or args.rebuild_patched)


def derive_from_pack(pack, rebuild=False):
    """Make the snapshots a facility pack derives (pack.yaml, provides.derived_snapshots) inside that pack."""
    import yaml
    meta = yaml.safe_load((pack / "pack.yaml").read_text(encoding="utf-8"))

    def locate(ref):
        if ref.startswith("snapshots:"):
            return HERE / ref[len("snapshots:"):]
        if ref.startswith("pack:"):
            return pack / ref[len("pack:"):]
        sys.exit(f"{pack.name}: {ref!r} must start with snapshots: or pack:")

    derived = (meta.get("provides") or {}).get("derived_snapshots") or {}
    if not derived:
        print(f"{meta['pack']}: no derived_snapshots")
    for rel, how in derived.items():
        dest, base = pack / rel, locate(how["base"])
        if dest.exists() and not rebuild:
            print(f"exists   {meta['pack']}: {rel}")
            continue
        if not base.is_dir():
            sys.exit(f"base snapshot not found: {base}; make the snapshots here first")
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(base, dest)
        for ref in how["patches"]:
            subprocess.run(["git", "-c", "core.autocrlf=false", "apply", "-p1", str(locate(ref))], cwd=dest, check=True)
        print(f"created  {meta['pack']}: {rel}  ({len(how['patches'])} patch(es): {', '.join(Path(x).name for x in how['patches'])})")


if __name__ == "__main__":
    main()
