"""
The input files some working examples need and AutoMC does not ship (CAD geometries, meshes, ENDF and multipole files; decision A3 of
devops/AutoMC_Authoring_Plan.md). `skills/working_examples/data.json` lists each with its SHA-256, its size and where it comes from: a
URL, or a member of a zip archive read by HTTP range requests (only the member's bytes are fetched). They are kept in the data cache,
AUTOMC_DATA_CACHE or else ~/.cache/automc/data, and every file is checked against its SHA-256 before it is kept.

    python -m automc data list [EXAMPLE|FILE ...] [--json]     what each example needs, and whether the cache has it
    python -m automc data fetch [EXAMPLE|FILE ...]             download what is missing (all files when none is named)
    python -m automc data verify [EXAMPLE|FILE ...]            check the cached files against their SHA-256

Exit codes: 0 ok, 1 a file missing, different or not fetched, 2 a bad request.
"""
import hashlib
import io
import json
import os
import sys
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

import automc_paths

REGISTRY = automc_paths.SRC / "skills" / "working_examples" / "data.json"
USER_AGENT = "AutoMC"
TIMEOUT_S = 120
CHUNK = 1 << 20


class DataError(Exception):
    pass


def cache_dir():
    return Path(os.environ.get("AUTOMC_DATA_CACHE") or Path.home() / ".cache" / "automc" / "data").expanduser()


def registry():
    return json.loads(REGISTRY.read_text(encoding="utf-8"))["files"]


def wanted(targets=None):
    """The registry's files for `targets` (example names or file paths); none named: every file an example uses."""
    files = registry()
    if not targets:
        return {rel: f for rel, f in files.items() if f.get("examples")}
    out = {}
    for t in targets:
        hit = {rel: f for rel, f in files.items() if rel == t or t in f.get("examples", [])}
        if not hit:
            examples = sorted({e for f in files.values() for e in f.get("examples", [])})
            raise DataError(f"{t!r} is neither a data file nor an example that needs one; examples: {', '.join(examples)}")
        out.update(hit)
    return out


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(CHUNK), b""):
            h.update(block)
    return h.hexdigest()


def state(rel, entry):
    """'ok', 'missing' or 'differs' (the cached file is not the one data.json names)."""
    p = cache_dir() / rel
    if not p.is_file():
        return "missing"
    if p.stat().st_size != entry["bytes"] or _sha256(p) != entry["sha256"]:
        return "differs"
    return "ok"


def status(targets=None):
    return [{"file": rel, "bytes": f["bytes"], "state": state(rel, f), "examples": f.get("examples", []), "source": f["source"]}
            for rel, f in sorted(wanted(targets).items())]


def _open(url, start=None, end=None):
    headers = {"User-Agent": USER_AGENT}
    if start is not None:
        headers["Range"] = f"bytes={start}-{end}"
    return urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=TIMEOUT_S)


class _RemoteFile(io.RawIOBase):
    """A file over HTTP that reads only the byte ranges asked for, so zipfile can take one member out of a large archive."""

    def __init__(self, url):
        self.url, self.pos, self.fetched = url, 0, 0
        with _open(url, 0, 0) as r:
            total = (r.headers.get("Content-Range") or "").rsplit("/", 1)[-1]
        if not total.isdigit():
            raise DataError(f"{url} does not answer range requests, so one member cannot be read from it")
        self.size = int(total)

    def readable(self):
        return True

    def seekable(self):
        return True

    def tell(self):
        return self.pos

    def seek(self, offset, whence=io.SEEK_SET):
        self.pos = {io.SEEK_SET: offset, io.SEEK_CUR: self.pos + offset, io.SEEK_END: self.size + offset}[whence]
        return self.pos

    def readinto(self, b):
        if self.pos >= self.size:
            return 0
        end = min(self.pos + len(b), self.size) - 1
        with _open(self.url, self.pos, end) as r:
            data = r.read()
        b[:len(data)] = data
        self.pos += len(data)
        self.fetched += len(data)
        return len(data)


def _download(entry, part):
    """Write the file to `part`; returns the bytes taken from the network."""
    if "zip" in entry:
        remote = _RemoteFile(entry["zip"])
        with zipfile.ZipFile(io.BufferedReader(remote, buffer_size=1 << 16)) as z, open(part, "wb") as out:
            try:
                with z.open(entry["member"]) as member:
                    for block in iter(lambda: member.read(CHUNK), b""):
                        out.write(block)
            except KeyError:
                raise DataError(f"the archive has no member {entry['member']}") from None
        return remote.fetched
    n = 0
    with _open(entry["url"]) as r, open(part, "wb") as out:
        for block in iter(lambda: r.read(CHUNK), b""):
            out.write(block)
            n += len(block)
    return n


def fetch(targets=None, say=print):
    """Download what the cache lacks, check each file's SHA-256, keep it only when it matches. Returns [{file, state, ...}]."""
    root = cache_dir()
    out = []
    for rel, entry in sorted(wanted(targets).items()):
        before = state(rel, entry)
        if before == "ok":
            out.append({"file": rel, "state": "ok", "fetched": 0})
            continue
        dest = root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_name(dest.name + ".part")
        say(f"fetching {rel} ({entry['bytes']:,} bytes) from {entry.get('zip') or entry['url']}")
        try:
            got = _download(entry, part)
        except (OSError, urllib.error.URLError, zipfile.BadZipFile, DataError) as e:
            part.unlink(missing_ok=True)
            out.append({"file": rel, "state": "not fetched", "error": f"{type(e).__name__}: {e}"})
            continue
        sha = _sha256(part)
        if sha != entry["sha256"]:
            part.unlink(missing_ok=True)
            out.append({"file": rel, "state": "not fetched", "error": f"the download's SHA-256 is {sha[:12]}..., not the "
                        f"{entry['sha256'][:12]}... data.json names; it was not kept"})
            continue
        part.replace(dest)
        out.append({"file": rel, "state": "fetched", "fetched": got, "replaced": before == "differs"})
    return out


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(prog="python -m automc data", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["list", "fetch", "verify"])
    ap.add_argument("targets", nargs="*", help="example names or data files (default: all)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    try:
        rows = fetch(a.targets) if a.command == "fetch" else status(a.targets)
    except DataError as e:
        print(e, file=sys.stderr)
        return 2
    if a.json:
        print(json.dumps({"cache": str(cache_dir()), "files": rows}, indent=2))
    else:
        print(f"data cache: {cache_dir()}")
        for r in rows:
            extra = r.get("error") or (f"{r['fetched']:,} bytes downloaded" if r.get("fetched") else "") or \
                ", ".join(r.get("examples", []))
            print(f"  {r['state']:12s} {r['file']:34s} {extra}")
    return 0 if all(r["state"] in ("ok", "fetched") for r in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
