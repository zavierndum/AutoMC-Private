"""
The workspace (WD-2, devops/AutoMC_Workspace_Plan.md sections 3, 4 and 8): one .automc/ folder for everything AutoMC keeps; settings.yaml
with folders, labels, links and policies, the three environment shortcuts and the order between them; open and facility modes; places
recorded as references that survive moving the workspace; linked workspaces read only; cases in subfolders; and `workspace upgrade`,
which moves an older workspace's hidden items in, rewrites what pointed at them, keeps every file, and can run twice.
"""
import contextlib
import importlib.util
import io
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import automc_paths
import automc_settings
import automc_workspace
from agent import guard
from tools import registry
from tools.files import places
from tools.files.places import Refused

HAVE_YAML = importlib.util.find_spec("yaml") is not None
ENVS = ("AUTOMC_PUBLIC_ROOTS", "AUTOMC_RESTRICTED_ROOTS", "AUTOMC_ROOTS", "AUTOMC_FILE_ROOTS")


@unittest.skipUnless(HAVE_YAML, "needs PyYAML")
class Base(unittest.TestCase):
    """A workspace, an outside folder and a user settings file of their own; no restricted pack unless a test says so."""
    pack = False

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="automc_ws_")).resolve()
        self.ws, self.outside = self.tmp / "ws", self.tmp / "outside"
        for d in (self.ws, self.outside / "public" / "sub", self.outside / "secret", self.outside / "other"):
            d.mkdir(parents=True)
        self.patches = [mock.patch.object(automc_paths, "WORKSPACE", self.ws), mock.patch.dict(os.environ),
                        mock.patch.object(automc_settings, "restricted_pack_installed", return_value=self.pack),
                        mock.patch.object(guard, "restricted_pack_installed", return_value=self.pack)]
        for p in self.patches:
            p.start()
        for env in ENVS:
            os.environ.pop(env, None)
        os.environ[automc_settings.USER_ENV] = str(self.tmp / "user-settings.yaml")
        automc_settings._CACHE.clear()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        automc_settings._CACHE.clear()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def settings(self, text, where=None):
        path = where or automc_settings.workspace_file(self.ws)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        automc_settings._CACHE.clear()


class Settings(Base):
    def test_folders_labels_links_and_policies_are_read_and_checked(self):
        self.settings(f"format: 1\nfolders:\n  - {{name: pub, path: {self.outside / 'public'}, label: public}}\n"
                      f"  - {{name: sec, path: {self.outside / 'secret'}, label: restricted}}\n"
                      f"  - {{name: rel, path: ../outside/other}}\n"
                      f"links:\n  - {{name: projA, path: {self.tmp / 'projA'}}}\npython: {{approval: auto}}\n")
        s = automc_settings.load()
        self.assertEqual([(f.name, f.label) for f in s["folders"]], [("pub", "public"), ("sec", "restricted"), ("rel", None)])
        self.assertEqual(s["folders"][2].path, self.outside / "other")                    # relative to the workspace
        self.assertEqual((s["links"][0].name, s["python"]["approval"], s["conversations"]["save_restricted"]), ("projA", "auto", False))
        for bad, why in (("colour: blue\n", "unknown settings"), ("folders:\n  - {name: a, path: /x, label: secret}\n", "label must be"),
                         ("folders:\n  - {name: a, path: /x}\n  - {name: a, path: /y}\n", "unique"),
                         ("python: {approval: always}\n", "python.approval"), ("format: 2\n", "format 2"),
                         ("mode: open\n", "mode must be auto or facility"),
                         ("folders:\n  - {name: a, path: /x}\nmode: facility\nfolders:\n  - {name: b, path: /y}\n", "given twice")):
            with self.subTest(bad=bad):
                self.settings(bad)
                with self.assertRaises(automc_settings.SettingsError) as ctx:
                    automc_settings.load()
                self.assertIn(why, str(ctx.exception))

    def test_the_environment_adds_folders_and_the_old_name_is_warned_about(self):
        os.environ["AUTOMC_PUBLIC_ROOTS"] = str(self.outside / "public")
        os.environ["AUTOMC_FILE_ROOTS"] = str(self.outside / "other")
        s = automc_settings.load()
        self.assertEqual([(f.label, f.source) for f in s["folders"]], [("public", "AUTOMC_PUBLIC_ROOTS"), (None, "AUTOMC_FILE_ROOTS")])
        self.assertIn("old name of AUTOMC_ROOTS", s["warnings"][0])
        # a public folder needs no second listing to be readable
        (self.outside / "public" / "note.txt").write_text("hello", encoding="utf-8")
        self.assertTrue(registry.call("FileReadTool", {"path": str(self.outside / "public" / "note.txt")})["ok"])

    def test_the_workspace_comes_before_the_user_and_config_show_says_where_from(self):
        self.settings("python: {approval: auto}\nconversations: {save_restricted: true}\n", where=automc_settings.user_file())
        self.settings("python: {approval: ask}\n")
        s = automc_settings.load()
        self.assertEqual((s["python"]["approval"], s["conversations"]["save_restricted"]), ("ask", True))
        rows = {k: (v, src) for k, v, src in automc_settings.effective()[0]}
        self.assertEqual(rows["python.approval"][1], str(automc_settings.workspace_file(self.ws)))
        self.assertEqual(rows["conversations.save_restricted"][1], str(automc_settings.user_file()))
        self.assertTrue(rows["mode"][0].startswith("open"))
        import automc
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(automc.main(["config", "show", "--json"]), 0)
        self.assertIn("python.approval", out.getvalue())

    def test_write_refuses_what_it_could_not_read_back(self):
        path = automc_settings.write({"folders": [{"name": "pub", "path": str(self.outside / "public"), "label": "public"}]})
        self.assertTrue(path.read_text(encoding="utf-8").startswith("# AutoMC workspace settings"))
        with self.assertRaises(automc_settings.SettingsError):
            automc_settings.write({"folders": [{"name": "x", "path": "/x", "label": "maybe"}]})
        self.assertEqual(automc_settings.load()["folders"][0].name, "pub")                 # the good file stays


