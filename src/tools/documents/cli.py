"""
Knowledge bases from the command line (WSL, automc environment). From AutoMC/src:

    python -m automc docs ingest FOLDER [--name NAME] [--text-mode nougat|lightweight] [--redo FILE ...] [--vision CHOICE]
                                        show the plan, ask y/n, start; the command returns at once and the run goes on. Run it again
                                        after adding files: only the new ones are parsed
    python -m automc docs list                       every knowledge base, and the run going on
    python -m automc docs check [JOB_ID] [--kb KB]   a run, or a knowledge base
    python -m automc docs watch JOB_ID               follow a run until it ends; Ctrl-C stops watching, not the run
    python -m automc docs search WORDS [--kb KB] [--kinds text,visual] [--max N]
    python -m automc docs cancel JOB_ID

The vision model: this machine's (Ollama) unless the folder is public and --vision names a cloud model, as `python -m automc models
discover` lists them (openai/gpt-5.4, openai-portkey/@<slug>/gpt-5.4, gemini/gemini-3.8-flash). Embedding is always on this machine
(Ollama's nomic-embed-text). Add --json for machine-readable output.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
import time

import console
from console import BOLD, CYAN, DIM, GREEN, RED, YELLOW, paint
from tools.files.places import Refused

from . import parse, search

EXCERPT = 700                                         # characters of each search result shown, unless --full


def _show(text):
    """A block of console.py text, its banner rules in colour on a terminal."""
    lines = text.split("\n")
    rule = [bool(line) and set(line) == {"="} for line in lines]
    shown = [paint(line, CYAN) if rule[i] else
             paint(line, BOLD) if 0 < i < len(lines) - 1 and rule[i - 1] and rule[i + 1] else line
             for i, line in enumerate(lines)]
    print("\n".join(shown))


def _ask(plan, yes, out=None):
    if out is None:
        _show(plan["text"])
    else:
        print(plan["text"], file=out)
    if yes:
        return True
    if not sys.stdin.isatty():
        raise Refused("approval", "there is no terminal to ask on; give --yes to approve this run", "NeedsApproval", 3)
    return input("\nStart it? [y/N] ").strip().lower() in ("y", "yes")


def _when(iso):
    try:
        return _dt.datetime.fromisoformat(iso).astimezone().strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        return ""


def _status(s):
    return paint(s, GREEN if s in ("success", "ready") else RED if s in ("failed", "interrupted") else YELLOW if s == "cancelled" else "")


def _progress(j):
    prog = j.get("progress") or {}
    return "; ".join(f"{k} {v}" for k, v in prog.items() if k != "last_log_line")


def _job_title(j):
    return (f"Run {j['job_id']}   knowledge base {j['kb']}   {j.get('files') or 0} file(s)   "
            f"vision {(j.get('vision') or {}).get('route')}")


def _print_job(j):
    _show(console.banner(_job_title(j)))
    print(console.fields([("Status", _status(j["status"])), ("Progress", _progress(j)),
                          ("Time", parse._duration(j["seconds_so_far"]) if j.get("seconds_so_far") is not None else None),
                          ("Log", (j.get("progress") or {}).get("last_log_line")), ("Error", j.get("error")), ("Next", j.get("next"))]))


def _print_kb(d):
    ix = d.get("index") or {}
    _show(console.banner(f"Knowledge base {d['kb']}   {d['status']}"))
    run = d.get("last_run") or {}
    last = (f"{run.get('status')}: {len(run.get('parsed') or [])} parsed, {len(run.get('failed') or [])} failed, "
            f"{run.get('embedded', 0)} rows embedded, {parse._duration(run.get('seconds') or 0)}") if run else None
    index = (f"{ix.get('documents', 0)} document(s), {ix.get('text_rows', 0)} text rows, {ix.get('visual_rows', 0)} visual rows, "
             f"{ix.get('vectors', 0)} vectors" + (f", {ix['waiting']} rows not embedded yet" if ix.get("waiting") else "")) if ix else None
    print(console.fields([("Folder", d.get("folder")), ("Label", d.get("label_reason")), ("Text", d.get("text_mode")),
                          ("Vision", (d.get("vision") or {}).get("where")), ("Embedding", ix.get("embedding_model")),
                          ("Index", index), ("Last run", last), ("Updated", _when(d.get("updated_at"))), ("Error", d.get("error"))]
                         + [("Warning", w) for w in run.get("warnings") or []]))
    titles = {doc.get("source"): doc.get("title") for doc in d.get("documents") or []}
    files = d.get("files") or []
    if files:
        print()
        print(console.table(["Status", "Pages", "File", "Title"],
                            [[f["status"], f.get("pages") or "", f.get("relpath") or f["name"], titles.get(f["name"]) or ""] for f in files],
                            right={1}, limits={2: 50, 3: 60}))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m automc docs", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)
    p = sub.add_parser("ingest", help="show the plan, ask, start")
    p.add_argument("folder")
    p.add_argument("--name")
    p.add_argument("--text-mode", choices=parse.TEXT_MODES)
    p.add_argument("--redo", nargs="+", default=[], metavar="FILE", help="parse these files again (a plan lists the changed ones)")
    p.add_argument("--vision", help="a cloud model for a public folder's page images (default: this machine's)")
    p.add_argument("--yes", action="store_true", help="approve without asking (you typed the command)")
    p.add_argument("--json", action="store_true")
    sub.add_parser("list")
    c = sub.add_parser("check")
    c.add_argument("job_id", nargs="?")
    c.add_argument("--kb")
    c.add_argument("--json", action="store_true")
    w = sub.add_parser("watch")
    w.add_argument("job_id")
    s = sub.add_parser("search")
    s.add_argument("words", nargs="+")
    s.add_argument("--kb")
    s.add_argument("--kinds", help="comma list of text, visual")
    s.add_argument("--max", type=int, default=8)
    s.add_argument("--full", action="store_true", help=f"show each row whole (default: its first {EXCERPT} characters)")
    s.add_argument("--json", action="store_true")
    x = sub.add_parser("cancel")
    x.add_argument("job_id")
    a = ap.parse_args(argv)
    try:
        if a.command == "ingest":
            provider = None
            if a.vision:
                from agent import discovery
                try:
                    provider = discovery.resolve(a.vision, require_checked=False)
                except Exception as e:                         # an unknown choice, a provider not set up
                    raise Refused("--vision", str(e), "InvalidArgument", 2) from None
            plan = parse.plan_parse(a.folder, a.name, a.text_mode, a.redo, provider=provider)
            if plan["nothing_to_do"]:
                _show(plan["text"])
                print(paint("\n  Nothing new to parse or embed.", GREEN))
                return 0
            if not _ask(plan, a.yes, sys.stderr if a.json else None):      # with --json, standard output carries only the JSON
                print("not started")
                return 1
            job = parse.start(plan, approved_by="cli")
            if a.json:
                print(json.dumps(job, indent=2))
            else:
                print()
                _print_job(job)
                print(f"\n  Started; the run goes on. Follow it: python -m automc docs watch {job['job_id']}")
        elif a.command == "list":
            out = parse.check()
            for j in out["active"]:
                _print_job(j)
                print()
            _show(console.banner(f"Knowledge bases ({len(out['knowledge_bases'])})   workspace/{parse.DOCS}/"))
            if out["knowledge_bases"]:
                print(console.table(["Name", "Status", "Files", "Label", "Updated", "Folder"],
                                    [[k["kb"], k["status"], k["files"], k["label"], _when(k["updated_at"]), k["folder"]]
                                     for k in out["knowledge_bases"]], right={2}))
            else:
                print("  none yet: python -m automc docs ingest FOLDER")
        elif a.command == "check":
            out = parse.check(job_id=a.job_id, kb=a.kb)
            if a.json:
                print(json.dumps(out, indent=2, default=str))
            elif a.job_id:
                _print_job(out)
                if out.get("kb_summary"):
                    print()
                    _print_kb(out["kb_summary"])
            elif a.kb:
                _print_kb(out)
            else:
                return main(["list"])
        elif a.command == "watch":
            last, log = None, None
            try:
                out = parse.check(job_id=a.job_id)
                _show(console.banner(_job_title(out)))
                print(paint(f"  {'Time':>6}  {'Status':<9}  Progress", DIM))
                while True:
                    shown = (out["status"], _progress(out))
                    if shown != last:
                        t = parse._duration(out["seconds_so_far"]) if out.get("seconds_so_far") is not None else ""
                        print(f"  {t:>6}  {_status(out['status'])}{' ' * max(0, 9 - len(out['status']))}  {_progress(out)}")
                        last = shown
                    line = (out.get("progress") or {}).get("last_log_line")
                    if line and line != log and set(line) != {"="}:
                        print(paint(f"  {'':>6}  {'':<9}  log: {line[:console.width() - 25]}", DIM))
                        log = line
                    if out["status"] not in ("queued", "running"):
                        if out.get("error"):
                            print(paint(f"  error: {out['error']}", RED))
                        if out.get("kb_summary"):
                            print()
                            _print_kb(out["kb_summary"])
                        break
                    time.sleep(3)
                    out = parse.check(job_id=a.job_id)
            except KeyboardInterrupt:
                print("\nstopped watching; the run goes on")
        elif a.command == "search":
            query = " ".join(a.words)
            out = search.search(query, kb=a.kb, kinds=a.kinds.split(",") if a.kinds else None, max_results=a.max)
            if a.json:
                print(json.dumps(out, indent=2))
            else:
                how = "by meaning" if any(r.get("score") is not None for r in out["results"]) else "by full text"
                _show(console.banner(f"Search: {query[:70]}{'...' if len(query) > 70 else ''}   ({how}, "
                                     f"{', '.join(out['searched']) or 'no knowledge base'})"))
                rows = out["results"]
                if rows:
                    print(console.table(["#", "Score", "Kind", "Page", "File", "Knowledge base"],
                                        [[i, f"{r['score']:.3f}" if r.get("score") is not None else "-", r["kind"],
                                          r["page"] if r.get("page") is not None else "image", r["file"], r["kb"]]
                                         for i, r in enumerate(rows, 1)], right={0, 1}, limits={4: 50}))
                for i, r in enumerate(rows, 1):
                    where = f"page {r['page']}" if r.get("page") is not None else "image"
                    score = f", score {r['score']:.3f}" if r.get("score") is not None else ""
                    print()
                    print(paint(f"  [{i}] {r['file']}, {where}, {r['kind']}{score}" + (f"   ({r['title']})" if r.get("title") else ""), BOLD))
                    print(console.wrap(console.plain_markdown(r["text"]), indent=6, limit=None if a.full else EXCERPT))
                    if r.get("cut"):
                        print(paint(f"      ({r['cut']})", DIM))
                notes = list(out.get("notes") or []) + ([out["note"]] if out.get("note") else [])
                if notes:
                    print()
                for n in notes:
                    print(paint(console.wrap(f"note: {n}", indent=2), YELLOW))
                if rows and not a.full:
                    print(paint(f"\n  Rows are shown to {EXCERPT} characters; --full shows them whole, --json as data.", DIM))
        elif a.command == "cancel":
            _print_job(parse.cancel(a.job_id))
    except Refused as e:
        print(f"{e.kind}: {e}", file=sys.stderr)
        return e.exit_code
    return 0
