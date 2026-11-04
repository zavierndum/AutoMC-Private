"""
A knowledge base's index (devops/AutoMC_General_Tools_Plan.md, GT-3R): the rows Visual-Parser wrote, embedded and searchable, in one
SQLite file beside them, index.sqlite. The design of Axiom's RAG without Axiom (axiom/rag/sqlite_store.py, and the row layout of
vp_extension_scripts/RAG_MERGE_DESIGN.md section 3.2), with the settings the retrieval experiment chose
(devops/validation/GT3R_RETRIEVAL.md, arm 3+v):

  documents   one row per PDF or image: title, authors, report number, Visual-Parser's metadata
  chunks      one row per text chunk or visual (kind text or visual: a figure, a table, a standalone image), with its page, its
              locator (Visual-Parser's chunk_id, figure_id or image_id), its text, and the embedding model that made its vectors
  pieces      one per vector: a text chunk whole; a visual's description in pieces of about 1,500 characters, by Visual-Parser's own
              recursive splitter, so nothing is cut. Each piece is embedded after nomic's document prefix and a short header (title,
              report number, page, figure), as vpx does
  chunks_fts  full text (FTS5, porter), for search without embeddings
  chunks_vec  the vectors (sqlite-vec: 768 four-byte numbers, cosine), with each piece's kind

The embedding model is fixed per knowledge base and recorded; when Ollama's model changes, the next ingest embeds every row again.
Search: the question embedded by the same model, the nearest pieces, each row once (its best piece), visuals first when the question
names one (RADIANT-LLM's rule, plurals added). Without an embedding model, or without sqlite-vec, search is full text only, as Axiom's
is when no embedding model answers.
"""
from __future__ import annotations

import hashlib
import importlib
import json
import os
import re
import sqlite3
import urllib.error
import urllib.request
from pathlib import Path

from tools.files.places import Refused

INDEX = "index.sqlite"
KB_FILES = {"text": "01_chunks_kb.jsonl", "visual": "02_visuals_kb.jsonl", "metadata": "03_metadata_kb.jsonl",
            "image": "image_descriptions_kb.jsonl"}
MODEL = "nomic-embed-text"                       # Axiom's default (axiom/rag/embeddings.py) and vpx's
DIMS = 768
DOC_PREFIX, QUERY_PREFIX = "search_document: ", "search_query: "
PIECE_CHARS = 1500
# Ollama's nomic-embed-text refuses more than 2,048 tokens. A token covers at least one character, so prefix (17) + header (at most
# 300) + newline + piece (at most 1,500) stays under it.
HEADER_CHARS = 300
BATCH = 32
EMBEDDER_ENV = "AUTOMC_EMBEDDER"                 # "module:Class" of another embedder (the tests' fake); default Ollama's nomic
TEXT_BUDGET = 24_000                             # characters a search returns; past it a visual gives its matching piece
VP_SEPARATORS = ["\n\n", "\n", " ", ""]          # Visual-Parser's splitter (visual_parser/text_extractor.py:46-55)
KINDS = ("text", "visual")
# RADIANT-LLM's visual-question words (utils/pdf_helpers.py:441-444), with the plurals and the words it misses
VISUAL_TERMS = {"figure", "figures", "fig", "figs", "visual", "visuals", "diagram", "diagrams", "schematic", "schematics", "flowchart",
                "flowcharts", "caption", "captions", "oval", "arrow", "arrows", "table", "tables", "plot", "plots", "graph", "graphs",
                "image", "images", "chart", "charts", "curve", "curves", "axis", "axes", "legend", "drawing", "drawings", "photo",
                "photos", "photograph", "map", "maps", "layout"}


class EmbedderUnavailable(Exception):
    """No embedding model answers; the rows wait, and search is full text until they are embedded."""


# ----------------------------------------------------------------------------------------------------------------------
# the embedding model
# ----------------------------------------------------------------------------------------------------------------------
def ollama_base():
    base = os.environ.get("OLLAMA_HOST", "127.0.0.1:11434")
    return base if base.startswith("http") else f"http://{base}"