class Modes(Base):
    def test_open_mode_labels_every_place_public_and_withholds_only_restricted(self):
        os.environ["AUTOMC_ROOTS"] = str(self.outside / "other")
        self.assertFalse(automc_settings.facility_mode())
        self.assertEqual(places.label(self.outside / "other")[0], "public")
        self.assertEqual(places.label(self.ws / "notes.md")[0], "public")
        self.assertTrue(guard.check({"facility_data": "unknown"}, cloud=True)[0])
        self.assertTrue(guard.check({"x": 1}, cloud=True)[0])                               # no label
        self.assertFalse(guard.check({"facility_data": "restricted"}, cloud=True)[0])        # an explicit label always counts

    def test_a_restricted_folder_or_mode_facility_turns_facility_mode_on(self):
        for text in (f"folders:\n  - {{name: sec, path: {self.outside / 'secret'}, label: restricted}}\n", "mode: facility\n"):
            with self.subTest(text=text):
                self.settings(text)
                self.assertTrue(automc_settings.facility_mode())
                self.assertIsNone(places.label(self.outside / "other")[0])
                self.assertFalse(guard.check({"facility_data": "unknown"}, cloud=True)[0])
                self.assertFalse(guard.check({"x": 1}, cloud=True)[0])
                if "secret" in text:
                    self.assertEqual(places.label(self.outside / "secret" / "a.pdf")[0], "restricted")
                    self.assertIn(self.outside / "secret", places.read_roots())

    def test_a_settings_file_that_cannot_be_read_counts_as_facility(self):
        self.settings("folders: [oops\n")
        self.assertTrue(automc_settings.facility_mode())


class References(Base):
    def test_places_are_recorded_so_that_moving_the_workspace_keeps_them(self):
        self.settings(f"folders:\n  - {{name: pub, path: {self.outside / 'public'}, label: public}}\n")
        self.assertEqual(places.to_ref(self.ws / "docs" / "a"), "ws:docs/a")
        self.assertEqual(places.to_ref(self.outside / "public" / "sub"), "folder:pub:sub")
        self.assertEqual(places.to_ref(self.outside / "other"), str(self.outside / "other"))          # undeclared: absolute
        self.assertEqual(places.from_ref("folder:pub:sub"), self.outside / "public" / "sub")
        moved = self.tmp / "moved-ws"
        shutil.copytree(self.ws, moved)
        with mock.patch.object(automc_paths, "WORKSPACE", moved):
            self.assertEqual(places.from_ref("ws:docs/a"), moved / "docs" / "a")
        self.settings("folders: []\n")
        with self.assertRaises(Refused):
            places.from_ref("folder:pub:sub")

    def test_a_knowledge_base_records_its_folder_as_a_reference(self):
        from tools.documents import parse
        self.settings(f"folders:\n  - {{name: pub, path: {self.outside / 'public'}, label: public}}\n")
        parse.kb_dir("kb1").mkdir(parents=True)
        rec = parse._write_record("kb1", kb="kb1", folder=str(self.outside / "public" / "sub"), label="public")
        self.assertEqual(rec["folder"], str(self.outside / "public" / "sub"))
        stored = json.loads((parse.kb_dir("kb1") / "kb.json").read_text(encoding="utf-8"))
        self.assertEqual(stored["folder"], "folder:pub:sub")
        parse._write_record("kb1", status="ready")                                         # another field: the reference stays
        self.assertEqual(json.loads((parse.kb_dir("kb1") / "kb.json").read_text(encoding="utf-8"))["folder"], "folder:pub:sub")
        self.assertEqual([r["folder"] for r in parse.kbs()], [str(self.outside / "public" / "sub")])
        self.assertEqual(parse.kb_dir("kb1"), self.ws / ".automc" / "documents" / "kb1")


