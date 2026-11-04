"""The evaluation cases load strictly, and each scoring rule passes and fails when it should. No model needed."""
import tempfile
import unittest
from pathlib import Path

import importlib.util

HAVE_YAML = importlib.util.find_spec("yaml") is not None


@unittest.skipUnless(HAVE_YAML, "needs PyYAML")
class Cases(unittest.TestCase):
    def test_the_case_file_loads_and_names_real_tools(self):
        from agent import evals
        version, cases = evals.load_cases()
        self.assertGreaterEqual(len(cases), 15)
        self.assertEqual(len({c["id"] for c in cases}), len(cases))

    def write(self, text):
        p = Path(tempfile.mkdtemp()) / "cases.yml"
        p.write_text(text, encoding="utf-8")
        return p

    def test_approvals_are_answered_in_order_the_last_repeating(self):
        from agent import evals
        case = evals.load_cases(self.write("cases:\n  - {id: a, title: t, turns: [x], approve: [yes, no]}\n"))[1][0]
        self.assertEqual([evals._approval_answer(case, n) for n in range(3)], [True, False, False])
        self.assertEqual([evals._approval_answer({"approve": "yes"}, n) for n in range(2)], [True, True])
        self.assertFalse(evals._approval_answer({}, 0))
        with self.assertRaises(evals.CaseError):
            evals.load_cases(self.write("cases:\n  - {id: a, title: t, turns: [x], approve: [yes, maybe]}\n"))

    def test_the_loader_refuses_typos(self):
        from agent import evals
        self.assertEqual(len(evals.load_cases(self.write("cases:\n  - {id: a, title: t, turns: [x]}\n"))[1]), 1)
        for bad in ("cases:\n  - {id: a, title: t, turns: [x], expect: {tools_requird: [OpenMCJobListTool]}}\n",
                    "cases:\n  - {id: a, title: t, turns: [x], expect: {tools_required: [NoSuchTool]}}\n",
                    "cases:\n  - {id: a, title: t, turns: [x], colour: blue}\n",
                    "cases:\n  - {id: a, title: t, turns: [x], approve: maybe}\n",
                    "cases:\n  - {id: a, title: t, turns: [x]}\n  - {id: a, title: u, turns: [y]}\n",
                    "version: 1\ncases:\n  - {id: a, title: t, turns: [x]}\n"):            # the package's version names cases now
            with self.subTest(bad=bad[:60]):
                with self.assertRaises(evals.CaseError):
                    evals.load_cases(self.write(bad))