class OllamaEmbedder:
    """Ollama's /api/embed as vpx calls it, with truncate false: a text too long is an error, never cut silently."""

    def __init__(self, model=MODEL):
        self.model, self.base = model, ollama_base()

    def _get(self, path, body=None, timeout=10):
        req = urllib.request.Request(f"{self.base}{path}", data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)

    def model_id(self):
        """ollama:<model>@<digest>: vectors from another build of the model are not comparable."""
        try:
            tags = self._get("/api/tags").get("models", [])
        except (OSError, ValueError) as e:
            raise EmbedderUnavailable(f"Ollama does not answer at {self.base} ({type(e).__name__})") from None
        for m in tags:
            if m.get("name") in (self.model, f"{self.model}:latest"):
                return f"ollama:{self.model}@{(m.get('digest') or '')[:12]}"
        raise EmbedderUnavailable(f"Ollama has no {self.model} (ollama pull {self.model})")

    def embed(self, texts):
        out = []
        for i in range(0, len(texts), BATCH):
            try:
                out += self._get("/api/embed", {"model": self.model, "input": texts[i:i + BATCH], "truncate": False},
                                 timeout=600)["embeddings"]
            except urllib.error.HTTPError as e:
                raise EmbedderUnavailable(f"Ollama refused the embedding request: HTTP {e.code} {e.read()[:200]!r}") from None
            except (OSError, ValueError, KeyError) as e:
                raise EmbedderUnavailable(f"the embedding request to Ollama failed ({type(e).__name__}: {e})") from None
        return out


def make_embedder():
    named = os.environ.get(EMBEDDER_ENV, "").strip()
    if named:
        module, _, attr = named.partition(":")
        return getattr(importlib.import_module(module), attr)()
    return OllamaEmbedder()


# ----------------------------------------------------------------------------------------------------------------------
# what is embedded
# ----------------------------------------------------------------------------------------------------------------------
def header(title, source, report_number=None, page=None, figure_index=None, image=False):
    """vpx's header (vpx/embeddings.py:73-81): title (or file), report number, page, figure; an image: title and file."""
    if image:
        h = f"{title or source} | image {source}"
    else:
        bits = [title or source] + ([report_number] if report_number else []) + ([f"p. {page}"] if page is not None else [])
        bits += [f"figure {int(figure_index) + 1}"] if figure_index not in (None, "") else []
        h = " | ".join(str(b) for b in bits)
    return h if len(h) <= HEADER_CHARS else h[:HEADER_CHARS - 3] + "..."


_SPLITTER = []


def pieces(text):
    """A row's pieces: itself when it is at most PIECE_CHARS long, else Visual-Parser's recursive splitter at PIECE_CHARS."""
    if len(text) <= PIECE_CHARS:
        return [text]
    if not _SPLITTER:
        try:
            from langchain_text_splitters import RecursiveCharacterTextSplitter
            _SPLITTER.append(RecursiveCharacterTextSplitter(chunk_size=PIECE_CHARS, chunk_overlap=0, separators=VP_SEPARATORS).split_text)
        except ImportError:
            _SPLITTER.append(_split)
    return [p for p in _SPLITTER[0](text) if p.strip()] or [text[:PIECE_CHARS]]


def _split(text, seps=tuple(VP_SEPARATORS)):
    """The same rule without LangChain: split at the first separator that occurs, merge parts up to PIECE_CHARS, recurse on longer ones."""
    sep = next((s for s in seps if s and s in text), "")
    parts = text.split(sep) if sep else [text[i:i + PIECE_CHARS] for i in range(0, len(text), PIECE_CHARS)]
    out, buf = [], ""
    for part in parts:
        if len(part) > PIECE_CHARS:
            if buf:
                out.append(buf)
                buf = ""
            out += _split(part, seps[seps.index(sep) + 1:] if sep else ("",))
        elif not buf:
            buf = part
        elif len(buf) + len(sep) + len(part) <= PIECE_CHARS:
            buf = f"{buf}{sep}{part}"
        else:
            out.append(buf)
            buf = part
    return out + ([buf] if buf else [])


