"""
GT-3R (devops/AutoMC_General_Tools_Plan.md): knowledge bases from folders. A fake `visual-parser` command behaves as the real one where
it matters here (reads its input folder, keeps its lists of parsed files in the output folder, --redo removes a file's rows, writes the
four knowledge-base files) and records which variables it was given; a fake embedder (tests/documents/fake_embedder.py) hashes words
into 768 numbers. So every rule is checked without a model: what is new, changed, re-saved, copied or named twice; the vision model
follows the folder's label, a folder that stays on this machine gets no key at all; the tool only plans; a run is a job settled from
its files; the document folder is only read; rows are indexed, embedded and searched by meaning, each once, long descriptions in
pieces; full text when no embedding model answers; failure, cancel, one run at a time; the labels everything carries. Needs PyMuPDF
(the WSL automc environment); the runs also need Linux or WSL, and vector search needs sqlite-vec.
"""
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import automc_paths
from agent.llm import Provider
from tools import registry
from tools.documents import index, parse, search
from tools.files import places
from tools.files.places import Refused
from tools.openmc import jobs

HAVE_PYMUPDF = importlib.util.find_spec("pymupdf") is not None
HAVE_VEC = importlib.util.find_spec("sqlite_vec") is not None
POSIX = os.name == "posix"
FAKE_KEY = "sk-test-not-a-real-key-0123456789"
FAKE_EMBEDDER = "tests.documents.fake_embedder:FakeEmbedder"

OPENAI = Provider(name="openai", kind="cloud", account="personal", backend="openai_compatible", base_url="https://api.openai.com/v1",
                  model="gpt-5.4", api_key_env="AUTOMC_TEST_OPENAI_KEY")
PORTKEY = Provider(name="openai-portkey", kind="cloud", account="ut", backend="openai_compatible", base_url="https://api.portkey.ai/v1",
                   model="@team-openai-x1/gpt-5.4", api_key_env="AUTOMC_TEST_PORTKEY_KEY")
GEMINI = Provider(name="gemini", kind="cloud", account="personal", backend="google_genai",
                  base_url="https://generativelanguage.googleapis.com/v1beta", model="gemini-3.8-flash", api_key_env="AUTOMC_TEST_GEMINI_KEY")
LOCAL = Provider(name="local", kind="local", backend="ollama", base_url="http://127.0.0.1:11434", model="qwen3.6:35b-32k-t0")

