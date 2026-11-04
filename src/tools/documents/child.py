"""
The process of one knowledge-base run (started by parse.start, never by hand): runs the visual-parser command parse.py wrote into
run_request.json, over the staging folder of links, with the environment parse.child_env() gave this process; records every file it
parsed (sha256, a fingerprint of each page) or that failed; brings the index up to Visual-Parser's files and embeds the new rows
(index.py); then writes the done line (AUTOMC_JOB_DONE, the job service's sentinel) at the end of run.log, so the run can be settled
from its files even if nobody watched.

    python -m tools.documents.child --folder workspace/.automc/documents/<kb>
"""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from tools.documents import index, parse
from tools.openmc import jobs


def _read_from(path, offset=0):
    try:
        with open(path, "rb") as fh:
            fh.seek(offset)
            return fh.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def _errors(folder, offset):
    """Visual-Parser's own [ERROR] lines and the last line of a traceback, from run.log and this run's part of 05_pipeline.log."""
    found = []
    for text in (_read_from(Path(folder) / parse.LOG), _read_from(Path(folder) / "05_pipeline.log", offset)):
        found += [l.strip() for l in text.splitlines() if re.search(r"\[ERROR\]|^\S*Error: |ERROR -", l)]
    return [re.sub(r"\s+", " ", e)[:300] for e in found[-5:]]


# Visual-Parser logs a vision answer it could not use as a WARNING and goes on, so a page whose answer was empty or unreadable looks
# like a page without figures. These are found in this run's part of 05_pipeline.log and recorded.
_VISION_TROUBLE = re.compile(r"WARNING visual_parser\.(figure_describer|image_describer|metadata_extractor|pipeline): (.*(?:Could not "
                             r"parse|non-list output|failed|timed out|timeout|empty).*)", re.IGNORECASE)
_WHERE = re.compile(r"(\S+?\.(?:pdf|png|jpe?g|tiff?|bmp))\b[^\n]*?\bp(?:age)?\s?(\d+)\b", re.IGNORECASE)


def vision_trouble(folder, offset=0):
    """{where: ['file p2', ...], lines} of the vision answers Visual-Parser could not use in this run."""
    lines = [m.group(2).strip() for m in _VISION_TROUBLE.finditer(_read_from(Path(folder) / "05_pipeline.log", offset))]
    where = list(dict.fromkeys(f"{m.group(1)} p{m.group(2)}" for l in lines for m in [_WHERE.search(l)] if m))
    return {"where": where, "lines": [re.sub(r"\s+", " ", l)[:200] for l in lines[-5:]]}


def _write(folder, **fields):
    """kb.json, by the folder this process was given (never by name: the workspace is the parent's to know)."""
    rec = {**(jobs.read_json(folder / parse.RECORD) or {}), **fields}
    jobs.atomic_write_json(folder / parse.RECORD, rec)
    return rec


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m tools.documents.child")
    ap.add_argument("--folder", required=True)
    a = ap.parse_args(argv)
    folder = Path(a.folder)
    request = jobs.read_json(folder / parse.REQUEST) or {}
    rec = jobs.read_json(folder / parse.RECORD) or {}
    to_parse = request.get("to_parse") or []
    t = time.time()
    rc = 0
    if request.get("args"):
        try:
            rc = subprocess.run(request["args"], cwd=str(folder), stdin=subprocess.DEVNULL).returncode
        except (FileNotFoundError, OSError) as e:
            print(f"[ERROR] could not run visual-parser: {type(e).__name__}: {e}", flush=True)
            rc = 127
    parse_seconds = round(time.time() - t, 1)
    rec = jobs.read_json(folder / parse.RECORD) or rec            # start() writes the job id once this process is running

    registered = parse._registered(folder)                        # Visual-Parser lists a file only once it parsed it
    files, parsed, failed, now = dict(rec.get("files") or {}), [], [], jobs.now()
    for f in to_parse:
        keep = {"relpath": f["relpath"], "sha256": f["sha256"], "kind": f["kind"], "job_id": rec.get("job_id")}
        if f["name"] in registered:
            fp = parse.fingerprint(Path(request["folder"]) / f["relpath"])
            files[f["name"]] = {**keep, "fingerprint": fp, "pages": (len(fp) if fp and f["kind"] == "pdf" else f.get("page_count") or 1),
                                "status": "parsed", "parsed_at": now}
            parsed.append(f["name"])
        else:
            files[f["name"]] = {**keep, "status": "failed", "failed_at": now}
            failed.append(f["name"])
    for u in request.get("updates") or []:                       # moved, or re-saved with the same content
        if u["name"] in files:
            files[u["name"]].update({k: v for k, v in u.items() if k != "name"})
    _write(folder, files=files)

    warnings, embedded, synced, index_error = [], 0, None, None
    try:
        idx = index.Index(folder, create=True)
        try:
            synced = index.sync(idx, folder)
            try:
                embedded = index.embed_waiting(idx, index.make_embedder(),
                                               progress=lambda d, n: _write(folder, embedding_progress={"done": d, "total": n}))
            except index.EmbedderUnavailable as e:
                warnings.append(f"rows not embedded: {e}. Search uses full text for them until the folder is ingested again")
        finally:
            idx.close()
    except Exception as e:                                        # the index itself (disk, a damaged file)
        index_error = f"the index could not be updated: {type(e).__name__}: {e}"
        print(f"[ERROR] {index_error}", flush=True)

    offset = request.get("pipeline_log_offset") or 0
    errors = _errors(folder, offset)
    error = None
    if to_parse and not parsed:
        error = (errors[-1] if errors else f"visual-parser ended with code {rc}; see {parse.LOG}") if rc != 0 else \
            f"visual-parser finished but parsed none of the files; see {parse.LOG}"
    elif index_error:
        error = index_error
    status = "failed" if error else "success"
    if failed and parsed:
        warnings.insert(0, f"not parsed: {', '.join(failed)}; the next run of the folder tries them again")
    if status == "success":
        warnings += errors
    trouble = vision_trouble(folder, offset)
    if trouble["lines"]:
        where = ", ".join(trouble["where"]) or "some pages"
        warnings.insert(0, f"the vision model gave no usable answer for {where}, so figures there may be missing (the text is not "
                           f"affected); see 05_pipeline.log: {trouble['lines'][0]}")
    run = {"job_id": rec.get("job_id"), "finished_at": jobs.now(), "seconds": round(time.time() - t, 1), "parse_seconds": parse_seconds,
           "pages": sum((files[n].get("pages") or 1) for n in parsed), "parsed": parsed, "failed": failed, "rows": synced,
           "embedded": embedded, "text_mode": rec.get("text_mode"), "vision_route": (rec.get("vision") or {}).get("route"),
           "status": status, "warnings": warnings or None}
    _write(folder, status="ready" if status == "success" else "failed", error=error, warnings=warnings or None,
           runs=(rec.get("runs") or []) + [run], embedding_progress=None, updated_at=jobs.now())
    shutil.rmtree(request.get("stage") or folder / parse.STAGING / "-", ignore_errors=True)   # links only
    sys.stdout.flush()
    print(jobs.sentinel_line(rc if rc != 0 or status == "success" else 1, status, 1), flush=True)
    return 0 if status == "success" else 1


if __name__ == "__main__":
    sys.exit(main())
