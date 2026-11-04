"""
Refuse an AutoMC image that holds what must never be published (devops/AutoMC_Workspace_Plan.md, WD-5): a restricted facility pack,
AutoMC's devops/ or a workspace, a keys file or key, or a personal identifier (the Portkey workspace slug, a UT EID). AutoSAM's image is
public on Docker Hub; AutoMC's may be too, so this runs on every image before it is pushed or shared.

    python docker/check_image.py automc:0.8.0        exit 0: clean; exit 1: what it found; exit 2: the image could not be read

It lists every file of the image (docker run --entrypoint find) and searches AutoMC's own folder for the identifiers. Standard library.
"""
from __future__ import annotations

import fnmatch
import subprocess
import sys

APP = "/opt/automc"
# anywhere in the image
NEVER = [("*AutoMC_restricted*", "a restricted facility packs folder"), ("*netl_restricted*", "the restricted NETL pack"),
         ("*/keys.env", "a keys file"), ("*/.automc_jupyter_token", "a Jupyter token")]
# in AutoMC's own folder
NEVER_IN_APP = [(f"{APP}/devops*", "AutoMC's devops/ (names people, quotes their data)"), (f"{APP}/workspace*", "a workspace"),
                (f"{APP}/downloads*", "downloads (nuclear data is mounted, never built in)"), ("*/.env", "a .env file"),
                ("*.env", "an env file"), ("*.pem", "a key or certificate file"), ("*/id_rsa*", "an SSH key"),
                ("*/.automc/*", "a workspace's .automc/ folder")]
# text that must not appear in AutoMC's files (Perl regular expressions): the shape of a Portkey workspace slug; a key, except the
# tests' and evaluations' made-up ones (sk-test-..., sk-eval-..., sk-file-...); the author's EID and first name (code is publishable)
IDENTIFIERS = [r"@mech-[a-z0-9]+-[a-z]{3}-[0-9a-f]{6}", r"sk-(?!test-|eval-|file-)[A-Za-z0-9_-]{20,}", r"\bzn2366\b", r"\bZavier\b"]


def files(image):
    p = subprocess.run(["docker", "run", "--rm", "--entrypoint", "find", image, "/", "-xdev", "(", "-path", "/proc", "-o", "-path",
                        "/sys", "-o", "-path", "/dev", ")", "-prune", "-o", "-print"], capture_output=True, text=True)
    if p.returncode not in (0, 1) or not p.stdout:
        raise RuntimeError(f"cannot list the image's files: {p.stderr.strip()[:300]}")
    return p.stdout.splitlines()


def identifiers(image):
    pattern = "|".join(IDENTIFIERS)
    p = subprocess.run(["docker", "run", "--rm", "--entrypoint", "grep", image, "-rIlP", pattern, APP], capture_output=True, text=True)
    return [line for line in p.stdout.splitlines() if line.strip()]


def check(image):
    found = []
    every = files(image)
    for path in every:
        for pat, why in NEVER:
            if fnmatch.fnmatch(path, pat):
                found.append(f"{path}: {why}")
        if path.startswith(APP):
            for pat, why in NEVER_IN_APP:
                if fnmatch.fnmatch(path, pat):
                    found.append(f"{path}: {why}")
    found += [f"{path}: holds a personal identifier or a key (a Portkey slug, an EID, sk-...)" for path in identifiers(image)]
    return len(every), found


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print(__doc__.strip())
        return 2
    try:
        n, found = check(argv[0])
    except (RuntimeError, OSError) as e:
        print(f"could not check {argv[0]}: {e}", file=sys.stderr)
        return 2
    if found:
        print(f"{argv[0]}: NOT clean ({len(found)} finding(s) in {n:,} files):")
        for f in found[:200]:
            print(f"  {f}")
        return 1
    print(f"{argv[0]}: clean ({n:,} files checked; no restricted pack, devops/, workspace, keys or personal identifier)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