FAKE_VP = r'''#!{python}
"""A stand-in for visual-parser: the same options, folders and output files, no models."""
import argparse, hashlib, json, os, sys, time
from pathlib import Path
ap = argparse.ArgumentParser()
for o in ("--input-dir", "--output-dir", "--text-mode", "--vision-provider", "--vision-model", "--max-workers", "--metadata-pages",
          "--log-level"):
    ap.add_argument(o)
ap.add_argument("--redo", action="append", default=[])
a = ap.parse_args()
inp, out = Path(a.input_dir), Path(a.output_dir)
keys = {keys!r}
env = {{k: os.environ.get(k) for k in keys}}
report = {{"args": vars(a), "set": {{k: bool(v) for k, v in env.items()}}, "present": {{k: k in os.environ for k in keys}},
          "openai_key_ok": os.environ.get("OPENAI_API_KEY") == {fake!r}, "portkey_key_ok": os.environ.get("PORTKEY_API_KEY") == {fake!r},
          "env_file": os.environ.get("VISUAL_PARSER_ENV_FILE"), "automc_env_file": os.environ.get("AUTOMC_ENV_FILE"),
          "slug": os.environ.get("PORTKEY_OPENAI_PROVIDER_SLUG"), "force_portkey": os.environ.get("VISUAL_PARSER_FORCE_PORTKEY"),
          "tracing": os.environ.get("LANGCHAIN_TRACING_V2"), "inputs": sorted(os.listdir(inp)),
          "links": all(os.path.islink(inp / n) for n in os.listdir(inp))}}
with open(out / "fake_runs.jsonl", "a") as fh:
    fh.write(json.dumps(report) + "\n")
print("fake visual-parser starting", flush=True)
time.sleep(float(os.environ.get("FAKE_VP_SLEEP", "0")))
if os.environ.get("FAKE_VP_FAIL"):
    print("[ERROR] boom: the vision model did not answer", flush=True)
    sys.exit(1)
REG = {{"pdf": out / "04_processed_pdfs.txt", "image": out / "parsed_images.txt"}}
FILES = ("01_chunks_kb.jsonl", "02_visuals_kb.jsonl", "03_metadata_kb.jsonl", "image_descriptions_kb.jsonl")
def listed(p):
    return set(p.read_text().split("\n")) - {{""}} if p.exists() else set()
for name in a.redo:                                         # as visual_parser/kb_redo.py: the file's rows and list entries go
    for f in FILES:
        p = out / f
        if p.exists():
            keep = [l for l in p.read_text().splitlines() if l.strip() and json.loads(l).get("source") != name]
            p.write_text("".join(k + "\n" for k in keep))
    for p in REG.values():
        if p.exists():
            p.write_text("".join(n + "\n" for n in sorted(listed(p) - {{name}})))
def write(f, row):
    with open(out / f, "a") as fh:
        fh.write(json.dumps(row) + "\n")
import pymupdf
for f in sorted(os.listdir(inp)):
    src, doc = inp / f, hashlib.sha1(f.encode()).hexdigest()[:16]
    if f == os.environ.get("FAKE_VP_FAIL_FILE"):
        print(f"[ERROR] {{f}}: the vision model did not answer", flush=True)
        continue
    if f.lower().endswith(".pdf"):
        if f in listed(REG["pdf"]):
            continue
        d = pymupdf.open(src)
        texts, title = [pg.get_text().strip() for pg in d], d.metadata.get("title") or f"A Test Report {{f}}"
        d.close()
        for p, t in enumerate(texts, 1):
            write("01_chunks_kb.jsonl", {{"source": f, "page": str(p), "content": t, "document_id": doc, "chunk_id": f"{{doc}}:p{{p}}:c0",
                                         "chunk_index": 0}})
        desc = f"**Subject:** Figure 1, the core map of {{f}}\n**Dimensions & Data:** {{texts[0]}}"
        if os.environ.get("FAKE_VP_LONG"):
            desc += "\n" + "\n".join(f"- row {{i}}: rod position {{i * 7}} cm" for i in range(400)) + "\n- last row: graphite reflector"
        write("02_visuals_kb.jsonl", {{"source": f, "page": "1", "figure_id": f"{{doc}}:p1:f0", "figure_index": "0", "document_id": doc,
                                      "description": desc}})
        write("03_metadata_kb.jsonl", {{"source": f, "document_id": doc, "title": title, "authors": ["A. Writer"],
                                       "facility_data": "public"}})
        if os.environ.get("FAKE_VP_VISION_EMPTY"):
            with open(out / "05_pipeline.log", "a") as fh:
                fh.write(f"2026-10-31 12:05:25,348 WARNING visual_parser.figure_describer: Could not parse JSON for {{f}} p2: ''\n")
        with open(REG["pdf"], "a") as fh:
            fh.write(f + "\n")
    else:
        if f in listed(REG["image"]):
            continue
        write("image_descriptions_kb.jsonl", {{"source": f, "image_id": doc, "title": "Control rod drive",
                                               "description": "**Subject:** a drawing of the control rod drive mechanism"}})
        with open(REG["image"], "a") as fh:
            fh.write(f + "\n")
print("fake visual-parser done", flush=True)
'''


def make_pdf(path, pages=("zirconium hydride fuel in the reactor core",), title=None):
    import pymupdf
    d = pymupdf.open()
    for t in pages:
        d.new_page().insert_text((72, 100), t, fontsize=11)
    if title:
        d.set_metadata({"title": title})
    path.parent.mkdir(parents=True, exist_ok=True)
    d.save(path)
    d.close()
    return path


def make_png(path, color=(200, 30, 30), info=None):
    from PIL import Image, PngImagePlugin
    meta = None
    if info:
        meta = PngImagePlugin.PngInfo()
        meta.add_text("Comment", info)
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (8, 8), color).save(path, pnginfo=meta)
    return path


def labels_in(obj):
    found = []

    def walk(x):
        if isinstance(x, dict):
            for k, v in x.items():
                if k == "facility_data":
                    found.append(v)
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    walk(obj)
    return found


def flat(text):
    """A plan's text with its line wrapping undone, so a phrase is found wherever the terminal width broke it."""
    return " ".join(text.split())


def tree(folder):
    return sorted((p.relative_to(folder).as_posix(), p.stat().st_mtime_ns) for p in Path(folder).rglob("*"))


