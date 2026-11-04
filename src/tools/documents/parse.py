"""
Knowledge bases from folders, with Visual-Parser (devops/AutoMC_General_Tools_Plan.md, GT-3R), as RADIANT-LLM, AutoSAM and AutoFLUKA
read documents: point at a folder; Visual-Parser parses its new PDFs and images (PNG, JPG, TIFF, BMP) into its knowledge-base files;
the rows are embedded and searched (tools/documents/index.py).

    plan_parse(folder, name, text_mode, redo)  what would be parsed, how, with which vision model, where the page images go; starts nothing
    start(plan, approved_by)                    only after the user said yes through a surface (the chat, the command line)
    check(job_id=None, kb=None)                 a job (settled from its files), a knowledge base, or everything
    cancel(job_id)                              kills the job; what it wrote stays
    kbs()                                       every knowledge base's record

A knowledge base is one folder, workspace/.automc/documents/<name>/ (the name defaults to the document folder's): Visual-Parser's files
(01_chunks_kb.jsonl, 02_visuals_kb.jsonl, 03_metadata_kb.jsonl, image_descriptions_kb.jsonl, its lists of parsed files, its log),
index.sqlite, and kb.json, AutoMC's record: the folder (as a reference that survives moving the workspace: places.to_ref), the label
and why, the text mode, every file parsed (its sha256 and a fingerprint of each page), every run. The document folder is only read: each run hands Visual-Parser a staging folder of links to the
files it parses.

What a run parses: the files not parsed before (Visual-Parser knows a file by its name). A file whose bytes changed is compared page by
page on what Visual-Parser reads (text, page drawing, pictures; annotations such as highlights, metadata and the file's layout left
out): the same content counts as unchanged; changed content is listed, and parsed again (Visual-Parser's --redo) only when the user
names the file. A file with the content of one already parsed under another name, and a second file with a name already used in the
folder, are listed and skipped. A file gone from the folder keeps its rows.

Visual-Parser runs as its own process (its `visual-parser` command, AUTOMC_VISUAL_PARSER to name another), never imported: on import it
loads .env files from places AutoMC does not control (visual_parser/config.py). AutoMC gives that process its environment:
  - a public knowledge base may send its page images to the chat model's cloud provider when that is OpenAI (directly or through
    Portkey) or Gemini (decision T7); only that provider's key goes in;
  - every other one (restricted, or not known to be public) uses the local Ollama vision model, and every cloud key is set empty, which
    also keeps a .env file from supplying one (python-dotenv never overrides a variable that is set);
  - always: VISUAL_PARSER_ENV_FILE naming an empty file, LangChain tracing off, Hugging Face telemetry off, no AUTOMC_ENV_FILE.
Embedding is always local (Ollama's nomic-embed-text). What a knowledge base holds carries its label, so the data guard (agent/guard.py)
treats it as it treats the folder.

Jobs have their own registry (workspace/.automc/documents/registry.json, written by the job service's registry code,
tools/openmc/jobs.py), so they never mix with transport runs. One job at a time: Nougat, the vision model and the embedding model share
the GPU.
"""
from __future__ import annotations

import contextlib
import contextvars
import hashlib
import json
import os
import re
import shutil
import signal
import statistics
import subprocess
import sys
import time
from pathlib import Path

import automc_paths
from tools.files import places
from tools.files.places import Refused
from tools.openmc import jobs

from . import index as kb_index