class Links(Base):
    def test_a_linked_workspace_is_read_only_with_its_own_labels(self):
        other = self.tmp / "projA"
        case = other / "c8"
        case.mkdir(parents=True)
        (case / "result.json").write_text(json.dumps({"facility_data": {"distribution": "public"}}), encoding="utf-8")
        (case / "notes.txt").write_text("k-eff 1.002", encoding="utf-8")
        kb = other / ".automc" / "documents" / "sar"
        kb.mkdir(parents=True)
        (kb / "kb.json").write_text(json.dumps({"kb": "sar", "label": "restricted", "folder": "ws:docs"}), encoding="utf-8")
        self.settings(f"mode: facility\nlinks:\n  - {{name: projA, path: {other}}}\n")
        read = registry.call("FileReadTool", {"path": str(case / "notes.txt")})
        self.assertTrue(read["ok"], read)
        self.assertEqual(read["facility_data"], "public")                                   # the linked case's own label
        self.assertEqual(places.label(kb / "01_chunks_kb.jsonl")[0], "restricted")
        self.assertFalse(registry.call("FileWriteTool", {"path": str(other / "x.md"), "content": "no"})["ok"])
        from tools.documents import parse
        self.assertEqual([(n, r["label"]) for n, _, r in parse.linked_kbs()], [("projA:sar", "restricted")])


class NestedCases(Base):
    def test_a_case_may_sit_in_subfolders_and_its_job_goes_to_the_new_registry(self):
        from tests.openmc.test_jobs import make_case
        from tools.openmc import jobs, runs
        make_case(self.ws, "study-8/c8")
        self.assertEqual(runs.case_folder("study-8/c8"), self.ws / "study-8" / "c8")
        plan = runs.plan_run(case="study-8/c8", threads=1)
        self.assertEqual(plan["cases"][0]["run_folder"], "study-8/c8/run-001")
        for bad in ("a/b/c/d/e/f", "../x", ".automc/jobs"):
            with self.subTest(bad=bad), self.assertRaises(Exception):
                runs.case_folder(bad)
        j = jobs.create_job(jobs.home(self.ws), kind="run", case="study-8/c8", status="success")
        self.assertTrue((self.ws / ".automc" / "jobs" / "registry.json").is_file())
        self.assertEqual(runs.list_jobs()[0]["job_id"], j["job_id"])