class Pieces(unittest.TestCase):
    def test_a_row_is_split_only_when_longer_than_a_piece_and_nothing_is_lost(self):
        self.assertEqual(index.pieces("short"), ["short"])
        long = "\n".join(f"- row {i}: rod position {i * 7} cm" for i in range(400))
        for split in (index.pieces, index._split):
            with self.subTest(split=split.__name__):
                got = split(long)
                self.assertGreater(len(got), 5)
                self.assertTrue(all(len(p) <= index.PIECE_CHARS for p in got))
                self.assertEqual("".join(got).replace("\n", ""), long.replace("\n", ""))
        self.assertTrue(all(len(p) <= index.PIECE_CHARS for p in index._split("x" * 4000)))

    def test_the_header_and_the_limit(self):
        h = index.header("A Report", "r.pdf", "ORNL-1", 3, "0")
        self.assertEqual(h, "A Report | ORNL-1 | p. 3 | figure 1")
        self.assertEqual(index.header(None, "rod.png", image=True), "rod.png | image rod.png")
        self.assertLessEqual(len(index.header("T" * 1000, "x.pdf", None, 1)), index.HEADER_CHARS)
        worst = len(index.DOC_PREFIX) + index.HEADER_CHARS + 1 + index.PIECE_CHARS
        self.assertLess(worst + 2, 2048)                    # a token covers at least one character: under nomic's limit

    def test_query_words_and_visual_questions(self):
        self.assertEqual(index.fts_terms("What is the U235 enrichment of the fuel?"), ["u235", "enrichment", "fuel"])
        self.assertTrue(index.names_a_visual("What do the figures show?"))           # RADIANT-LLM's rule misses the plural
        self.assertTrue(index.names_a_visual("the curve in the chart"))
        self.assertFalse(index.names_a_visual("What is the core barrel radius?"))

    def test_comparing_fingerprints(self):
        a = ["t1.d1.p1", "t2.d2.p2"]
        self.assertTrue(parse.compare(a, list(a))["same"])
        self.assertEqual(parse.compare(a, ["t1.d9.p1", "t2.d2.p2"])["drawing_only"], [1])
        self.assertEqual(parse.compare(a, ["t1.d1.p1", "t9.d9.p2"])["pages"], [2])
        self.assertEqual(parse.compare(a, a + ["t3.d3.p3"])["pages"], [3])
        self.assertTrue(parse.compare(None, a)["unreadable"])


@unittest.skipUnless(HAVE_PYMUPDF, "needs PyMuPDF (the WSL automc environment)")
class Fingerprints(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="automc_fp_"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_a_highlight_or_a_new_title_is_not_a_change_and_an_edit_is(self):
        import pymupdf
        base = make_pdf(self.tmp / "a.pdf", ["The core holds 85 fuel elements.", "Page two."])
        fp = parse.fingerprint(base)
        hl = self.tmp / "hl.pdf"
        shutil.copy(base, hl)
        d = pymupdf.open(hl)
        d[0].add_highlight_annot(d[0].search_for("85 fuel elements"))
        d[0].add_text_annot((80, 300), "check this")
        d.saveIncr()
        d.close()
        d = pymupdf.open(hl)
        d.save(self.tmp / "hl_saved_as.pdf", garbage=4, deflate=True)
        d.set_metadata({"title": "another title"})
        d.save(self.tmp / "retitled.pdf")
        d.close()
        for f in ("hl.pdf", "hl_saved_as.pdf", "retitled.pdf"):
            with self.subTest(f=f):
                self.assertNotEqual(parse._sha256(self.tmp / f), parse._sha256(base))
                self.assertTrue(parse.compare(fp, parse.fingerprint(self.tmp / f))["same"])
        d = pymupdf.open(base)
        d[1].insert_text((72, 140), "Revised: 87 fuel elements.")
        d.save(self.tmp / "edited.pdf")
        d.close()
        self.assertEqual(parse.compare(fp, parse.fingerprint(self.tmp / "edited.pdf"))["pages"], [2])
        d = pymupdf.open(hl)
        d.bake(annots=True)                                 # the highlight flattened into the page
        d.save(self.tmp / "flat.pdf")
        d.close()
        flat = parse.compare(fp, parse.fingerprint(self.tmp / "flat.pdf"))
        self.assertEqual((flat["pages"], flat["drawing_only"]), ([], [1]))

    def test_an_images_pixels_count_not_its_metadata(self):
        a = make_png(self.tmp / "a.png")
        b = make_png(self.tmp / "b.png", info="edited in a viewer")
        c = make_png(self.tmp / "c.png", color=(0, 0, 0))
        self.assertNotEqual(parse._sha256(a), parse._sha256(b))
        self.assertEqual(parse.fingerprint(a), parse.fingerprint(b))
        self.assertNotEqual(parse.fingerprint(a), parse.fingerprint(c))


@unittest.skipUnless(HAVE_PYMUPDF, "needs PyMuPDF (the WSL automc environment)")
class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="automc_docs_")).resolve()
        self.ws, self.public, self.outside, self.restricted = (self.tmp / n for n in ("ws", "public", "outside", "restricted"))
        for d in (self.ws, self.public, self.outside, self.restricted):
            d.mkdir()
        import automc_settings
        from agent import guard
        self.patches = [mock.patch.object(automc_paths, "WORKSPACE", self.ws),
                        mock.patch.object(places, "restricted_dirs", return_value=[self.restricted]),
                        mock.patch.object(parse, "_ollama_loaded", return_value=[]),            # no call to the shared Ollama
                        mock.patch.dict(os.environ),
                        # facility mode, whether or not a restricted pack is installed here: a restricted folder is declared
                        mock.patch.object(automc_settings, "restricted_pack_installed", return_value=True),
                        mock.patch.object(guard, "restricted_pack_installed", return_value=True)]
        for p in self.patches:
            p.start()
        for n in ("AUTOMC_ENV_FILE", "OPENAI_API_KEY", "PORTKEY_API_KEY", "GEMINI_API_KEY", "FAKE_VP_FAIL", "FAKE_VP_SLEEP",
                  "FAKE_VP_LONG", "FAKE_VP_FAIL_FILE", "FAKE_VP_VISION_EMPTY"):
            os.environ.pop(n, None)
        os.environ[places.ROOTS_ENV] = os.pathsep.join(str(d) for d in (self.public, self.outside))
        os.environ[places.PUBLIC_ENV] = str(self.public)
        os.environ.update(AUTOMC_TEST_OPENAI_KEY=FAKE_KEY, AUTOMC_TEST_PORTKEY_KEY=FAKE_KEY, AUTOMC_TEST_GEMINI_KEY=FAKE_KEY)
        os.environ[index.EMBEDDER_ENV] = FAKE_EMBEDDER
        fake = self.tmp / "fake-visual-parser"
        fake.write_text(FAKE_VP.format(python=sys.executable, keys=parse.CLOUD_KEYS, fake=FAKE_KEY))
        fake.chmod(0o755)
        os.environ[parse.COMMAND_ENV] = str(fake)

    def tearDown(self):
        if parse.docs_dir().is_dir():
            for j in jobs.list_jobs(parse.docs_dir(), active_only=True):         # never leave a run behind
                try:
                    parse.cancel(j["job_id"])
                except Refused:
                    pass
        for p in reversed(self.patches):
            p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def pdf(self, where="outside", name="report.pdf", pages=("zirconium hydride fuel in the reactor core",), title=None):
        return make_pdf(getattr(self, where) / "docs" / name, pages, title)

    def folder(self, where="outside"):
        return getattr(self, where) / "docs"