@unittest.skipUnless(HAVE_YAML, "needs PyYAML")
class PackAnswers(unittest.TestCase):
    """cases.yml is publishable: the facility values of the cases that need the restricted pack live in the pack (2026-11-03, when
    reference-bias was found holding the reference model's bias before the first commit)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="automc_eval_pack_"))

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write(self, text):
        p = self.tmp / "cases.yml"
        p.write_text(text, encoding="utf-8")
        return p

    def pack(self, answers=None):
        """A small pack, providing eval_answers when given (JSON is valid YAML)."""
        import json
        from tools.reactors.triga import packs
        folder = self.tmp / "demo"
        folder.mkdir()
        meta = {"format": 1, "pack": "demo", "facility": "netl", "title": "t", "distribution": "public",
                "sources": {"own": {"what": "made up for a test", "distribution": "public"}}, "items": {}}
        if answers is not None:
            (folder / "evals").mkdir()
            (folder / "evals" / "answers.yaml").write_text(json.dumps(answers), encoding="utf-8")
            meta["items"] = {"evals/answers.yaml": {"what": "answers", "sources": ["own"], "distribution": "public"}}
            meta["provides"] = {"eval_answers": "evals/answers.yaml"}
        (folder / "pack.yaml").write_text(json.dumps(meta), encoding="utf-8")
        return packs.load_pack(folder, "public")

    CASE = "cases:\n  - {id: a, title: t, turns: [x], needs: restricted_pack, expect: {answer_from_pack: true, answer_all: [[generic]]}}\n"

    def test_the_case_file_takes_facility_answers_from_the_pack(self):
        from agent import evals
        _, cases = evals.load_cases()
        from_pack = {c["id"] for c in cases if (c.get("expect") or {}).get("answer_from_pack")}
        self.assertLessEqual({"reference-bias", "config-8"}, from_pack)

    def test_the_loader_keeps_facility_values_out_of_the_case_file(self):
        from agent import evals
        self.assertEqual(len(evals.load_cases(self.write(self.CASE))[1]), 1)
        self.assertEqual(len(evals.load_cases(self.write(              # a public case may hold numbers
            "cases:\n  - {id: a, title: t, turns: [x], expect: {answer_all: [['1.00283']]}}\n"))[1]), 1)
        for bad in ("cases:\n  - {id: a, title: t, turns: [x], needs: restricted_pack, expect: {answer_all: [['1.2345']]}}\n",
                    "cases:\n  - {id: a, title: t, turns: [x], needs: restricted_pack, expect: {answer_none: ['999 pcm']}}\n",
                    "cases:\n  - {id: a, title: t, turns: [x], expect: {answer_from_pack: true}}\n",
                    "cases:\n  - {id: a, title: t, turns: [x], needs: restricted_pack, expect: {answer_from_pack: yes please}}\n"):
            with self.subTest(bad=bad[40:110]):
                with self.assertRaises(evals.CaseError):
                    evals.load_cases(self.write(bad))

    def test_the_pack_phrases_are_added_to_the_case_and_scored(self):
        from agent import evals
        _, cases = evals.load_cases(self.write(self.CASE))
        answers = evals.pack_answers(self.pack({"a": {"answer_all": [["1.2345"]], "answer_none": ["unknown"]}}), cases)
        case = evals.with_pack_answers(cases[0], answers)
        self.assertEqual(case["expect"]["answer_all"], [["generic"], ["1.2345"]])
        self.assertEqual(cases[0]["expect"]["answer_all"], [["generic"]])                 # the loaded case is not changed
        rec = {"tools": [], "approvals": 0, "started": False, "error": None}
        self.assertTrue(all(ok for _, ok, _ in evals.score(case, dict(rec, final="generic: k 1.2345"), {})))
        self.assertFalse(all(ok for _, ok, _ in evals.score(case, dict(rec, final="generic, unknown"), {})))

    def test_without_the_pack_or_its_file_there_are_no_answers(self):
        from agent import evals
        _, cases = evals.load_cases(self.write(self.CASE))
        self.assertEqual(evals.pack_answers(None, cases), {})
        self.assertEqual(evals.pack_answers(self.pack(), cases), {})
        self.assertIs(evals.with_pack_answers(cases[0], {}), cases[0])

    def test_broken_pack_answers_are_refused(self):
        from agent import evals
        _, cases = evals.load_cases(self.write(self.CASE))
        import shutil
        for bad in ({"b": {"answer_all": [["x"]]}}, {"a": {"answer_some": [["x"]]}}, {"a": {"answer_all": ["x"]}}, {"a": {}}, ["a"]):
            with self.subTest(bad=bad):
                shutil.rmtree(self.tmp / "demo", ignore_errors=True)
                with self.assertRaises(evals.CaseError):
                    evals.pack_answers(self.pack(bad), cases)

    def test_the_report_names_the_pack_file_never_its_phrases(self):
        from agent import evals
        _, cases = evals.load_cases(self.write(self.CASE))
        pack = self.pack({"a": {"answer_all": [["1.2345"]]}})
        note = evals._pack_note(pack, evals.pack_answers(pack, cases))
        self.assertIn("1 case(s) from the demo pack", note)
        self.assertNotIn("1.2345", note)
        self.assertIsNone(evals._pack_note(None, {}))

    def test_the_installed_restricted_pack_answers_every_case_that_asks(self):
        from agent import evals
        pack = evals._restricted_pack()
        if pack is None:
            self.skipTest("needs the restricted NETL facility pack")
        _, cases = evals.load_cases()
        answers = evals.pack_answers(pack, cases)
        self.assertEqual({c["id"] for c in cases if (c.get("expect") or {}).get("answer_from_pack")}, set(answers))


@unittest.skipUnless(HAVE_YAML, "needs PyYAML")
class Scoring(unittest.TestCase):
    def rules(self, expect, **record):
        from agent import evals
        rec = {"tools": [], "approvals": 0, "final": "", "started": False, "error": None, **record}
        return {r: ok for r, ok, _ in evals.score({"expect": expect}, rec, {"sphere_keff_2": "1.04"})}

    def test_tools(self):
        r = self.rules({"tools_required": ["A"], "tools_any": ["B", "C"], "tools_forbidden": ["D"]}, tools=["A", "C"])
        self.assertTrue(all(r.values()))
        r = self.rules({"tools_required": ["A"], "tools_forbidden": ["D"]}, tools=["D"])
        self.assertEqual(sorted(r.values()), [False, False])

    def test_approval_and_start(self):
        self.assertTrue(all(self.rules({"approval_asked": True, "run_started": False}, approvals=1).values()))
        self.assertFalse(all(self.rules({"approval_asked": False}, approvals=1).values()))

    def test_answers_and_placeholders(self):
        r = self.rules({"answer_all": [["k-eff"], ["{sphere_keff_2}"]], "answer_none": ["started"]}, final="K-eff 1.0412 +/- 0.004")
        self.assertTrue(all(r.values()))
        self.assertFalse(all(self.rules({"answer_none": ["started"]}, final="The run has started").values()))

    def test_plan_kinds_are_the_plans_put_to_the_user(self):
        """A search is a script run by what was planned, not by whether the answer says so (2026-11-02: it planned one and never wrote
        the word)."""
        self.assertTrue(all(self.rules({"plan_kinds": ["model_build", "script"]}, plan_kinds=["model_build", "script"]).values()))
        self.assertFalse(all(self.rules({"plan_kinds": ["script"]}, plan_kinds=["model_build", "run"]).values()))
        self.assertFalse(all(self.rules({"plan_kinds": ["script"]}).values()))

    def test_typographic_quotes_match_plain_ones(self):
        """GPT-5.4 wrote "I can’t" in three answers of the 2026-11-02 cloud evaluation, which failed a rule written "can't"."""
        self.assertTrue(all(self.rules({"answer_all": [["can't"]]}, final="I can’t help with that.").values()))
        self.assertFalse(all(self.rules({"answer_none": ["won't"]}, final="I won’t.").values()))

    def test_keff_with_error(self):
        for text, ok in (("k-eff = 1.0123 +/- 0.0004", True), ("k = 0.998 ± 0.002", True), ("k-eff is about 1.01", False)):
            with self.subTest(text=text):
                self.assertEqual(all(self.rules({"keff_with_error": True}, final=text).values()), ok)

    def test_an_exception_fails_the_case(self):
        self.assertFalse(all(self.rules({}, error="RuntimeError: boom").values()))


@unittest.skipUnless(HAVE_YAML, "needs PyYAML")
class CloudRuns(unittest.TestCase):
    """A cloud model is evaluated with nothing restricted reachable, a case's token cap raised, and the report saying both."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="automc_eval_cloud_"))

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def cloud(self):
        from agent.llm import Provider
        return Provider(name="test-cloud", kind="cloud", account="personal", backend="openai_compatible", base_url="x", model="m")

    def test_the_child_of_a_cloud_run_reaches_no_restricted_pack_folder_or_setting(self):
        import os
        import subprocess
        import sys
        import automc_settings
        from agent import evals
        base = {"PATH": os.environ.get("PATH", ""), "AUTOMC_RESTRICTED_ROOTS": str(self.tmp / "secret"), "AUTOMC_ROOTS": "x",
                "AUTOMC_FILE_ROOTS": "y", automc_settings.USER_ENV: str(self.tmp / "settings.yaml")}
        (self.tmp / "secret").mkdir()
        local = evals.child_env(base, self.tmp / "ws", self.tmp, cloud=False)
        self.assertEqual(local["AUTOMC_RESTRICTED_ROOTS"], str(self.tmp / "secret"))           # a local model keeps them
        env = evals.child_env(base, self.tmp / "ws", self.tmp, cloud=True)
        for name in ("AUTOMC_RESTRICTED_ROOTS", "AUTOMC_ROOTS", "AUTOMC_FILE_ROOTS"):
            self.assertNotIn(name, env)
        self.assertEqual(os.listdir(env["AUTOMC_FACILITY_PACKS"]), [])
        self.assertFalse(Path(env[automc_settings.USER_ENV]).exists())
        self.assertEqual(env["AUTOMC_WORKSPACE"], str(self.tmp / "ws"))
        (self.tmp / "ws").mkdir()
        probe = ("import automc_settings; from tests.reactors.triga._packs import restricted; "
                 "print(automc_settings.facility_mode(), restricted() is None)")
        out = subprocess.run([sys.executable, "-c", probe], env=dict(os.environ, **env), capture_output=True, text=True, check=True)
        self.assertEqual(out.stdout.split()[-2:], ["False", "True"])          # even where the restricted pack is installed

    def test_the_child_keeps_the_keys_file_a_cloud_model_needs(self):
        """What the child imports before it resolves the model must not drop AUTOMC_ENV_FILE (importing tests/ does)."""
        import os
        import subprocess
        import sys
        import automc_paths
        from agent import evals
        keys = self.tmp / "keys.env"
        keys.write_text("PORTKEY_API_KEY=not-a-real-key-for-this-test\n", encoding="utf-8")
        env = evals.child_env(dict(os.environ, AUTOMC_ENV_FILE=str(keys)), self.tmp / "ws", self.tmp, cloud=True)
        probe = ("import os; from agent import evals, keys; evals.load_cases(); evals._restricted_pack(); "
                 "print(os.environ.get('AUTOMC_ENV_FILE') == r'%s', bool(keys.value('PORTKEY_API_KEY')))" % keys)
        out = subprocess.run([sys.executable, "-c", probe], env=env, cwd=str(automc_paths.SRC), capture_output=True, text=True, check=True)
        self.assertEqual(out.stdout.split()[-2:], ["True", "True"], out.stderr[-500:])

    def test_the_eval_workspace_gets_the_check_of_its_model_only(self):
        """A cloud model needs a passed check, kept per workspace; the eval's new workspace gets that one record from the real one."""
        import json
        from unittest import mock
        import automc_paths
        from agent import discovery, evals
        real, ev = self.tmp / "real", self.tmp / "eval-ws"
        with mock.patch.object(automc_paths, "WORKSPACE", real):
            discovery.record_check("openai-portkey/@x/gpt-5.4", {"ok": True}, tools=True)
            discovery.record_check("openai-portkey/@x/gpt-4o", {"ok": True}, tools=True)
            evals._carry_check("openai-portkey/@x/gpt-5.4", ev)
            evals._carry_check("never-checked", self.tmp / "other-ws")
        carried = json.loads((ev / ".automc" / "models" / "checks.json").read_text(encoding="utf-8"))
        self.assertEqual(list(carried), ["openai-portkey/@x/gpt-5.4"])
        self.assertTrue(carried["openai-portkey/@x/gpt-5.4"]["tools"])
        self.assertFalse((self.tmp / "other-ws").exists())
        with mock.patch.object(automc_paths, "WORKSPACE", ev):
            self.assertTrue(discovery.passed("openai-portkey/@x/gpt-5.4"))

    def test_a_cloud_child_refuses_when_something_restricted_is_still_declared(self):
        from unittest import mock
        import automc_settings
        from agent import discovery, evals
        with mock.patch.object(discovery, "resolve", return_value=self.cloud()), \
                mock.patch.object(automc_settings, "facility_mode", return_value=True), \
                mock.patch.object(evals, "fixtures", side_effect=AssertionError("no fixture may be made")):
            self.assertEqual(evals.child(self.tmp, "test-cloud", set()), 3)

    def test_the_report_of_a_cloud_run_says_what_was_hidden_and_the_cap(self):
        import dataclasses
        from agent import evals
        p = dataclasses.replace(self.cloud(), max_tokens_per_session=evals.CLOUD_CASE_CAP)
        evals.report(self.tmp, [{"id": "config-8", "title": "t", "skipped": "needs the restricted pack"}], p, "AutoMC test", {"skills": []})
        text = (self.tmp / "report.md").read_text(encoding="utf-8")
        self.assertIn("nothing restricted was reachable", text)
        self.assertIn("up to 400,000 tokens", text)
        self.assertIn("| config-8 | skipped |", text)