class Upgrade(Base):
    pack = False

    def make_legacy(self):
        ws, docs_src = self.ws, self.outside / "public" / "sub"
        (docs_src / "a.pdf").write_bytes(b"%PDF-1.4")
        files = {".automc_chats/2026-10-31_abc.jsonl": '{"event": "session"}\n', ".automc_chats/.history": "hello\n",
                 ".automc_documents/sub/kb.json": json.dumps({"kb": "sub", "folder": str(docs_src), "label": "public"}),
                 ".automc_documents/sub/01_chunks_kb.jsonl": "{}\n", ".automc_documents/.automc_jobs.json": "[]",
                 ".automc_jobs.json": json.dumps([{"job_id": "j1", "kind": "sequence", "status": "success",
                                                   "job_folder": ".automc_jobs/j1", "facility_data": "restricted"}]),
                 ".automc_jobs/j1/sequence.json": "{}", ".automc_python/t1/runs/run-001.txt": "42\n",
                 ".automc_web/usage.json": "{}", ".automc_models/checks.json": "{}",
                 "c0/result.json": json.dumps({"facility_data": {"distribution": "restricted"}}),
                 "c0/run-001/statepoint.10.h5": "binary"}
        for rel, text in files.items():
            (ws / rel).parent.mkdir(parents=True, exist_ok=True)
            (ws / rel).write_text(text, encoding="utf-8")
        os.environ["AUTOMC_PUBLIC_ROOTS"] = str(self.outside / "public")
        return files

    def every_file(self):
        """Every file's name and bytes, but for the records the upgrade rewrites or writes."""
        return sorted((p.name, p.read_bytes()) for p in self.ws.rglob("*") if p.is_file()
                      and p.name not in ("format.json", "settings.yaml", "settings.lock", "registry.json", ".automc_jobs.json",
                                         "kb.json"))

    def test_an_old_workspace_is_refused_until_upgraded_and_the_dry_run_touches_nothing(self):
        self.make_legacy()
        with self.assertRaises(automc_workspace.LayoutError) as ctx:
            automc_workspace.check()
        self.assertIn("workspace upgrade --dry-run", str(ctx.exception))
        import automc
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(automc.main(["models", "list"]), 3)                           # a command that uses .automc/ refuses
        before = sorted(p.relative_to(self.ws).as_posix() for p in self.ws.rglob("*"))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(automc.main(["workspace", "upgrade", "--dry-run"]), 0)
        self.assertEqual(sorted(p.relative_to(self.ws).as_posix() for p in self.ws.rglob("*")), before)
        text = out.getvalue()
        for phrase in (".automc_python  ->  .automc/python", "mode: facility", "folder:public:sub", "nothing is deleted"):
            self.assertIn(phrase, text)

    def test_the_upgrade_moves_rewrites_keeps_every_file_and_can_run_twice(self):
        self.make_legacy()
        contents = self.every_file()
        out = automc_workspace.upgrade()
        state = self.ws / ".automc"
        self.assertEqual(automc_workspace.legacy_items(), [])
        for rel in ("chats/legacy/2026-10-31_abc.jsonl", "chats/.history", "documents/sub/01_chunks_kb.jsonl", "documents/registry.json",
                    "jobs/registry.json", "jobs/j1/sequence.json", "python/t1/runs/run-001.txt", "web/usage.json", "models/checks.json",
                    "format.json", "settings.yaml"):
            self.assertTrue((state / rel).exists(), rel)
        self.assertTrue((self.ws / "c0" / "run-001" / "statepoint.10.h5").is_file())       # run results are not touched
        self.assertEqual(self.every_file(), contents)                                         # nothing lost (records rewritten aside)
        reg = json.loads((state / "jobs" / "registry.json").read_text(encoding="utf-8"))
        self.assertEqual(reg[0]["job_folder"], ".automc/jobs/j1")
        settings = automc_settings.load()
        self.assertEqual(settings["mode"], "facility")                                      # it held restricted data
        self.assertEqual([(f.name, f.label) for f in settings["folders"] if f.source.endswith("settings.yaml")], [("public", "public")])
        kb = json.loads((state / "documents" / "sub" / "kb.json").read_text(encoding="utf-8"))
        self.assertEqual(kb["folder"], "folder:public:sub")
        from tools.documents import parse
        os.environ.pop("AUTOMC_PUBLIC_ROOTS")                                                # the settings now declare it
        automc_settings._CACHE.clear()
        self.assertEqual(parse.record("sub")["folder"], str(self.outside / "public" / "sub"))
        self.assertEqual(places.label(self.outside / "public" / "sub")[0], "public")
        again = automc_workspace.plan_upgrade()
        self.assertFalse(again["needed"], again)
        automc_workspace.check()

    def test_an_active_run_blocks_the_upgrade(self):
        self.make_legacy()
        (self.ws / ".automc_jobs.json").write_text(json.dumps([{"job_id": "j2", "status": "running"}]), encoding="utf-8")
        with self.assertRaises(automc_workspace.LayoutError) as ctx:
            automc_workspace.upgrade()
        self.assertIn("run(s) active", str(ctx.exception))
        self.assertTrue((self.ws / ".automc_jobs.json").is_file())

    def test_a_new_workspace_gets_its_format_file_and_its_usage(self):
        automc_workspace.ensure_format()
        fmt = json.loads((self.ws / ".automc" / "format.json").read_text(encoding="utf-8"))
        self.assertEqual(fmt["format"], automc_workspace.FORMAT)
        (self.ws / "c1").mkdir()
        (self.ws / "c1" / "result.json").write_text("{}", encoding="utf-8")
        rows = {w: n for w, n, _ in automc_workspace.usage()}
        self.assertGreater(rows["cases and their runs"], 0)


class WorkspaceFlag(unittest.TestCase):
    def test_workspace_anywhere_on_the_command_line(self):
        tmp = Path(tempfile.mkdtemp(prefix="automc_flag_")).resolve()
        try:
            with mock.patch.object(automc_paths, "WORKSPACE", automc_paths.WORKSPACE), mock.patch.dict(os.environ):
                import automc
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    self.assertEqual(automc.main(["config", "show", "--json", "--workspace", str(tmp)]), 0)
                rows = {r["setting"]: r["value"] for r in json.loads(out.getvalue())["settings"]}
                self.assertEqual(rows["workspace"], str(tmp))
                self.assertEqual(os.environ["AUTOMC_WORKSPACE"], str(tmp))                 # for the processes it starts
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