class Plans(Base):
    def test_what_is_refused(self):
        pdf = self.pdf()
        (self.outside / "empty").mkdir()
        for args, field in (((str(pdf),), "folder"), ((str(self.outside / "empty"),), "folder"),
                            ((str(self.folder()), None, "fast"), "text_mode"), ((str(self.tmp / "nowhere"),), "folder"),
                            ((str(self.folder()), None, None, ["other.pdf"]), "redo")):
            with self.subTest(args=args):
                with self.assertRaises(Refused) as ctx:
                    parse.plan_parse(*args)
                self.assertEqual(ctx.exception.field, field)

    def test_a_public_folder_with_a_cloud_chat_model_uses_its_service(self):
        self.pdf("public")
        plan = parse.plan_parse(str(self.folder("public")), provider=OPENAI)
        self.assertEqual((plan["label"], plan["vision"]["route"], plan["vision"]["model"]), ("public", "openai", "gpt-5.4"))
        self.assertIn("OpenAI (gpt-5.4), with your key", flat(plan["text"]))
        self.assertNotIn(FAKE_KEY, json.dumps(plan))
        p = parse.plan_parse(str(self.folder("public")), provider=PORTKEY)["vision"]
        self.assertEqual((p["route"], p["model"], p["slug"]), ("portkey", "gpt-5.4", "team-openai-x1"))
        self.assertEqual(parse.plan_parse(str(self.folder("public")), provider=GEMINI)["vision"]["route"], "gemini")

    def test_folders_not_known_to_be_public_stay_on_this_machine_whatever_the_chat_model(self):
        for where, label in (("outside", "unknown"), ("restricted", "restricted")):
            self.pdf(where)
            for provider in (OPENAI, PORTKEY, GEMINI, LOCAL, None):
                with self.subTest(where=where, provider=provider and provider.name):
                    plan = parse.plan_parse(str(self.folder(where)), provider=provider)
                    self.assertEqual((plan["label"], plan["vision"]["route"]), (label, "local"))
                    self.assertIn("only the local model may read what it finds", flat(plan["text"]))
        self.pdf("public")
        self.assertEqual(parse.plan_parse(str(self.folder("public")), provider=LOCAL)["vision"]["route"], "local")
        del os.environ["AUTOMC_TEST_OPENAI_KEY"]
        self.assertIn("AUTOMC_TEST_OPENAI_KEY is not set", parse.plan_parse(str(self.folder("public")), provider=OPENAI)["vision"]["why"])
        with parse.chat_model(GEMINI):
            self.assertEqual(parse.plan_parse(str(self.folder("public")))["vision"]["route"], "gemini")

    def test_the_plan_lists_new_files_and_skips_copies_names_twice_and_hidden_folders(self):
        a = self.pdf(name="a.pdf")
        self.pdf(name="sub/a.pdf", pages=("another file of the same name",))
        shutil.copy(a, self.folder() / "a copy.pdf")
        make_png(self.folder() / "rod.png")
        self.pdf(name=".trash/old.pdf")
        plan = parse.plan_parse(str(self.folder()))
        self.assertEqual(sorted(f["relpath"] for f in plan["to_parse"]), ["a copy.pdf", "a.pdf", "rod.png"])  # a copy is new until a.pdf is parsed
        self.assertEqual(plan["same_name"], [{"file": "sub/a.pdf", "kept": "a.pdf"}])
        self.assertIn("Visual-Parser knows files by name", flat(plan["text"]))
        self.assertEqual((plan["kb"], plan["new_kb"], plan["nothing_to_do"]), ("docs", True, False))
        self.assertIn("2 PDF(s) and 1 image(s) not parsed before", flat(plan["text"]))
        self.assertIn("parse pdf 1 a copy.pdf", flat(plan["text"]))                  # the files table
        self.assertEqual(parse.plan_parse(str(self.folder()))["plan_id"], plan["plan_id"])

    def test_the_tool_returns_a_plan_and_starts_nothing(self):
        self.pdf()
        out = registry.call("DocumentParseTool", {"folder": str(self.folder())})
        self.assertTrue(out["ok"], out)
        self.assertEqual((out["started"], out["needs_approval"], out["facility_data"]), (False, True, "public"))
        self.assertFalse(parse.kb_dir("docs").exists())
        for sneaky in ("approved_by", "yes"):
            self.assertFalse(registry.call("DocumentParseTool", {"folder": str(self.folder()), sneaky: "chat"})["ok"])

    def test_start_needs_an_approval_and_the_same_plan(self):
        self.pdf()
        plan = parse.plan_parse(str(self.folder()))
        if POSIX:
            with self.assertRaises(Refused) as ctx:
                parse.start(plan, approved_by="")
            self.assertEqual(ctx.exception.kind, "NeedsApproval")
            self.pdf(name="b.pdf")                                                           # the folder changed after the plan
            with self.assertRaises(Refused) as ctx:
                parse.start(plan, approved_by="test")
            self.assertIn("changed since this plan was made", str(ctx.exception))