DOCS = places.DOCUMENTS
PDFS = (".pdf",)
IMAGES = (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp")       # what Visual-Parser takes as images (visual_parser/image_tracker.py)
TEXT_MODES = ("nougat", "lightweight")
RECORD, REQUEST, LOG, EMPTY_ENV, STAGING = "kb.json", "run_request.json", "run.log", "visual_parser_env_empty", ".staging"
REGISTRIES = ("04_processed_pdfs.txt", "parsed_images.txt")      # Visual-Parser's lists of the files it parsed, by name
COMMAND_ENV = "AUTOMC_VISUAL_PARSER"
CHILD_MODULE = "tools.documents.child"
MAX_BYTES = 200_000_000
METADATA_PAGES = 2
SHOWN = 12                                                        # file names a plan shows per group
# Every variable through which Visual-Parser, or a library it loads, could reach a cloud service. For a knowledge base that stays on
# this machine all are set empty.
CLOUD_KEYS = ("OPENAI_API_KEY", "PORTKEY_API_KEY", "PORTKEY_OPENAI_PROVIDER_SLUG", "VISUAL_PARSER_FORCE_PORTKEY", "GEMINI_API_KEY",
              "GOOGLE_API_KEY", "ANTHROPIC_API_KEY", "LANGCHAIN_API_KEY", "LANGSMITH_API_KEY", "TAVILY_API_KEY")
DROPPED = ("AUTOMC_ENV_FILE", "OPENAI_BASE_URL", "OPENAI_API_BASE", "LANGSMITH_ENDPOINT", "LANGCHAIN_ENDPOINT")
QUIET = {"LANGCHAIN_TRACING_V2": "false", "LANGCHAIN_TRACING": "false", "LANGSMITH_TRACING": "false", "LANGSMITH_TRACING_V2": "false",
         "HF_HUB_DISABLE_TELEMETRY": "1", "DO_NOT_TRACK": "1", "PYTHONUNBUFFERED": "1", "PYTHONDONTWRITEBYTECODE": "1",
         "PYTHONWARNINGS": "ignore::FutureWarning"}
SERVICES = {"openai": "OpenAI", "portkey": "OpenAI through Portkey", "gemini": "Google Gemini"}
VP_KEY = {"openai": "OPENAI_API_KEY", "portkey": "PORTKEY_API_KEY", "gemini": "GEMINI_API_KEY"}   # what visual_parser reads
_CHAT = contextvars.ContextVar("automc_chat_provider", default=None)


def docs_dir():
    return Path(automc_paths.WORKSPACE).resolve() / DOCS


def kb_dir(name):
    return docs_dir() / name


def _need_posix():
    if os.name != "posix":
        raise Refused("platform", "parses run in WSL or Linux (the automc environment), where Visual-Parser is installed",
                      kind="MissingLibrary", exit_code=4)


@contextlib.contextmanager
def chat_model(provider):
    """While a tool runs in a conversation: the chat model, whose cloud provider a public knowledge base's page images may use (T7)."""
    token = _CHAT.set(provider)
    try:
        yield
    finally:
        _CHAT.reset(token)


# ----------------------------------------------------------------------------------------------------------------------
# the vision model
# ----------------------------------------------------------------------------------------------------------------------
def cloud_route(provider):
    """How Visual-Parser can reach the chat model's own service for images: {route, vision_provider, model, key_env, slug}, or None
    (a local model, Claude, a provider without a model)."""
    if provider is None or not getattr(provider, "cloud", False) or not provider.model:
        return None
    base = provider.base_url or ""
    if provider.backend == "google_genai":
        return {"route": "gemini", "vision_provider": "gemini", "model": provider.model, "key_env": provider.api_key_env, "slug": None}
    if provider.backend == "openai_compatible" and "portkey.ai" in base:
        slug, _, model = provider.model.partition("/")
        if slug.startswith("@") and model:
            return {"route": "portkey", "vision_provider": "gpt", "model": model, "key_env": provider.api_key_env, "slug": slug[1:]}
    if provider.backend == "openai_compatible" and "api.openai.com" in base:
        return {"route": "openai", "vision_provider": "gpt", "model": provider.model, "key_env": provider.api_key_env, "slug": None}
    return None


def vision_for(label, provider=None, route=None):
    """The vision model for a knowledge base of this label: the chat model's cloud service only for a public one, else local."""
    from agent import keys
    chosen = route if route and route.get("route") != "local" else cloud_route(provider)
    why = None
    if label != "public":
        why = f"the folder is {label}, so its pages stay on this machine"
    elif chosen is None:
        why = "the folder is public, but the chat model is not an OpenAI or Gemini model"
    elif not keys.value(chosen["key_env"]):
        why = f"the folder is public, but {chosen['key_env']} is not set"
    if why:
        return {"route": "local", "vision_provider": "ollama", "model": "", "key_env": None, "slug": None, "why": why,
                "where": "this machine (Ollama; Visual-Parser picks the largest vision model that fits in the free GPU memory)"}
    return {**{k: chosen[k] for k in ("route", "vision_provider", "model", "key_env", "slug")},
            "why": "the folder is public and the chat model is a cloud model (decision T7)",
            "where": f"{SERVICES[chosen['route']]} ({chosen['model']}), with your key"}


def child_env(vision):
    """The environment of the job's process. Never written anywhere."""
    from agent import keys
    env = {k: v for k, v in os.environ.items() if k not in DROPPED}
    env.update({k: "" for k in CLOUD_KEYS})
    env.update(QUIET)
    empty = docs_dir() / EMPTY_ENV
    if not empty.is_file():
        empty.parent.mkdir(parents=True, exist_ok=True)
        empty.write_text("")
    env["VISUAL_PARSER_ENV_FILE"] = str(empty)
    if vision["route"] != "local":
        env[VP_KEY[vision["route"]]] = keys.value(vision["key_env"]) or ""      # the chat provider's key, under Visual-Parser's name
        if vision["route"] == "portkey":
            env.update(PORTKEY_OPENAI_PROVIDER_SLUG=vision["slug"], VISUAL_PARSER_FORCE_PORTKEY="true")
    env["PYTHONPATH"] = os.pathsep.join([str(automc_paths.SRC)] + ([os.environ["PYTHONPATH"]] if os.environ.get("PYTHONPATH") else []))
    return env


def command():
    """The visual-parser command: AUTOMC_VISUAL_PARSER, else the one installed next to this Python, else on PATH."""
    named = os.environ.get(COMMAND_ENV, "").strip()
    if named:
        return named
    beside = Path(sys.executable).parent / "visual-parser"
    if beside.is_file():
        return str(beside)
    found = shutil.which("visual-parser")
    if not found:
        raise Refused("visual-parser", "the visual-parser command is not installed (pip install visual-parser==2.2.4 in the automc "
                                       "environment, SETUP.md section 7f)", kind="MissingLibrary", exit_code=4)
    return found


# ----------------------------------------------------------------------------------------------------------------------
# files: what they hold, page by page
# ----------------------------------------------------------------------------------------------------------------------
def _sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _h(b):
    return hashlib.sha256(b).hexdigest()[:8]


def fingerprint(p):
    """What Visual-Parser reads, one entry per page, "text.drawing.pictures": for a PDF each page's text, its drawing (the content
    stream) and the pictures it shows; for an image its pixels. Annotations (highlights, notes), metadata and the file's layout are left
    out, so saving a highlight or re-saving the file changes nothing. None when the file cannot be read."""
    try:
        if Path(p).suffix.lower() in IMAGES:
            from PIL import Image
            with Image.open(p) as im:
                return [f"{_h(f'{im.mode}{im.size}'.encode() + im.tobytes())}.-.-"]
        import pymupdf
        with pymupdf.open(p) as d:
            if d.needs_pass:
                return None
            out = []
            for pg in d:
                pics = "".join(sorted(_h((d.extract_image(x[0]) or {}).get("image", b"")) for x in pg.get_images(full=True)))
                out.append(f"{_h(' '.join(pg.get_text().split()).encode())}.{_h(pg.read_contents())}.{_h(pics.encode())}")
            return out
    except Exception:
        return None


def compare(old, new):
    """{same, pages (text or pictures changed), drawing_only (only the drawing changed), pages_before, pages_after}."""
    if not old or not new:
        return {"same": False, "pages": [], "drawing_only": [], "unreadable": True}
    pages, drawing = [], []
    for n, (a, b) in enumerate(zip(old, new), start=1):
        if a == b:
            continue
        (at, ad, ap), (bt, bd, bp) = a.split("."), b.split(".")
        (pages if (at, ap) != (bt, bp) else drawing).append(n)
    if len(old) != len(new):
        pages += list(range(min(len(old), len(new)) + 1, max(len(old), len(new)) + 1))
    return {"same": not pages and not drawing, "pages": pages, "drawing_only": drawing, "pages_before": len(old), "pages_after": len(new)}


def _page_count(p):
    if Path(p).suffix.lower() in IMAGES:
        return 1
    try:
        import pymupdf
        with pymupdf.open(p) as d:
            return None if d.needs_pass else len(d)
    except Exception:
        return None


def discover(folder):
    """(PDFs and images under the folder, sorted; [(file, why) skipped]). Hidden folders are left out."""
    found, skipped = [], []
    for root, dirs, files in os.walk(folder):
        dirs[:] = sorted(d for d in dirs if not d.startswith("."))
        for f in sorted(files):
            p = Path(root) / f
            if p.suffix.lower() not in PDFS + IMAGES or f.startswith("."):
                continue
            try:
                size = p.stat().st_size
            except OSError:
                skipped.append((p.relative_to(folder).as_posix(), "cannot be read"))
                continue
            if size > MAX_BYTES:
                skipped.append((p.relative_to(folder).as_posix(), f"{size / 1e6:,.0f} MB, over {MAX_BYTES / 1e6:,.0f} MB"))
            else:
                found.append(p)
    return found, skipped


# ----------------------------------------------------------------------------------------------------------------------
# records
# ----------------------------------------------------------------------------------------------------------------------
def _resolved(rec):
    """A record as the code uses it: its source folder, stored as a reference (places.to_ref: ws:..., folder:<name>:...), as a path."""
    if not isinstance(rec, dict) or not rec.get("folder"):
        return rec
    try:
        return {**rec, "folder": str(places.from_ref(rec["folder"]))}
    except Refused:                                    # its declared folder was removed from the settings: keep the reference
        return rec


def record(name):
    return _resolved(jobs.read_json(kb_dir(name) / RECORD)) if name else None


def _write_record(name, **fields):
    if fields.get("folder"):
        fields["folder"] = places.to_ref(fields["folder"])      # survives moving the workspace or mounting it elsewhere
    rec = {**(jobs.read_json(kb_dir(name) / RECORD) or {}), **fields}
    jobs.atomic_write_json(kb_dir(name) / RECORD, rec)
    return _resolved(rec)


def kbs():
    """Every knowledge base's record, most recently updated first."""
    d = docs_dir()
    out = [r for r in (jobs.read_json(f / RECORD) for f in d.iterdir() if f.is_dir()) if isinstance(r, dict)] if d.is_dir() else []
    return sorted((_resolved(r) for r in out), key=lambda r: r.get("updated_at") or "", reverse=True)


def linked_kbs():
    """The knowledge bases of the linked workspaces, read only: [(LINK:KB, folder, record)]. Their records are not resolved: a ws:
    reference in them names the linked workspace, not this one."""
    import automc_settings
    out = []
    for lk in automc_settings.links():
        d = lk.path / DOCS
        for f in sorted(d.iterdir()) if d.is_dir() else ():
            rec = jobs.read_json(f / RECORD) if f.is_dir() else None
            if isinstance(rec, dict):
                out.append((f"{lk.name}:{f.name}", f, rec))
    return out


def _clean_name(s):
    return re.sub(r"[^A-Za-z0-9._-]+", "_", s or "").strip("._-")[:60] or "documents"


def _kb_for(folder, name):
    """(name, record) of the knowledge base for this folder: the named one, the one already made for the folder, else the folder's
    name. A name that already holds another folder is refused."""
    want = _clean_name(name) if name else None
    for r in kbs():
        if r.get("folder") == str(folder) and want in (None, r.get("kb")):
            return r["kb"], r
    nm = want or _clean_name(Path(folder).name)
    if nm in ("registry.json", "registry.lock"):
        nm = f"{nm}-kb"
    rec = record(nm)
    if rec and rec.get("folder") != str(folder):
        raise Refused("name", f"the knowledge base {nm!r} already holds {rec.get('folder')}; give another name", "NotAllowed", 3)
    return nm, rec or {}


def _registered(folder):
    """Names Visual-Parser lists as parsed, in the knowledge base's folder."""
    out = set()
    for f in REGISTRIES:
        try:
            out |= {l.strip() for l in (Path(folder) / f).read_text(encoding="utf-8", errors="replace").splitlines() if l.strip()}
        except OSError:
            pass
    return out


def _estimate(pages, text_mode, route):
    """Seconds, from earlier runs with the same text mode and the same kind of vision model (local or cloud)."""
    kind = "local" if route == "local" else "cloud"
    rates = [r["seconds"] / r["pages"] for rec in kbs() for r in rec.get("runs") or []
             if r.get("status") == "success" and r.get("seconds") and r.get("pages") and r.get("text_mode") == text_mode
             and ("local" if r.get("vision_route") == "local" else "cloud") == kind]
    if not rates or not pages:
        return None
    return {"seconds": round(statistics.median(rates) * pages, 1), "from": len(rates)}


def _duration(s):
    return f"{s:.0f} s" if s < 90 else (f"{s / 60:.0f} min" if s < 5400 else f"{s / 3600:.1f} h")


# ----------------------------------------------------------------------------------------------------------------------
# planning
# ----------------------------------------------------------------------------------------------------------------------
def _active():
    return [settle(j) for j in jobs.list_jobs(docs_dir(), active_only=True)] if docs_dir().is_dir() else []


def _waiting(name):
    """Rows of the knowledge base still to embed (none yet, or all when Ollama's embedding model changed), and the model's state."""
    if not (kb_dir(name) / kb_index.INDEX).is_file():
        return 0, None
    idx = kb_index.Index(kb_dir(name))
    try:
        try:
            model_id, problem = kb_index.make_embedder().model_id(), None
        except kb_index.EmbedderUnavailable as e:
            model_id, problem = None, str(e)
        return kb_index.waiting(idx, model_id), problem
    finally:
        idx.close()


def plan_parse(folder, name=None, text_mode=None, redo=None, provider=None, route=None):
    """What a run would do, for the user to approve. `provider` is the chat model (default: the conversation's, see chat_model());
    `route` is a plan's earlier vision choice, so that a plan made again is the same plan."""
    p = places.check_read(folder, "folder")
    if not p.is_dir():
        raise Refused("folder", f"{p.name} is a file; give the folder that holds it (a knowledge base is a folder: every PDF and image "
                                f"in it is parsed)", "InvalidArgument", 2)
    if text_mode and text_mode not in TEXT_MODES:
        raise Refused("text_mode", f"must be one of {list(TEXT_MODES)}, got {text_mode!r}", "InvalidArgument", 2)
    name, rec = _kb_for(p, name)
    if rec.get("text_mode") and text_mode and text_mode != rec["text_mode"]:
        raise Refused("text_mode", f"the knowledge base {name!r} was made with {rec['text_mode']}; a knowledge base keeps its text "
                                   f"mode (give another name for a {text_mode} one)", "NotAllowed", 3)
    text_mode = rec.get("text_mode") or text_mode or "nougat"
    found, skipped = discover(p)
    if not found and not rec:
        raise Refused("folder", f"{p} holds no PDF or image ({', '.join(PDFS + IMAGES)})", "InvalidArgument", 2)
    known = rec.get("files") or {}
    by_sha = {v.get("sha256"): k for k, v in known.items() if v.get("status") == "parsed"}
    redo = list(dict.fromkeys(redo or []))
    groups = {g: [] for g in ("new", "again", "changed", "resaved", "unchanged", "copies", "same_name")}
    seen, updates = {}, []
    for f in found:
        rel, base = f.relative_to(p).as_posix(), f.name
        if base in seen:
            groups["same_name"].append({"file": rel, "kept": seen[base]})
            continue
        seen[base] = rel
        sha = _sha256(f)
        entry = {"name": base, "relpath": rel, "sha256": sha, "kind": "image" if f.suffix.lower() in IMAGES else "pdf"}
        old = known.get(base)
        if old and old.get("status") == "parsed":
            if old.get("sha256") == sha:
                groups["unchanged"].append(base)
                if old.get("relpath") != rel:
                    updates.append({"name": base, "relpath": rel})
                if base in redo:
                    groups["again"].append({**entry, "redo": True, "why": "you asked to parse it again"})
                continue
            fp = fingerprint(f)
            diff = compare(old.get("fingerprint"), fp)
            if diff["same"]:
                groups["resaved"].append(base)
                updates.append({"name": base, "relpath": rel, "sha256": sha, "fingerprint": fp})
            elif base in redo:
                groups["again"].append({**entry, "redo": True, **diff, "why": "changed, and you asked to parse it again"})
            else:
                groups["changed"].append({**entry, **diff})
            continue
        if old:                                                  # failed or cancelled before: parsed again, as a new file
            groups["new"].append({**entry, "redo": True, "why": f"its last run ended {old.get('status')}"})
        elif sha in by_sha and by_sha[sha] != base:
            groups["copies"].append({"file": rel, "same_as": by_sha[sha]})
        else:
            groups["new"].append(entry)
    unknown = [n for n in redo if n not in seen or n not in known]
    if unknown:
        raise Refused("redo", f"not a parsed file of this folder: {', '.join(unknown)}", "InvalidArgument", 2)
    gone = sorted(k for k, v in known.items() if k not in seen and v.get("status") == "parsed")
    to_parse = groups["new"] + groups["again"]
    for f in to_parse:
        f["page_count"] = _page_count(p / f["relpath"])
    pages = sum(f["page_count"] or 1 for f in to_parse)
    waiting, embed_problem = _waiting(name)
    labels = [places.label(p)] + [places.label(f) for f in found]
    lab, origin = max(((l or "unknown", o) for l, o in labels), key=lambda x: places.ORDER.get(x[0], 2))
    vision = vision_for(lab, provider if provider is not None else _CHAT.get(), route)
    warnings = []
    active = _active()
    if active:
        warnings.append(f"another job is running (job {active[0]['job_id']}, {active[0]['kb']}); this one can start when it ends")
    if to_parse and vision["route"] == "local":
        loaded = _ollama_loaded()
        if loaded:
            warnings.append(f"Ollama has {', '.join(loaded)} loaded; the vision model shares the GPU with it and may be slow")
    if embed_problem and (to_parse or waiting):
        warnings.append(f"{embed_problem}: the rows will be searchable by full text only until it answers and the folder is ingested again")
    plan = {"kind": "parse", "folder": str(p), "kb": name, "kb_folder": f"{DOCS}/{name}", "new_kb": not rec, "to_parse": to_parse,
            "pages_to_parse": pages, "updates": updates, "waiting_rows": waiting, "unchanged": len(groups["unchanged"]),
            "resaved": groups["resaved"], "changed": groups["changed"], "copies": groups["copies"], "same_name": groups["same_name"],
            "gone": gone, "skipped": [{"file": f, "why": w} for f, w in skipped], "text_mode": text_mode, "label": lab,
            "label_reason": f"{lab}: {origin}", "vision": vision, "embedding": f"{kb_index.MODEL} on this machine (Ollama)",
            "estimate": _estimate(pages, text_mode, vision["route"]), "warnings": warnings,
            "nothing_to_do": not to_parse and not waiting,
            "facility_data": "public"}   # the plan goes to the user's approval, never to the model; what the run finds carries `label`
    key = {"folder": plan["folder"], "kb": name, "text_mode": text_mode, "label": lab, "waiting": bool(waiting),
           "to_parse": [(f["name"], f["sha256"], bool(f.get("redo"))) for f in to_parse],
           "vision": {k: vision[k] for k in ("route", "model", "slug")}}
    plan["plan_id"] = hashlib.sha256(json.dumps(key, sort_keys=True).encode()).hexdigest()[:16]
    plan["text"] = describe(plan)
    return plan


def _ollama_loaded():
    """The models Ollama has in memory now (its /api/ps), or [] when it does not answer."""
    import urllib.request
    try:
        with urllib.request.urlopen(f"{kb_index.ollama_base()}/api/ps", timeout=2) as r:
            return [m.get("name") for m in json.loads(r.read().decode()).get("models", [])]
    except Exception:
        return []


def _names(items, key=None, n=SHOWN):
    names = [i[key] if key else i for i in items]
    return ", ".join(names[:n]) + (f" and {len(names) - n} more" if len(names) > n else "")


def _kind_of(name):
    return "image" if Path(name).suffix.lower() in IMAGES else "pdf"


def _pages_list(pages):
    return ", ".join(map(str, pages))


ROWS_SHOWN = 30                                                   # file rows a plan's table shows


def describe(plan):
    """The plan as the user is asked about it: a banner, the files in a table, then how and where (console.py's style)."""
    import console
    first_run = lambda f: f.get("why", "").startswith("its last run")
    rows = []
    for f in plan["to_parse"]:
        again = f.get("redo") and not first_run(f)
        note = f["why"] if first_run(f) else ""
        if again:
            note = f["why"] + (f"; text or pictures differ on p. {_pages_list(f['pages'])}" if f.get("pages") else "")
        rows.append(["again" if again else "parse", f["kind"], f.get("page_count") or "?", f["relpath"], note])
    for f in plan["changed"]:
        what = (["its pages could not be compared"] if f.get("unreadable") else []) + \
               ([f"text or pictures differ on p. {_pages_list(f['pages'])}"] if f.get("pages") else []) + \
               ([f"only the drawing differs on p. {_pages_list(f['drawing_only'])}"] if f.get("drawing_only") else [])
        rows.append(["changed", f["kind"], "", f["relpath"], "; ".join(what)])
    rows += [["skipped", _kind_of(c["file"]), "", c["file"], f"the same content as {c['same_as']}"] for c in plan["copies"]]
    rows += [["skipped", _kind_of(s["file"]), "", s["file"], f"a second file named like {s['kept']}"] for s in plan["same_name"]]
    rows += [["skipped", _kind_of(s["file"]), "", s["file"], s["why"]] for s in plan["skipped"]]
    rows += [["gone", _kind_of(g), "", g, "no longer in the folder; its rows stay"] for g in plan["gone"]]
    if len(rows) > ROWS_SHOWN:
        rows = rows[:ROWS_SHOWN] + [["...", "", "", f"and {len(rows) - ROWS_SHOWN} more", ""]]

    new = [f for f in plan["to_parse"] if not f.get("redo") or first_run(f)]
    pdfs, imgs = sum(f["kind"] == "pdf" for f in new), sum(f["kind"] == "image" for f in new)
    what = " and ".join(x for x in (f"{pdfs} PDF(s)" if pdfs else "", f"{imgs} image(s)" if imgs else "") if x)
    parse_line = (f"{what} not parsed before" if new else "") + (", " if new and len(plan["to_parse"]) > len(new) else "") + \
                 (f"{len(plan['to_parse']) - len(new)} to parse again" if len(plan["to_parse"]) > len(new) else "")
    if plan["to_parse"]:
        parse_line += f"; {plan['pages_to_parse']} page(s) in all"
    kept = plan["unchanged"] + len(plan["resaved"])
    kept_line = (f"{kept} file(s) parsed before and unchanged" if kept else "") + \
                (f", {len(plan['resaved'])} of them re-saved with the same content (highlights, notes or metadata only)"
                 if plan["resaved"] else "")
    head = "Make the knowledge base" if plan["new_kb"] else "Update the knowledge base"
    out = [console.banner(f"{head} {plan['kb']!r} with Visual-Parser"),
           console.fields([("Folder", plan["folder"]), ("Parse", parse_line or "nothing new"), ("Kept", kept_line)])]
    if rows:
        out += ["", console.table(["Action", "Kind", "Pages", "File", "Note"], rows, right={2}, limits={3: 60})]
        notes = []
        if plan["changed"]:
            notes.append("changed: not parsed again unless you name it (--redo NAME; in the chat, ask to parse it again)")
        if plan["same_name"]:
            notes.append("a second file of the same name is skipped: Visual-Parser knows files by name")
        out += [console.wrap(n, indent=2) for n in notes]
    est = plan["estimate"]
    text = ("Nougat (reads equations and scanned pages; uses the GPU)" if plan["text_mode"] == "nougat" else
            "the PDF's own text layer (fast, no GPU; no equations, nothing from scanned pages)")
    embed = (f"the new rows{', and ' + str(plan['waiting_rows']) + ' rows waiting from before' if plan['waiting_rows'] else ''}"
             if plan["to_parse"] else f"{plan['waiting_rows']} rows waiting to be embedded")
    after = ("a cloud model may read what it finds" if plan["label"] == "public" else
             "only the local model may read what it finds; a cloud model will not see it")
    parsing = bool(plan["to_parse"])
    out += ["", console.fields(
        [("Text", text if parsing else None),
         ("Figures", "a vision model describes the figures and tables of each page, and every image" if parsing else None),
         ("Vision", f"{plan['vision']['where']}; {plan['vision']['why']}" if parsing else None),
         ("Embed", f"{embed}, with {plan['embedding']}"),
         ("Label", f"{plan['label_reason']}; {after}"),
         ("Into", f"workspace/{plan['kb_folder']}/ (the document folder is only read)"),
         ("Time", (f"about {_duration(est['seconds'])} (from {est['from']} earlier run(s))" if est else
                   "no estimate yet; the first runs measure it") if parsing else None)]
        + [("Warning", w) for w in plan["warnings"]])]
    return "\n".join(out)


# ----------------------------------------------------------------------------------------------------------------------
# starting
# ----------------------------------------------------------------------------------------------------------------------
def start(plan, approved_by):
    """
    Start what a plan describes. `approved_by` names the surface that asked the user ("cli", "chat"); there is no default, and no
    agent tool calls this. The plan is made again and must be identical to the one the user approved.
    """
    _need_posix()
    if not isinstance(approved_by, str) or not approved_by.strip():
        raise Refused("approved_by", "a run starts only with the user's approval, given through a surface", "NeedsApproval", 3)
    redo = [f["name"] for f in plan.get("to_parse") or [] if f.get("redo") and f["name"] in (record(plan["kb"]) or {}).get("files", {})]
    fresh = plan_parse(plan["folder"], plan["kb"], plan["text_mode"], redo, route=plan["vision"])
    if fresh["plan_id"] != plan.get("plan_id"):
        raise Refused("plan", "the folder, its label or the vision model changed since this plan was made; make a new plan and ask again",
                      "NotAllowed", 3)
    if fresh["nothing_to_do"]:
        raise Refused("folder", f"nothing to parse or embed in {plan['folder']}; the knowledge base {plan['kb']!r} is up to date",
                      "NotAllowed", 3)
    active = _active()
    if active:
        raise Refused("folder", f"a job is already running (job {active[0]['job_id']}, {active[0]['kb']}); wait for it or cancel it",
                      "NotAllowed", 3)
    name, v = fresh["kb"], fresh["vision"]
    folder = kb_dir(name)
    folder.mkdir(parents=True, exist_ok=True)
    stamp = jobs.now().replace(":", "")
    if (folder / LOG).is_file():                                  # the last run's log, kept
        (folder / "runs").mkdir(exist_ok=True)
        (folder / LOG).rename(folder / "runs" / f"{stamp}-{LOG}")
    args, stage = None, folder / STAGING / stamp
    if fresh["to_parse"]:
        stage.mkdir(parents=True)
        for f in fresh["to_parse"]:                               # links, so the document folder is only read
            (stage / f["name"]).symlink_to(Path(fresh["folder"]) / f["relpath"])
        args = [command(), "--input-dir", str(stage), "--output-dir", str(folder), "--vision-provider", v["vision_provider"],
                "--max-workers", "1", "--metadata-pages", str(METADATA_PAGES), "--log-level", "INFO", "--text-mode", fresh["text_mode"]]
        args += ["--vision-model", v["model"]] if v["model"] else []
        for f in fresh["to_parse"]:
            if f.get("redo"):
                args += ["--redo", f["name"]]
    pipeline_log = folder / "05_pipeline.log"
    jobs.atomic_write_json(folder / REQUEST, {"args": args, "stage": str(stage), "folder": fresh["folder"], "to_parse": fresh["to_parse"],
                                              "updates": fresh["updates"], "pages": fresh["pages_to_parse"],
                                              "pipeline_log_offset": pipeline_log.stat().st_size if pipeline_log.is_file() else 0})
    rec = record(name) or {}
    _write_record(name, kb=name, folder=fresh["folder"], label=fresh["label"], label_reason=fresh["label_reason"],
                  text_mode=fresh["text_mode"], status="ingesting", created_at=rec.get("created_at") or jobs.now(), updated_at=jobs.now(),
                  vision={k: v[k] for k in ("route", "vision_provider", "model", "where")}, visual_parser=_version(),
                  files=rec.get("files") or {}, runs=rec.get("runs") or [], error=None)
    python = os.environ.get("AUTOMC_PYTHON") or sys.executable
    with open(folder / LOG, "ab") as log:
        proc = subprocess.Popen([python, "-m", CHILD_MODULE, "--folder", str(folder)], cwd=str(folder), env=child_env(v), stdout=log,
                                stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)
    jobs.CHILDREN[proc.pid] = proc
    if v.get("route") != "local":                    # page images go to the chat model's cloud service: recorded in egress.jsonl
        from tools import conversation
        from tools.chats import store as chat_store
        chat_store.egress(f"vision: {v.get('where')}", fresh["label"], chat=(conversation.current() or {}).get("thread_id"),
                          what=f"page images of {len(fresh['to_parse'])} file(s) for the knowledge base {name}")
    job = jobs.create_job(docs_dir(), kind="parse", status="running", kb=name, case=name, path=fresh["folder"], pid=proc.pid,
                          pid_started_at=jobs.process_started_at(proc.pid), segment=1, approved_by=approved_by, approved_at=jobs.now(),
                          plan_id=fresh["plan_id"], facility_data=fresh["label"], vision={k: v[k] for k in ("route", "model")},
                          files=len(fresh["to_parse"]), started_at=jobs.now())
    _write_record(name, job_id=job["job_id"])
    return summarize(job)


def _version():
    try:
        from importlib.metadata import version
        return version("visual-parser")
    except Exception:
        return None


# ----------------------------------------------------------------------------------------------------------------------
# checking
# ----------------------------------------------------------------------------------------------------------------------
def settle(job):
    """Bring an active job's record up to date from its files and its process."""
    jobs.reap_children()
    if job["status"] not in jobs.ACTIVE or not job.get("pid"):
        return job
    folder = kb_dir(job["kb"])
    sent = jobs.read_sentinel(folder / LOG)
    if sent is not None:
        rec = record(job["kb"]) or {}
        fields = {"status": sent[1], "returncode": sent[0], "error": rec.get("error")}
    elif jobs.process_alive(job["pid"], job.get("pid_started_at")):
        return job
    elif job.get("cancel_requested"):
        fields = {"status": "cancelled"}
        _write_record(job["kb"], status="cancelled", updated_at=jobs.now())
    else:
        fields = {"status": "interrupted", "error": "the job's process ended without finishing (no AUTOMC_JOB_DONE line); the machine may "
                                                   "have restarted or the process was killed"}
        _write_record(job["kb"], status="interrupted", updated_at=jobs.now(), error=fields["error"])
    return jobs.update_job(docs_dir(), job["job_id"], **fields) or job


def summarize(job):
    out = {k: job.get(k) for k in ("job_id", "kind", "status", "kb", "path", "files", "approved_by", "vision", "facility_data",
                                    "started_at", "completed_at", "error")}
    if job["status"] in jobs.ACTIVE:
        out["seconds_so_far"] = round(time.time() - _epoch(job.get("started_at")), 0) if job.get("started_at") else None
        out["progress"] = _progress(kb_dir(job["kb"]))
        out["next"] = "check again later with DocumentCheckTool; a run takes minutes. DocumentCancelTool stops it"
    elif job["status"] == "success":
        out["next"] = f"search it with DocumentSearchTool (kb {job['kb']})"
    return out


def _epoch(iso):
    import datetime as _dt
    return _dt.datetime.fromisoformat(iso).timestamp()


def _progress(folder):
    """What the run has done so far: files parsed, rows embedded, the last line of its log."""
    out = {}
    req = jobs.read_json(folder / REQUEST) or {}
    names = {f["name"] for f in req.get("to_parse") or []}
    if names:
        out["parsed"] = f"{len(names & _registered(folder))} of {len(names)} files"
    emb = (record(folder.name) or {}).get("embedding_progress")
    if emb:
        out["embedded"] = f"{emb['done']} of {emb['total']} rows"
    try:
        lines = [l.strip() for l in (folder / LOG).read_text(encoding="utf-8", errors="replace").splitlines()[-30:] if l.strip()]
        out["last_log_line"] = re.sub(r"\s+", " ", lines[-1])[:200] if lines else None
    except OSError:
        pass
    return out


def summary(name):
    """A knowledge base: its folder, label, files, what the index holds, its documents, its last run."""
    rec = record(name)
    if not rec:
        raise Refused("kb", f"no knowledge base {name!r}; DocumentCheckTool with no arguments lists them", "NotFound", 2)
    out = {k: rec.get(k) for k in ("kb", "folder", "status", "text_mode", "vision", "label_reason", "created_at", "updated_at", "error",
                                    "job_id")}
    files = rec.get("files") or {}
    out["files"] = [{"name": k, "relpath": v.get("relpath"), "status": v.get("status"), "pages": v.get("pages"),
                     "parsed_at": v.get("parsed_at")} for k, v in sorted(files.items())]
    if (kb_dir(name) / kb_index.INDEX).is_file():
        idx = kb_index.Index(kb_dir(name))
        try:
            out["index"] = idx.stats()
            out["documents"] = [{k: d[k] for k in ("source", "kind", "title", "report_number")} | (
                {"authors": json.loads(d["authors"])} if d["authors"] else {}) for d in
                idx.conn.execute("SELECT * FROM documents ORDER BY source LIMIT 100")]
        finally:
            idx.close()
    runs = rec.get("runs") or []
    if runs:
        out["last_run"] = runs[-1]
    out["facility_data"] = rec.get("label") or "unknown"
    if rec.get("status") == "ready":
        out["next"] = f"search it with DocumentSearchTool (kb {name}); DocumentParseTool on {rec.get('folder')} adds files put there later"
    return out


def check(job_id=None, kb=None):
    if job_id:
        job = jobs.get_job(docs_dir(), job_id)
        if job is None:
            raise Refused("job_id", f"no job {job_id!r}", "NotFound", 2)
        out = summarize(settle(job))
        if out["status"] == "success":
            out["kb_summary"] = summary(job["kb"])
        return out
    if kb:
        rec = record(kb)
        if rec and rec.get("job_id") and rec.get("status") == "ingesting":
            job = jobs.get_job(docs_dir(), rec["job_id"])
            if job:
                settle(job)
        return summary(kb)
    active = [summarize(j) for j in _active()]
    listed = [{"kb": r.get("kb"), "folder": r.get("folder"), "status": r.get("status"), "files": len(r.get("files") or {}),
               "label": r.get("label"), "updated_at": r.get("updated_at")} for r in kbs()]
    labels = [r.get("label") or "unknown" for r in kbs()] + [j.get("facility_data") or "unknown" for j in active]
    return {"knowledge_bases": listed, "active": active, "facility_data": places.worst(labels) if labels else "public",
            "next": "DocumentCheckTool with kb for one knowledge base; DocumentSearchTool to search them"}


def cancel(job_id):
    """Kill a job at once. What it wrote stays; the next run of the folder parses what this one did not finish."""
    _need_posix()
    job = jobs.get_job(docs_dir(), job_id) if docs_dir().is_dir() else None
    if job is None:
        raise Refused("job_id", f"no job {job_id!r}", "NotFound", 2)
    job = settle(job)
    if job["status"] not in jobs.ACTIVE:
        raise Refused("job_id", f"job {job_id} is {job['status']}; nothing to cancel", "NotAllowed", 3)
    jobs.update_job(docs_dir(), job_id, cancel_requested=True)
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(job["pid"], sig)
        except (ProcessLookupError, PermissionError):
            break
        t = time.time()
        while time.time() - t < 5.0 and jobs.process_alive(job["pid"], job.get("pid_started_at")):
            time.sleep(0.1)
        if not jobs.process_alive(job["pid"], job.get("pid_started_at")):
            break
    shutil.rmtree(kb_dir(job["kb"]) / STAGING, ignore_errors=True)   # links only
    return summarize(settle(jobs.get_job(docs_dir(), job_id)))