# ----------------------------------------------------------------------------------------------------------------------
# the store
# ----------------------------------------------------------------------------------------------------------------------
SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS documents (
    document_id TEXT PRIMARY KEY, source TEXT NOT NULL, kind TEXT NOT NULL, title TEXT, authors TEXT, report_number TEXT,
    publication_date TEXT, metadata TEXT);
CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY, locator TEXT NOT NULL UNIQUE, document_id TEXT, source TEXT NOT NULL, kind TEXT NOT NULL, page INTEGER,
    chunk_index INTEGER, text TEXT NOT NULL, header TEXT NOT NULL, content_hash TEXT NOT NULL, embedding_model TEXT);
CREATE INDEX IF NOT EXISTS idx_chunks_source ON chunks (source);
CREATE TABLE IF NOT EXISTS pieces (id INTEGER PRIMARY KEY, chunk_id INTEGER NOT NULL, piece INTEGER NOT NULL, text TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_pieces_chunk ON pieces (chunk_id);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(text, header, content='chunks', content_rowid='id',
                                                         tokenize='porter unicode61');
CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN
    INSERT INTO chunks_fts (rowid, text, header) VALUES (new.id, new.text, new.header); END;
CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN
    INSERT INTO chunks_fts (chunks_fts, rowid, text, header) VALUES ('delete', old.id, old.text, old.header); END;
CREATE TRIGGER IF NOT EXISTS chunks_au AFTER UPDATE OF text, header ON chunks BEGIN
    INSERT INTO chunks_fts (chunks_fts, rowid, text, header) VALUES ('delete', old.id, old.text, old.header);
    INSERT INTO chunks_fts (rowid, text, header) VALUES (new.id, new.text, new.header); END;
"""


class Index:
    def __init__(self, folder, create=False):
        self.path = Path(folder) / INDEX
        if not create and not self.path.is_file():
            raise Refused("kb", f"{Path(folder).name} has no index yet", "NotFound", 2)
        self.conn = sqlite3.connect(self.path, timeout=30)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")               # searches read while an ingest writes
        self.conn.executescript(SCHEMA)
        self.vec = False
        try:
            import sqlite_vec
            self.conn.enable_load_extension(True)
            sqlite_vec.load(self.conn)
            self.conn.enable_load_extension(False)
            self.conn.execute(f"CREATE VIRTUAL TABLE IF NOT EXISTS chunks_vec USING vec0(piece_id INTEGER PRIMARY KEY, "
                              f"embedding float[{DIMS}] distance_metric=cosine, kind text)")
            self.vec = True
        except (ImportError, AttributeError, sqlite3.OperationalError):
            pass
        self.conn.commit()

    def close(self):
        self.conn.close()

    def meta(self, key, value=None):
        if value is None:
            row = self.conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
            return row[0] if row else None
        self.conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, value))

    def stats(self):
        q = lambda sql: self.conn.execute(sql).fetchone()[0]
        return {"documents": q("SELECT count(*) FROM documents"), "text_rows": q("SELECT count(*) FROM chunks WHERE kind = 'text'"),
                "visual_rows": q("SELECT count(*) FROM chunks WHERE kind = 'visual'"), "vectors": q("SELECT count(*) FROM pieces"),
                "waiting": q("SELECT count(*) FROM chunks WHERE embedding_model IS NULL"),
                "embedding_model": self.meta("embedding_model"), "vector_search": self.vec}

    def _drop_vectors(self, chunk_ids):
        for cid in chunk_ids:
            ids = [r[0] for r in self.conn.execute("SELECT id FROM pieces WHERE chunk_id = ?", (cid,))]
            if self.vec and ids:
                self.conn.executemany("DELETE FROM chunks_vec WHERE piece_id = ?", [(i,) for i in ids])
            self.conn.execute("DELETE FROM pieces WHERE chunk_id = ?", (cid,))


def _jsonl(path):
    out = []
    try:
        lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return out
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue                                     # a partly written last line
        if isinstance(row, dict):
            out.append(row)
    return out


def _page(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def rows_of(folder):
    """(documents, rows) from Visual-Parser's files, rows as dicts ready for the chunks table."""
    folder = Path(folder)
    docs = {}
    for m in _jsonl(folder / KB_FILES["metadata"]):
        if m.get("document_id"):
            docs[m["document_id"]] = {"document_id": m["document_id"], "source": m.get("source") or "", "kind": "pdf",
                                      "title": m.get("title"), "authors": json.dumps(m.get("authors")) if m.get("authors") else None,
                                      "report_number": m.get("report_number"), "publication_date": m.get("publication_date"),
                                      "metadata": json.dumps({k: v for k, v in m.items() if k not in ("document_id", "source")})}
    rows = []

    def add(kind, locator, r, text, h):
        if locator and text.strip():
            rows.append({"locator": locator, "document_id": r.get("document_id"), "source": r.get("source") or "", "kind": kind,
                         "page": _page(r.get("page")), "chunk_index": _page(r.get("chunk_index", r.get("figure_index"))),
                         "text": text.strip(), "header": h,
                         "content_hash": hashlib.sha1(f"{h}\n{text.strip()}".encode("utf-8")).hexdigest()})

    for r in _jsonl(folder / KB_FILES["text"]):
        d = docs.get(r.get("document_id")) or {}
        add("text", r.get("chunk_id"), r, str(r.get("content") or ""), header(d.get("title"), r.get("source"), d.get("report_number"),
                                                                              _page(r.get("page"))))
    for r in _jsonl(folder / KB_FILES["visual"]):
        d = docs.get(r.get("document_id")) or {}
        desc = r.get("description")
        desc = "\n".join(map(str, desc)) if isinstance(desc, list) else str(desc or "")
        add("visual", r.get("figure_id"), r, desc, header(d.get("title"), r.get("source"), d.get("report_number"), _page(r.get("page")),
                                                          r.get("figure_index")))
    for r in _jsonl(folder / KB_FILES["image"]):
        if r.get("image_id"):
            docs[r["image_id"]] = {"document_id": r["image_id"], "source": r.get("source") or "", "kind": "image", "title": r.get("title"),
                                   "authors": None, "report_number": None, "publication_date": None,
                                   "metadata": json.dumps({k: v for k, v in r.items() if k not in ("description", "image_id")})}
        add("visual", r.get("image_id"), {**r, "document_id": r.get("image_id")}, str(r.get("description") or ""),
            header(r.get("title"), r.get("source"), image=True))
    return list(docs.values()), rows


def sync(index, folder):
    """Bring the index up to Visual-Parser's files: new rows added, changed rows replaced (their vectors dropped, so they are embedded
    again), rows no longer there removed (Visual-Parser's --redo rewrites a document's rows)."""
    documents, rows = rows_of(folder)
    c = index.conn
    have = {r["locator"]: (r["id"], r["content_hash"]) for r in c.execute("SELECT id, locator, content_hash FROM chunks")}
    added = changed = 0
    for r in rows:
        old = have.pop(r["locator"], None)
        if old is None:
            c.execute("INSERT INTO chunks (locator, document_id, source, kind, page, chunk_index, text, header, content_hash) "
                      "VALUES (:locator, :document_id, :source, :kind, :page, :chunk_index, :text, :header, :content_hash)", r)
            added += 1
        elif old[1] != r["content_hash"]:
            index._drop_vectors([old[0]])
            c.execute("UPDATE chunks SET document_id = :document_id, source = :source, kind = :kind, page = :page, "
                      "chunk_index = :chunk_index, text = :text, header = :header, content_hash = :content_hash, embedding_model = NULL "
                      "WHERE id = :id", {**r, "id": old[0]})
            changed += 1
    gone = [cid for cid, _ in have.values()]
    index._drop_vectors(gone)
    c.executemany("DELETE FROM chunks WHERE id = ?", [(cid,) for cid in gone])
    c.execute("DELETE FROM documents")
    c.executemany("INSERT OR REPLACE INTO documents (document_id, source, kind, title, authors, report_number, publication_date, metadata) "
                  "VALUES (:document_id, :source, :kind, :title, :authors, :report_number, :publication_date, :metadata)", documents)
    c.commit()
    return {"added": added, "changed": changed, "removed": len(gone), "rows": len(rows)}


def waiting(index, model_id=None):
    """Rows that still need vectors: none yet, or (when model_id is given and differs from the index's) all of them."""
    if not index.vec:
        return 0
    if model_id and index.meta("embedding_model") not in (None, model_id):
        return index.conn.execute("SELECT count(*) FROM chunks").fetchone()[0]
    return index.conn.execute("SELECT count(*) FROM chunks WHERE embedding_model IS NULL").fetchone()[0]


def embed_waiting(index, embedder, progress=None):
    """Embed every row without vectors; when the embedding model changed, every row again. Returns the number of rows embedded."""
    if not index.vec:
        raise EmbedderUnavailable("sqlite-vec is not installed (pip install sqlite-vec), so rows cannot hold vectors")
    model_id = embedder.model_id()
    c = index.conn
    if index.meta("embedding_model") not in (None, model_id):
        c.execute("DELETE FROM chunks_vec")
        c.execute("DELETE FROM pieces")
        c.execute("UPDATE chunks SET embedding_model = NULL")
    index.meta("embedding_model", model_id)
    index.meta("dims", str(DIMS))
    c.commit()
    todo = [dict(r) for r in c.execute("SELECT id, kind, text, header FROM chunks WHERE embedding_model IS NULL ORDER BY id")]
    done = 0
    for i in range(0, len(todo), 256):
        part = todo[i:i + 256]
        units = [(r, n, p) for r in part for n, p in enumerate(pieces(r["text"]))]
        vectors = embedder.embed([f"{DOC_PREFIX}{r['header']}\n{p}" for r, _, p in units])
        if len(vectors) != len(units) or any(len(v) != DIMS for v in vectors):
            raise EmbedderUnavailable(f"the embedding model returned {len(vectors)} vectors for {len(units)} texts, or not {DIMS} numbers")
        import sqlite_vec
        for (r, n, p), v in zip(units, vectors):
            pid = c.execute("INSERT INTO pieces (chunk_id, piece, text) VALUES (?, ?, ?)", (r["id"], n, p)).lastrowid
            c.execute("INSERT INTO chunks_vec (piece_id, embedding, kind) VALUES (?, ?, ?)", (pid, sqlite_vec.serialize_float32(v), r["kind"]))
        c.executemany("UPDATE chunks SET embedding_model = ? WHERE id = ?", [(model_id, r["id"]) for r in part])
        c.commit()
        done += len(part)
        if progress:
            progress(done, len(todo))
    return done


# ----------------------------------------------------------------------------------------------------------------------
# search
# ----------------------------------------------------------------------------------------------------------------------
# Axiom's full-text term rules (axiom/rag/fts_query.py): words of three or more characters and short codes, common words left out,
# joined by OR so a long question still recalls rows
FTS_STOP = frozenset("""the a an of for and or to in on at is are was were be by with as it its this that these those what which who
whom how does do did either than then from into within between about also any all can could would should may might will shall not
no but if so such we you our your there""".split())


def fts_terms(text):
    low, out = (text or "").lower(), []
    for tok in re.findall(r"[a-z]+[0-9]+|[0-9]+[a-z]+", low) + re.findall(r"[a-z0-9]{3,}", low):
        lex = re.sub(r"[^a-z0-9]", "", tok)
        if len(lex) >= 2 and lex not in FTS_STOP and lex not in out:
            out.append(lex)
    return out


def names_a_visual(question):
    return bool(set(re.findall(r"[a-z0-9]+", question.lower())) & VISUAL_TERMS)


def _vector_hits(index, qvec, kinds, n):
    import sqlite_vec
    k = min(4096, max(64, 8 * n))
    sql = "SELECT piece_id, distance FROM chunks_vec WHERE embedding MATCH ? AND k = ?"
    params = [sqlite_vec.serialize_float32(qvec), k]
    if kinds and len(kinds) == 1:
        sql += " AND kind = ?"
        params.append(kinds[0])
    best = {}
    for pid, dist in index.conn.execute(sql, params):
        row = index.conn.execute("SELECT chunk_id FROM pieces WHERE id = ?", (pid,)).fetchone()
        if row and (row[0] not in best or dist < best[row[0]][0]):
            best[row[0]] = (dist, pid)
    return [(cid, 1.0 - d, pid) for cid, (d, pid) in sorted(best.items(), key=lambda kv: kv[1][0])][:2 * n]


def _text_hits(index, query, kinds, n):
    terms = fts_terms(query)
    if not terms:
        return []
    sql = ("SELECT f.rowid, bm25(chunks_fts) FROM chunks_fts f JOIN chunks c ON c.id = f.rowid WHERE chunks_fts MATCH ?"
           + (" AND c.kind = ?" if kinds and len(kinds) == 1 else "") + " ORDER BY bm25(chunks_fts) LIMIT ?")
    params = [" OR ".join(f'"{t}"' for t in terms)] + ([kinds[0]] if kinds and len(kinds) == 1 else []) + [2 * n]
    return [(cid, None, None) for cid, _ in index.conn.execute(sql, params)]


def search(indexes, query, kinds=None, max_results=8):
    """The best rows of these knowledge bases [(name, Index)] for a question: (rows, notes). Each row once; visual rows first when the
    question names a visual; at most TEXT_BUDGET characters, past which a visual gives its matching piece instead of its whole text."""
    notes, qvec, model_id = [], None, None
    try:
        embedder = make_embedder()
        model_id = embedder.model_id()
    except EmbedderUnavailable as e:
        embedder = None
        notes.append(f"{e}; searched by full text")
    hits = []                                            # (score or None, name, index, chunk id, piece id)
    for name, index in indexes:
        usable = embedder is not None and index.vec and index.meta("embedding_model") == model_id
        if usable and index.conn.execute("SELECT 1 FROM pieces LIMIT 1").fetchone():
            if qvec is None:
                qvec = embedder.embed([f"{QUERY_PREFIX}{query}"])[0]
            hits += [(s, name, index, cid, pid) for cid, s, pid in _vector_hits(index, qvec, kinds, max_results)]
            left = waiting(index)
            if left:
                notes.append(f"{name}: {left} rows not embedded yet are not searched by meaning")
        else:
            if embedder is not None and index.vec and index.meta("embedding_model") not in (None, model_id):
                notes.append(f"{name} was embedded with {index.meta('embedding_model')}, not {model_id}; searched by full text until "
                             f"the folder is ingested again")
            elif embedder is not None and not index.vec:
                notes.append("sqlite-vec is not installed; searched by full text")
            hits += [(None, name, index, cid, None) for cid, *_ in _text_hits(index, query, kinds, max_results)]
    scored = sorted((h for h in hits if h[0] is not None), key=lambda h: -h[0])
    ranked = scored + [h for h in hits if h[0] is None]
    rows = [(h, dict(h[2].conn.execute("SELECT * FROM chunks WHERE id = ?", (h[3],)).fetchone())) for h in ranked]
    if names_a_visual(query):
        window = 2 * max_results
        rows = sorted(rows[:window], key=lambda x: 0 if x[1]["kind"] == "visual" else 1) + rows[window:]
    out, budget = [], TEXT_BUDGET
    for (score, name, index, cid, pid), r in rows[:max_results]:
        text, cut = r["text"], False
        if len(text) > budget and pid is not None:
            piece = index.conn.execute("SELECT text FROM pieces WHERE id = ?", (pid,)).fetchone()
            text, cut = (piece[0] if piece else text[:max(budget, 500)]), True
        elif len(text) > budget:
            text, cut = text[:max(budget, 500)], True
        budget -= len(text)
        doc = index.conn.execute("SELECT title, report_number FROM documents WHERE document_id = ?", (r["document_id"],)).fetchone()
        out.append({"kb": name, "file": r["source"], "kind": r["kind"], "page": r["page"], "locator": r["locator"],
                    "title": doc["title"] if doc else None, "report_number": doc["report_number"] if doc else None,
                    "score": round(score, 3) if score is not None else None, "text": text,
                    **({"cut": "only the piece that matched; the whole description is longer"} if cut else {})})
    return out, notes