@unittest.skipUnless(POSIX and HAVE_VEC, "runs need Linux or WSL, and sqlite-vec")
class Ingest(Base):
    def ingest(self, where="outside", approved_by="test", **kw):
        job = parse.start(parse.plan_parse(str(self.folder(where)), **kw), approved_by=approved_by)
        return job, self.finished(job["job_id"])

    def finished(self, job_id, timeout=60):
        t = time.time()
        while time.time() - t < timeout:
            out = parse.check(job_id=job_id)
            if out["status"] not in jobs.ACTIVE:
                return out
            time.sleep(0.2)
        raise AssertionError("the run did not end")

    def vp_runs(self, kb="docs"):
        p = parse.kb_dir(kb) / "fake_runs.jsonl"
        return [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []

    def test_a_folder_that_stays_here_gets_no_key_at_all_and_is_only_read(self):
        self.pdf()
        make_png(self.folder() / "rod.png")
        before = tree(self.folder())
        os.environ.update(OPENAI_API_KEY=FAKE_KEY, GEMINI_API_KEY=FAKE_KEY, LANGCHAIN_TRACING_V2="true",
                          AUTOMC_ENV_FILE=str(self.tmp / "keys.env"))                         # even when the shell has them
        with parse.chat_model(OPENAI):
            job, done = self.ingest()
        self.assertEqual(done["status"], "success", done)
        r = self.vp_runs()[0]
        self.assertTrue(all(r["present"].values()), r["present"])                           # every key variable set...
        self.assertFalse(any(r["set"].values()), r["set"])                                  # ...and empty
        self.assertEqual((r["args"]["vision_provider"], r["args"]["vision_model"], r["tracing"], r["automc_env_file"]),
                         ("ollama", None, "false", None))
        self.assertEqual(Path(r["env_file"]).read_text(), "")
        self.assertEqual((r["inputs"], r["links"]), (["report.pdf", "rod.png"], True))      # links to the files, nothing else
        self.assertEqual(tree(self.folder()), before)                                        # the document folder is untouched
        self.assertFalse((parse.kb_dir("docs") / parse.STAGING).exists() and any((parse.kb_dir("docs") / parse.STAGING).iterdir()))
        kb = done["kb_summary"]
        self.assertEqual((kb["status"], kb["index"]["documents"], kb["index"]["waiting"]), ("ready", 2, 0))
        self.assertEqual(kb["index"]["vectors"], kb["index"]["text_rows"] + kb["index"]["visual_rows"])
        self.assertEqual(kb["index"]["embedding_model"], "fake:bag-of-words@1")
        self.assertEqual(set(labels_in(done)), {"unknown"})
        rec = parse.record("docs")
        self.assertEqual(len(rec["files"]["report.pdf"]["fingerprint"]), 1)
        self.assertEqual(jobs.read_sentinel(parse.kb_dir("docs") / parse.LOG)[:2], (0, "success"))

    def test_a_public_folder_gets_only_its_services_key(self):
        self.pdf("public")
        _, done = self.ingest("public", provider=OPENAI)
        self.assertEqual(done["status"], "success", done)
        r = self.vp_runs()[0]
        self.assertTrue(r["openai_key_ok"])
        self.assertEqual({k for k, v in r["set"].items() if v}, {"OPENAI_API_KEY"})
        self.assertEqual((r["args"]["vision_provider"], r["args"]["vision_model"]), ("gpt", "gpt-5.4"))
        self.pdf("public", "b.pdf")
        _, done = self.ingest("public", provider=PORTKEY)
        r = self.vp_runs()[-1]
        self.assertTrue(r["portkey_key_ok"])
        self.assertEqual((r["slug"], r["force_portkey"], r["inputs"]), ("team-openai-x1", "true", ["b.pdf"]))
        for f in (parse.RECORD, parse.REQUEST, "index.sqlite"):
            self.assertNotIn(FAKE_KEY.encode(), (parse.kb_dir("docs") / f).read_bytes())

    def test_files_added_later_are_the_only_ones_parsed_and_search_covers_all(self):
        self.pdf(name="a.pdf", pages=("zirconium hydride fuel in the reactor core",))
        self.ingest()
        self.pdf(name="b.pdf", pages=("the graphite reflector surrounds the core",))
        make_png(self.folder() / "rod.png")
        plan = parse.plan_parse(str(self.folder()))
        self.assertEqual((sorted(f["name"] for f in plan["to_parse"]), plan["unchanged"], plan["new_kb"]),
                         (["b.pdf", "rod.png"], 1, False))
        self.assertIn("Update the knowledge base 'docs'", flat(plan["text"]))
        _, done = self.ingest()
        self.assertEqual(self.vp_runs()[-1]["inputs"], ["b.pdf", "rod.png"])
        self.assertEqual(done["kb_summary"]["index"]["documents"], 3)
        hits = registry.call("DocumentSearchTool", {"query": "graphite reflector"})
        self.assertTrue(hits["ok"], hits)
        self.assertEqual((hits["results"][0]["file"], hits["results"][0]["page"]), ("b.pdf", 1))
        self.assertIsNotNone(hits["results"][0]["score"])
        rod = registry.call("DocumentSearchTool", {"query": "control rod drive mechanism", "kinds": ["visual"]})["results"][0]
        self.assertEqual((rod["file"], rod["kind"]), ("rod.png", "visual"))
        again = registry.call("DocumentParseTool", {"folder": str(self.folder())})
        self.assertEqual((again["nothing_to_do"], again["kb"]["kb"]), (True, "docs"))         # no approval, no run

    def test_the_command_line_prints_banners_and_tables(self):
        import contextlib
        import io

        from tools.documents import cli
        self.pdf(name="a.pdf", pages=("zirconium hydride fuel in the reactor core",), title="A Core Report")
        job, _ = self.ingest()

        def run(*argv):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(cli.main(list(argv)), 0)
            return out.getvalue()

        listed = run("list")
        self.assertIn("=" * 78, listed)
        self.assertIn("Name  Status  Files  Label", listed)
        kb = run("check", "--kb", "docs")
        self.assertIn(" Knowledge base docs   ready", kb)
        self.assertRegex(kb, r"parsed\s+1\s+a\.pdf\s+A Core Report")
        found = run("search", "zirconium", "hydride", "fuel")
        self.assertIn("#  Score  Kind    Page  File", found)
        self.assertIn("[1] a.pdf, page 1", found)
        self.assertNotIn("**", found)                                                        # markdown markers taken off
        watched = run("watch", job["job_id"])
        self.assertIn(f" Run {job['job_id']}", watched)
        self.assertIn("success", watched)
        self.assertIn("Nothing new to parse or embed", run("ingest", str(self.folder())))

    def test_a_highlight_is_not_a_change_an_edit_is_and_only_redo_parses_it_again(self):
        import pymupdf
        pdf = self.pdf(pages=("the core holds 85 fuel elements",))
        self.ingest()
        d = pymupdf.open(pdf)
        d[0].add_highlight_annot(d[0].search_for("85 fuel elements"))
        d.saveIncr()
        d.close()
        plan = parse.plan_parse(str(self.folder()))
        self.assertEqual((plan["nothing_to_do"], plan["resaved"]), (True, ["report.pdf"]))
        self.assertIn("re-saved with the same content", flat(plan["text"]))
        d = pymupdf.open()
        d.new_page().insert_text((72, 100), "the core holds 90 fuel elements after the reload", fontsize=11)
        d.save(pdf)
        d.close()
        plan = parse.plan_parse(str(self.folder()))
        self.assertEqual((plan["to_parse"], [c["pages"] for c in plan["changed"]]), ([], [[1]]))
        self.assertIn("not parsed again unless you name it", flat(plan["text"]))
        out = registry.call("DocumentParseTool", {"folder": str(self.folder())})
        self.assertEqual((out["nothing_to_do"], out["changed"][0]["name"]), (True, "report.pdf"))
        _, done = self.ingest(redo=["report.pdf"])
        self.assertEqual(done["status"], "success", done)
        self.assertEqual(self.vp_runs()[-1]["args"]["redo"], ["report.pdf"])
        top = registry.call("DocumentSearchTool", {"query": "fuel elements after the reload"})["results"]
        texts = " ".join(r["text"] for r in top)
        self.assertIn("90 fuel elements", texts)
        self.assertNotIn("85 fuel elements", texts)                                         # the old rows are gone
        self.assertTrue(parse.plan_parse(str(self.folder()))["nothing_to_do"])

    def test_a_copy_under_another_name_is_skipped(self):
        a = self.pdf(name="a.pdf")
        self.ingest()
        shutil.copy(a, self.folder() / "a (1).pdf")
        plan = parse.plan_parse(str(self.folder()))
        self.assertEqual((plan["copies"], plan["nothing_to_do"]), ([{"file": "a (1).pdf", "same_as": "a.pdf"}], True))

    def test_a_long_description_is_embedded_in_pieces_and_returned_once_and_whole(self):
        os.environ["FAKE_VP_LONG"] = "1"
        self.pdf()
        _, done = self.ingest()
        ix = done["kb_summary"]["index"]
        self.assertGreater(ix["vectors"], ix["text_rows"] + ix["visual_rows"] + 5)
        res = registry.call("DocumentSearchTool", {"query": "graphite reflector figure"})["results"]
        visual = [r for r in res if r["kind"] == "visual"]
        self.assertEqual(len(visual), 1)                                                     # one figure, once
        self.assertEqual(res[0]["kind"], "visual")                                           # a visual question: figures first
        self.assertIn("- row 0: rod position 0 cm", visual[0]["text"])
        self.assertIn("graphite reflector", visual[0]["text"])
        self.assertNotIn("cut", visual[0])
        with mock.patch.object(index, "TEXT_BUDGET", 100):
            cut = registry.call("DocumentSearchTool", {"query": "graphite reflector", "kinds": ["visual"]})["results"][0]
        self.assertIn("cut", cut)
        self.assertLessEqual(len(cut["text"]), index.PIECE_CHARS)
        self.assertIn("graphite reflector", cut["text"])                                    # the piece that matched

    def test_without_an_embedding_model_rows_wait_and_search_uses_full_text(self):
        os.environ[index.EMBEDDER_ENV] = "tests.documents.fake_embedder:Unavailable"
        self.pdf()
        _, done = self.ingest()
        self.assertEqual(done["status"], "success", done)
        self.assertIn("rows not embedded", done["kb_summary"]["last_run"]["warnings"][0])
        self.assertEqual(done["kb_summary"]["index"]["vectors"], 0)
        hits = registry.call("DocumentSearchTool", {"query": "zirconium hydride"})
        self.assertEqual((hits["results"][0]["page"], hits["results"][0]["score"]), (1, None))
        self.assertIn("searched by full text", hits["notes"][0])
        os.environ[index.EMBEDDER_ENV] = FAKE_EMBEDDER
        plan = parse.plan_parse(str(self.folder()))
        self.assertEqual((plan["to_parse"], plan["nothing_to_do"], plan["waiting_rows"]), ([], False, 2))
        self.assertIn("2 rows waiting to be embedded", flat(plan["text"]))
        _, done = self.ingest()
        self.assertEqual(done["kb_summary"]["index"]["waiting"], 0)
        self.assertEqual(len(self.vp_runs()), 1)                                            # embedded only; nothing parsed again
        self.assertIsNotNone(registry.call("DocumentSearchTool", {"query": "zirconium hydride"})["results"][0]["score"])

    def test_another_embedding_model_is_not_mixed_in(self):
        self.pdf()
        self.ingest()
        os.environ[index.EMBEDDER_ENV] = "tests.documents.fake_embedder:OtherModel"
        hits = registry.call("DocumentSearchTool", {"query": "zirconium hydride"})
        self.assertIsNone(hits["results"][0]["score"])
        self.assertIn("was embedded with fake:bag-of-words@1", hits["notes"][0])
        plan = parse.plan_parse(str(self.folder()))
        self.assertEqual(plan["waiting_rows"], 2)                                           # every row, again
        _, done = self.ingest()
        self.assertEqual(done["kb_summary"]["index"]["embedding_model"], "fake:bag-of-words@2")
        self.assertIsNotNone(registry.call("DocumentSearchTool", {"query": "zirconium hydride"})["results"][0]["score"])

    def test_labels_follow_the_knowledge_base(self):
        self.pdf("public")
        self.pdf("restricted", "sar.pdf")
        self.ingest("public")
        self.ingest("restricted", name="sar")
        self.assertEqual(registry.call("DocumentSearchTool", {"query": "zirconium", "kb": "docs"})["facility_data"], "public")
        self.assertEqual(registry.call("DocumentSearchTool", {"query": "zirconium"})["facility_data"], "restricted")
        read = registry.call("FileReadTool", {"path": f"{parse.DOCS}/sar/01_chunks_kb.jsonl"})
        self.assertEqual(read["facility_data"], "restricted")
        summary = registry.call("DocumentCheckTool", {"kb": "docs"})
        self.assertEqual(set(labels_in(summary)), {"public"})                               # the metadata row's own key is not one
        listed = registry.call("DocumentCheckTool", {})
        self.assertEqual(sorted(k["kb"] for k in listed["knowledge_bases"]), ["docs", "sar"])
        self.assertEqual(listed["facility_data"], "restricted")
        self.pdf("outside")
        with self.assertRaises(Refused) as ctx:
            parse.plan_parse(str(self.folder("outside")), name="sar")                       # a name that holds another folder
        self.assertEqual(ctx.exception.field, "name")

    def test_a_failure_and_a_partial_run(self):
        os.environ["FAKE_VP_FAIL"] = "1"
        self.pdf(name="a.pdf")
        _, done = self.ingest()
        self.assertEqual(done["status"], "failed")
        self.assertIn("boom", done["error"])
        del os.environ["FAKE_VP_FAIL"]
        plan = parse.plan_parse(str(self.folder()))
        self.assertEqual([f.get("why") for f in plan["to_parse"]], ["its last run ended failed"])
        self.pdf(name="b.pdf")
        os.environ["FAKE_VP_FAIL_FILE"] = "b.pdf"
        _, done = self.ingest()
        self.assertEqual(done["status"], "success", done)
        self.assertIn("not parsed: b.pdf", done["kb_summary"]["last_run"]["warnings"][0])
        self.assertEqual(self.vp_runs()[-1]["args"]["redo"], ["a.pdf"])                     # a.pdf's partial rows are cleared first
        del os.environ["FAKE_VP_FAIL_FILE"]
        self.assertEqual([f["name"] for f in parse.plan_parse(str(self.folder()))["to_parse"]], ["b.pdf"])

    def test_unusable_vision_answers_are_reported(self):
        os.environ["FAKE_VP_VISION_EMPTY"] = "1"
        self.pdf(pages=("one", "two"))
        _, done = self.ingest()
        warning = done["kb_summary"]["last_run"]["warnings"][0]
        self.assertIn("no usable answer for report.pdf p2", warning)
        self.assertIn("Could not parse JSON", warning)
        del os.environ["FAKE_VP_VISION_EMPTY"]
        self.pdf(name="b.pdf")
        _, done = self.ingest()
        self.assertIsNone(done["kb_summary"]["last_run"]["warnings"])                       # only this run's part of the log

    def test_cancel_and_one_run_at_a_time(self):
        os.environ["FAKE_VP_SLEEP"] = "30"
        self.pdf()
        job = parse.start(parse.plan_parse(str(self.folder())), approved_by="test")
        self.pdf("public")
        with self.assertRaises(Refused) as ctx:
            parse.start(parse.plan_parse(str(self.folder("public")), name="pub"), approved_by="test")
        self.assertIn("already running", str(ctx.exception))
        self.assertIn("another job is running", flat(parse.plan_parse(str(self.folder("public")), name="pub")["text"]))
        out = parse.cancel(job["job_id"])
        self.assertEqual(out["status"], "cancelled")
        self.assertEqual(parse.record("docs")["status"], "cancelled")
        self.assertFalse((parse.kb_dir("docs") / parse.STAGING).exists())
        self.assertEqual(jobs.list_jobs(jobs.home(self.ws)), [])                                       # never mixed with transport runs


@unittest.skipUnless(HAVE_PYMUPDF and importlib.util.find_spec("langgraph") is not None, "needs PyMuPDF and LangGraph")
class Agent(Base):
    def test_the_chat_asks_and_only_yes_starts_the_run(self):
        from langchain_core.messages import AIMessage, ToolMessage

        import automc_core
        from tests.agent.test_core import Scripted, call
        self.pdf()
        model = Scripted(replies=[call("DocumentParseTool", {"folder": str(self.folder())}), AIMessage(content="started")], seen=[])
        s = automc_core.Session(provider=LOCAL, model=model, audit=False)
        fake_job = {"job_id": "p1", "status": "running", "kb": "docs"}
        with mock.patch.object(automc_core.doc_parse, "start", return_value=fake_job) as start:
            evs = list(s.send("read the reports in this folder"))
            self.assertEqual((evs[-1][0], evs[-1][1]["kind"]), ("approval", "parse_approval"))
            self.assertIn("Make the knowledge base 'docs'", flat(evs[-1][1]["plan_text"]))
            start.assert_not_called()
            list(s.answer(True))
        start.assert_called_once()
        self.assertEqual(start.call_args.kwargs["approved_by"], "chat")
        result = json.loads([m for m in s.messages() if isinstance(m, ToolMessage)][-1].content)
        self.assertEqual((result["started"], result["job"]["job_id"]), (True, "p1"))


if __name__ == "__main__":
    unittest.main()
