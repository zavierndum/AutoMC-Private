"""
The file tools (devops/AutoMC_General_Tools_Plan.md, GT-2): list folders, read files, write notes. Same shape as the other tools: a
dict of arguments in, a JSON-serializable dict out, {"ok": false, "error": {...}} instead of raising. Where they may read and write,
what they refuse and the label of each place: tools/files/places.py.

FileReadTool reads text (any encoding), CSV and Excel (pandas; openpyxl for .xlsx), the text layer of a PDF (pypdf; no models, so no
figures or equations: those come with the Visual-Parser tools, GT-3) and the structure of an HDF5 file (h5py). Every read is paged and
gives at most about 20,000 characters, so a 32k model is not flooded; `next` says how to read on.
"""
from __future__ import annotations

import fnmatch
import functools
import importlib.util
import json
import os
import time
from pathlib import Path

import automc_paths

from . import places
from .places import Refused

MAX_CHARS = 20_000
MAX_LINES = 400
MAX_ROWS = 100
MAX_ENTRIES = 200
MAX_WRITE = 1_000_000
MAX_BYTES = 50_000_000                  # a text file is read up to this size; a larger table is read as text
TABLES = {".csv": ",", ".tsv": "\t"}
EXCEL = (".xlsx", ".xlsm")
HDF5 = (".h5", ".hdf5")
IMAGES = (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tif", ".tiff", ".webp")


def as_tool(schema):
    def wrap(fn):
        @functools.wraps(fn)
        def handler(args=None):
            args = args if args is not None else {}
            try:
                if not isinstance(args, dict):
                    raise Refused("arguments", f"must be an object, got {type(args).__name__}", "InvalidArgument", 2)
                unknown = sorted(set(args) - set(schema["properties"]))
                if unknown:
                    raise Refused(unknown[0], f"unknown argument; allowed: {sorted(schema['properties'])}", "InvalidArgument", 2)
                missing = [k for k in schema["required"] if k not in args]
                if missing:
                    raise Refused(missing[0], "required argument", "InvalidArgument", 2)
                return {"ok": True, **fn(args)}
            except Refused as e:
                return {"ok": False, "error": e.payload()}
            except Exception as e:                               # a bug or an unreadable file: report it as data, never crash
                return {"ok": False, "error": {"type": "Error", "field": None, "message": f"{type(e).__name__}: {str(e)[:300]}",
                                               "exit_code": 1}}
        return handler
    return wrap


def _defuse(obj):
    """A key named facility_data that comes from a file's content (a column, an attribute, a JSON field shown as data) is renamed:
    the data guard reads that key as the result's label, and a file must not label itself."""
    if isinstance(obj, dict):
        return {(f"{k} (from the file)" if k == "facility_data" else k): _defuse(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_defuse(v) for v in obj]
    return obj


def _labelled(out, labels_and_origins):
    """Put the label of what a result shows into it: facility_data, the only key of that name in the result (none when no place is
    known to be public or restricted), and, for the reader, where it comes from."""
    out = _defuse(out)
    lab = places.worst([lab for lab, _ in labels_and_origins])
    origins = sorted({o for lab_, o in labels_and_origins if lab_ == lab})
    if lab is not None:
        out["facility_data"] = lab
    out["label_reason"] = f"{lab or 'no label (not known to be public)'}: {'; '.join(origins[:3])}"
    return out


def _int(args, key, default, low, high):
    v = args.get(key, default)
    if isinstance(v, bool) or not isinstance(v, int):
        raise Refused(key, f"must be a whole number, got {v!r}", "InvalidArgument", 2)
    if not low <= v <= high:
        raise Refused(key, f"must be between {low} and {high}, got {v}", "InvalidArgument", 2)
    return v


def _shown(p):
    """The path as the user would write it: relative to the workspace when it is inside, else absolute."""
    ws = Path(automc_paths.WORKSPACE).resolve()
    return p.relative_to(ws).as_posix() if ws in p.parents else str(p)


# ---- FileListTool ----------------------------------------------------------------------------------------------------------------
LIST_SCHEMA = {"type": "object", "additionalProperties": False, "required": [], "properties": {
    "path": {"type": "string", "description": "the folder: relative to the workspace (default: the workspace itself), or absolute"},
    "pattern": {"type": "string", "description": "only names matching this pattern, for example *.csv (default: everything)"},
    "recursive": {"type": "boolean", "description": "also list the subfolders' contents (default false)"},
    "max_entries": {"type": "integer", "description": f"at most this many entries (default {MAX_ENTRIES}, at most 1000)"},
}}


def _walk(folder, recursive):
    """The folder's entries in name order, a level at a time and lazily, so a large tree stops at the limit; .git, __pycache__ and
    the like are not entered."""
    level = [folder]
    while level:
        below = []
        for d in level:
            try:
                kids = sorted(d.iterdir(), key=lambda x: x.name.lower())
            except OSError:
                continue
            for k in kids:
                if k.name in places.SKIP_DIRS:
                    continue
                yield k
                if recursive and k.is_dir() and not k.is_symlink():
                    below.append(k)
        level = below


@as_tool(LIST_SCHEMA)
def file_list(args):
    folder = places.check_read(args.get("path") or ".")
    if not folder.is_dir():
        raise Refused("path", f"{folder} is a file; read it with FileReadTool", "InvalidArgument", 2)
    pattern, recursive = args.get("pattern") or "*", bool(args.get("recursive", False))
    limit = _int(args, "max_entries", MAX_ENTRIES, 1, 1000)
    entries, skipped, labels, more = [], 0, [places.label(folder)], False
    for p in _walk(folder, recursive):
        if places.secret_reason(p.resolve()):
            skipped += 1
            continue
        if not fnmatch.fnmatch(p.name, pattern):
            continue
        if len(entries) >= limit:
            more = True
            break
        try:
            st = p.stat()
        except OSError:
            continue
        entries.append({"name": p.relative_to(folder).as_posix(), "kind": "folder" if p.is_dir() else "file",
                        **({} if p.is_dir() else {"bytes": st.st_size}),
                        "modified": time.strftime("%Y-%m-%d %H:%M", time.localtime(st.st_mtime))})
        labels.append(places.label(p.resolve()))
    out = {"folder": _shown(folder), "entries": entries, "count": len(entries), "more": more,
           "skipped": skipped and f"{skipped} entries left out: keys, credentials, and folders such as .git or __pycache__"}
    if more:
        out["next"] = "more entries exist: narrow the pattern, list a subfolder, or raise max_entries"
    return _labelled(out, labels)


# ---- FileReadTool ----------------------------------------------------------------------------------------------------------------
READ_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["path"], "properties": {
    "path": {"type": "string", "description": "the file: relative to the workspace, or absolute (a Windows path C:\\... works too)"},
    "start": {"type": "integer", "description": "the first line (text) or row (tables) to show, from 1 (default 1)"},
    "max_lines": {"type": "integer", "description": f"how many lines or rows to show (default {MAX_LINES} lines, {MAX_ROWS} rows)"},
    "sheet": {"type": "string", "description": "Excel only: the sheet's name or its number from 1 (default the first)"},
    "pages": {"type": "string", "description": "PDF only: the pages, for example 3 or 2-5 (default from page 1 until the limit)"},
}}


def _decode(raw):
    if b"\x00" in raw[:8192]:
        return None, None
    try:
        return raw.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        pass
    try:
        from charset_normalizer import from_bytes
        best = from_bytes(raw).best()
        if best is not None:
            return str(best), best.encoding
    except ImportError:
        pass
    return raw.decode("latin-1"), "latin-1"


def _text(p, args):
    with open(p, "rb") as fh:
        raw = fh.read(MAX_BYTES)
    text, enc = _decode(raw)
    if text is None:
        raise Refused("path", f"{p.name} is a binary file; FileReadTool reads text, CSV, Excel, PDF text and HDF5 structure",
                      "Unsupported", 2)
    lines = text.splitlines()
    start = _int(args, "start", 1, 1, max(1, len(lines)))
    n = _int(args, "max_lines", MAX_LINES, 1, 5000)
    shown, chars = [], 0
    for i, line in enumerate(lines[start - 1:start - 1 + n], start):
        if chars + len(line) > MAX_CHARS and shown:
            break
        shown.append(line[:MAX_CHARS])
        chars += len(line) + 1
    end = start + len(shown) - 1
    out = {"format": "text", "encoding": enc, "total_lines": len(lines), "lines": f"{start}-{end}", "text": "\n".join(shown)}
    if p.stat().st_size > MAX_BYTES:
        out["note"] = f"only the first {MAX_BYTES // 1_000_000} MB of this {p.stat().st_size / 1e6:.0f} MB file are read"
    if end < len(lines):
        out["next"] = f"lines {end + 1} to {len(lines)} remain: call again with start={end + 1}"
    return out


def _num(x):
    x = float(x)
    return round(x, 6) if x == x and abs(x) != float("inf") else None


def _frame(df, args, what):
    rows = len(df)
    start = _int(args, "start", 1, 1, max(1, rows))
    n = _int(args, "max_lines", MAX_ROWS, 1, 2000)
    part = df.iloc[start - 1:start - 1 + n]
    text = part.to_csv(index=False)
    while len(text) > MAX_CHARS and len(part) > 1:
        part = part.iloc[:max(1, len(part) // 2)]
        text = part.to_csv(index=False)
    end = start + len(part) - 1
    out = {"format": what, "rows": rows, "columns": [[str(c), str(t)] for c, t in df.dtypes.items()],
           "shown_rows": f"{start}-{end}", "text": text[:MAX_CHARS]}
    numeric = df.select_dtypes("number")
    if not numeric.empty:
        d = numeric.describe()
        out["numeric_columns"] = [[str(c), _num(d[c]["mean"]), _num(d[c]["min"]), _num(d[c]["max"])] for c in numeric.columns[:50]]
        out["numeric_columns_are"] = ["column", "mean", "min", "max"]
    if end < rows:
        out["next"] = f"rows {end + 1} to {rows} remain: call again with start={end + 1}"
    return out


def _table(p, args):
    import pandas as pd
    return _frame(pd.read_csv(p, sep=TABLES[p.suffix.lower()], encoding_errors="replace"), args, p.suffix.lower()[1:])


def _excel(p, args):
    if importlib.util.find_spec("openpyxl") is None:
        raise Refused("path", "reading Excel needs the Python package openpyxl (pip install openpyxl==3.1.5 in the automc "
                              "environment)", "MissingLibrary", 4)
    import pandas as pd
    with pd.ExcelFile(p, engine="openpyxl") as book:
        names = book.sheet_names
        want = (args.get("sheet") or "").strip()
        if not want:
            sheet = names[0]
        elif want in names:
            sheet = want
        elif want.isdigit() and 1 <= int(want) <= len(names):
            sheet = names[int(want) - 1]
        else:
            raise Refused("sheet", f"no sheet {want!r}; sheets: {names}", "NotFound", 2)
        frame = book.parse(sheet)
    return {**_frame(frame, args, "excel"), "sheet": sheet, "sheets": names}


def _pages(spec, count):
    if not spec:
        return list(range(1, count + 1))
    try:
        a, _, b = str(spec).partition("-")
        lo, hi = int(a), int(b or a)
    except ValueError:
        raise Refused("pages", f"must be a page or a range such as 2-5, got {spec!r}", "InvalidArgument", 2) from None
    if not 1 <= lo <= hi <= count:
        raise Refused("pages", f"the PDF has pages 1 to {count}, asked {spec!r}", "InvalidArgument", 2)
    return list(range(lo, hi + 1))


def _pdf(p, args):
    if importlib.util.find_spec("pypdf") is None:
        raise Refused("path", "reading a PDF needs the Python package pypdf (pip install pypdf==5.5.0 in the automc environment)",
                      "MissingLibrary", 4)
    from pypdf import PdfReader
    reader = PdfReader(str(p))
    count = len(reader.pages)
    shown, chars = [], 0
    for n in _pages(args.get("pages"), count):
        text = (reader.pages[n - 1].extract_text() or "").strip()
        if chars + len(text) > MAX_CHARS and shown:
            break
        shown.append({"page": n, "text": text[:MAX_CHARS]})
        chars += len(text)
    out = {"format": "pdf", "pages": count, "shown_pages": f"{shown[0]['page']}-{shown[-1]['page']}" if shown else "none",
           "text": "\n\n".join(f"[page {s['page']}]\n{s['text']}" for s in shown),
           "note": "the PDF's text layer only: figures, tables drawn as images, scanned pages and equations need the document "
                   "parser (Visual-Parser)"}
    if shown and shown[-1]["page"] < count:
        out["next"] = f"pages {shown[-1]['page'] + 1} to {count} remain: call again with pages={shown[-1]['page'] + 1}-{count}"
    if shown and not any(s["text"] for s in shown):
        out["note"] = "these pages have no text layer (a scan or images); the document parser (Visual-Parser) can read them"
    return out


def _hdf5(p, args):
    import h5py
    items = []

    def plain(v):
        v = v.tolist() if hasattr(v, "tolist") else v
        if isinstance(v, bytes):
            v = v.decode("utf-8", "replace")
        if isinstance(v, list):
            return [plain(x) for x in v[:20]]
        if isinstance(v, str):
            return v[:200]
        return v if isinstance(v, (int, float, bool)) or v is None else str(v)[:200]

    def visit(name, obj):
        row = {"name": name, "kind": "group" if isinstance(obj, h5py.Group) else "dataset"}
        if row["kind"] == "dataset":
            row.update(shape=list(obj.shape), dtype=str(obj.dtype))
            if obj.size and obj.size <= 8 and obj.dtype.kind in "iufb":
                row["value"] = plain(obj[()])
        if obj.attrs:
            row["attrs"] = [[str(k), plain(v)] for k, v in list(obj.attrs.items())[:10]]
        items.append(row)

    with h5py.File(p, "r") as f:
        f.visititems(visit)
    start = _int(args, "start", 1, 1, max(1, len(items)))
    n = _int(args, "max_lines", MAX_LINES, 1, 5000)
    part = items[start - 1:start - 1 + n]
    while len(json.dumps(part, default=str)) > MAX_CHARS and len(part) > 1:
        part = part[:len(part) // 2]
    end = start + len(part) - 1
    out = {"format": "hdf5", "objects": len(items), "shown": f"{start}-{end}", "structure": part,
           "note": "the structure only (names, shapes, types, small values); for an OpenMC statepoint, RunResultReaderTool reads "
                   "the results"}
    if end < len(items):
        out["next"] = f"objects {end + 1} to {len(items)} remain: call again with start={end + 1}"
    return out


def _image(p, args):
    out = {"format": "image", "note": "an image: its content is described by the document parser (Visual-Parser), not read here"}
    try:
        from PIL import Image
        with Image.open(p) as im:
            out.update(width=im.width, height=im.height, mode=im.mode)
    except Exception:
        pass
    return out


@as_tool(READ_SCHEMA)
def file_read(args):
    p = places.check_read(args["path"])
    if p.is_dir():
        raise Refused("path", f"{p} is a folder; list it with FileListTool", "InvalidArgument", 2)
    suffix = p.suffix.lower()
    if suffix in TABLES and p.stat().st_size <= MAX_BYTES:
        body = _table(p, args)
    elif suffix in EXCEL:
        body = _excel(p, args)
    elif suffix == ".xls":
        raise Refused("path", "old .xls workbooks are not read; save it as .xlsx or .csv", "Unsupported", 2)
    elif suffix == ".pdf":
        body = _pdf(p, args)
    elif suffix in HDF5:
        body = _hdf5(p, args)
    elif suffix in IMAGES:
        body = _image(p, args)
    else:
        body = _text(p, args)
    out = {"path": _shown(p), "bytes": p.stat().st_size, **body}
    return _labelled(out, [places.label(p)])


# ---- FileWriteTool ---------------------------------------------------------------------------------------------------------------
WRITE_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["path", "text"], "properties": {
    "path": {"type": "string", "description": "the file, relative to the workspace, for example notes/summary.md (never inside a case "
                                              "or a run folder)"},
    "text": {"type": "string", "description": "the whole text to write"},
    "overwrite": {"type": "boolean", "description": "replace the file if it exists (default false: an existing file is refused)"},
}}


@as_tool(WRITE_SCHEMA)
def file_write(args):
    p = places.check_write(args["path"])
    text = args["text"]
    if not isinstance(text, str):
        raise Refused("text", "must be text", "InvalidArgument", 2)
    data = text.encode("utf-8")
    if len(data) > MAX_WRITE:
        raise Refused("text", f"is {len(data):,} bytes; at most {MAX_WRITE:,} are written at once", "InvalidArgument", 2)
    existed = p.exists()
    if existed and not args.get("overwrite"):
        raise Refused("path", f"{_shown(p)} exists; write to a new name, or set overwrite (the user should agree)", "Exists", 2)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.{os.getpid()}.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, p)
    return {"path": _shown(p), "bytes": len(data), "replaced": existed,
            "note": "written into the workspace; nothing outside it was touched"}


TOOLS = [
    {"name": "FileListTool", "handler": file_list, "parameters": LIST_SCHEMA,
     "description": "List a folder: names, sizes, dates. Reads the workspace, the AutoMC folder, the facility packs and the folders the "
                    "user declared (.automc/settings.yaml) and the linked workspaces; keys, credentials and .env files are never listed. Each "
                    "result carries the "
                    "label of the place it shows."},
    {"name": "FileReadTool", "handler": file_read, "parameters": READ_SCHEMA,
     "description": "Read a file, a page at a time: text in any encoding, CSV and Excel (columns, types, rows), the text layer of a "
                    "PDF, the structure of an HDF5 file. At most about 20,000 characters per call; `next` says how to read on. Never "
                    "reads keys, credentials or .env files. For a finished OpenMC run, RunResultReaderTool is the better tool."},
    {"name": "FileWriteTool", "handler": file_write, "parameters": WRITE_SCHEMA,
     "description": "Write a text file into the workspace (a note, a table, a script for the user to look at). Never outside the "
                    "workspace, never inside a case or a run folder, and never over an existing file unless overwrite is set, which "
                    "needs the user's agreement."},
]
