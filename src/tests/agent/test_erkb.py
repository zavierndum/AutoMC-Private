"""The error knowledge base loads strictly, is matched against failed tool results only, reaches the model as known_errors through the
tool registry, and is no longer carried whole in the system prompt. No OpenMC needed; tests/skills/test_erkb_messages.py checks the
hints against the messages OpenMC really writes."""
import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HAVE_YAML = importlib.util.find_spec("yaml") is not None
ENTRY = ('- detection_hint: "{hint}"\n  signature: "s"\n  background: "b"\n  suggestion: "do this"\n  confidence: {conf}\n'
         '  origin: seen\n')


@unittest.skipUnless(HAVE_YAML, "needs PyYAML")
class Entries(unittest.TestCase):
    def write(self, text):
        p = Path(tempfile.mkdtemp()) / "ERKB.yaml"
        p.write_text(text, encoding="utf-8")
        return p

    def test_the_knowledge_base_loads(self):
        from agent import erkb
        found = erkb.entries()
        self.assertGreaterEqual(len(found), 23)
        self.assertEqual(len({e["detection_hint"] for e in found}), len(found))

    def test_a_broken_file_is_refused(self):
        from agent import erkb
        self.assertEqual(len(erkb.entries(self.write(ENTRY.format(hint="x", conf="high")))), 1)
        for bad in (ENTRY.format(hint="x", conf="certain"),                                   # unknown confidence
                    ENTRY.format(hint="x", conf="high") + ENTRY.format(hint="x", conf="low"),  # a hint twice
                    ENTRY.format(hint="x", conf="high").replace("  origin: seen\n", ""),      # a key missing
                    ENTRY.format(hint="x", conf="high") + "  colour: blue\n",                 # a key too many
                    ENTRY.format(hint="", conf="high")):                                      # an empty hint
            with self.subTest(bad=bad[-40:]):
                with self.assertRaises(erkb.ERKBError):
                    erkb.entries(self.write(bad))


@unittest.skipUnless(HAVE_YAML, "needs PyYAML")
class Matching(unittest.TestCase):
    def test_failed_results_get_their_known_errors_and_others_do_not(self):
        from agent import erkb
        line = "ERROR: Cannot mix atom and weight percents in material 1"
        failures = [{"ok": False, "error": {"message": line}}, {"status": "failed", "error": line},
                    {"status": "interrupted", "log_tail": ["...", line]}, {"built": False, "error": "x", "log_tail": line},
                    {"passed": False, "findings": [{"message": line}]}, {"status": "error", "output": "Traceback\n" + line},
                    {"ok": True, "job": {"status": "failed", "log_tail": [line]}}]                # OpenMCRunCheckTool's form
        for result in failures:
            with self.subTest(result=list(result)):
                out = erkb.annotate(dict(result))
                self.assertEqual([k["matched"] for k in out["known_errors"]], ["Cannot mix atom and weight percents"])
                self.assertTrue(out["known_errors"][0]["suggestion"])
        for result in ({"ok": True, "text": line}, {"status": "success", "log": line}, {"passed": True, "note": line}, "text", None,
                       {"ok": True, "job": {"status": "running", "note": line}}):
            with self.subTest(result=result):
                out = erkb.annotate(result if not isinstance(result, dict) else dict(result))
                self.assertFalse(isinstance(out, dict) and "known_errors" in out)
        self.assertNotIn("known_errors", erkb.annotate({"ok": False, "error": "an error nobody has met"}))

    def test_several_entries_match_in_the_files_order(self):
        from agent import erkb
        text = "ERROR: Overlapping cells detected: 2, 3\nAUTOMC_JOB_DONE rc=1 status=failed segment=0"
        matched = [k["matched"] for k in erkb.annotate({"status": "failed", "log_tail": text.splitlines()})["known_errors"]]
        self.assertEqual(matched, ["Overlapping cells detected", "status=failed"])

    def test_the_registry_attaches_them_to_any_tool(self):
        from tools import registry
        fake = {"name": "FakeTool", "handler": lambda args: {"ok": False, "error": "Too few source sites satisfied the constraints"}}
        with mock.patch.dict(registry._BY_NAME, {"FakeTool": fake}):
            out = registry.call("FakeTool", {})
        self.assertEqual(out["known_errors"][0]["matched"], "Too few source sites satisfied the constraints")
        with mock.patch.dict(registry._BY_NAME, {"FakeTool": {**fake, "handler": lambda a: {"ok": True}}}):
            self.assertEqual(registry.call("FakeTool", {}), {"ok": True})

    def test_a_broken_knowledge_base_never_breaks_a_tool(self):
        from agent import erkb
        from tools import registry
        fake = {"name": "FakeTool", "handler": lambda args: {"ok": False, "error": "boom"}}
        with mock.patch.dict(registry._BY_NAME, {"FakeTool": fake}), \
                mock.patch.object(erkb, "entries", side_effect=erkb.ERKBError("broken")):
            self.assertEqual(registry.call("FakeTool", {}), {"ok": False, "error": "boom"})

    def test_automc_s_own_hints_are_what_its_code_writes(self):
        """The entries whose message AutoMC itself writes: each hint must still be produced by the code named."""
        import automc_paths
        from tools.openmc import jobs
        self.assertTrue(jobs._SENTINEL_RE.search("AUTOMC_JOB_DONE rc=1 status=failed segment=0"))
        for hint, rel in (("is not built: missing", "tools/reactors/triga/libraries.py"),
                          ("No restricted facility pack is installed", "tools/reactors/triga/presets.py"),
                          ("bins in total, above the limit", "tools/reactors/triga/tallies.py")):
            with self.subTest(hint=hint):
                self.assertIn(hint, (automc_paths.SRC / rel).read_text(encoding="utf-8"))


@unittest.skipUnless(HAVE_YAML, "needs PyYAML")
class Prompt(unittest.TestCase):
    def test_the_prompt_points_to_known_errors_instead_of_carrying_the_file(self):
        try:
            from agent import prompt
            from agent.llm import Provider
        except ImportError:
            self.skipTest("needs the agent's dependencies")
        text, _ = prompt.system_prompt(Provider(name="t", kind="local", account="self_hosted", backend="ollama", base_url="x", model="m"))
        self.assertNotIn("detection_hint", text)
        self.assertNotIn("## Error knowledge base", text)
        self.assertIn("known_errors", text)
        self.assertIn("ERKB.yaml", text)                                   # where to read the whole file
