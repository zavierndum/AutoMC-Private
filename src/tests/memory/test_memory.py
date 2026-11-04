"""
The workspace's memory (WD-4, devops/AutoMC_Workspace_Plan.md section 6): AUTOMC.md and notes in .automc/memory/ with an index; a note
the agent proposes is saved only after the user's yes, with the conversation's label (never the model's), and a note of the same title
replaces the old one without lowering its label; the system prompt holds AUTOMC.md and the index, a cloud model in facility mode only
what is public; the size is capped; /remember and /memory work from the chat; MemoryReadTool carries a note's label.
"""
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
from tools import conversation, registry
from tools.memory import store

HAVE_LANGGRAPH = importlib.util.find_spec("langgraph") is not None


class Base(unittest.TestCase):
    facility = True

    def setUp(self):
        from agent import guard
        self.ws = Path(tempfile.mkdtemp(prefix="automc_memory_")).resolve()
        self.patches = [mock.patch.object(automc_paths, "WORKSPACE", self.ws), mock.patch.dict(os.environ),
                        mock.patch.object(automc_settings, "restricted_pack_installed", return_value=self.facility),
                        mock.patch.object(guard, "restricted_pack_installed", return_value=self.facility)]
        for p in self.patches:
            p.start()
        os.environ[automc_settings.USER_ENV] = str(self.ws / "no-user-settings.yaml")
        automc_settings._CACHE.clear()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        automc_settings._CACHE.clear()
        shutil.rmtree(self.ws, ignore_errors=True)


class Notes(Base):
    def test_notes_and_their_index(self):
        a = store.save("Threads", "Always run with 16 threads.", "public")
        b = store.save("C8 shim position", "Shim 1 sits at 23.8 cm in the C8 study.", "restricted")
        self.assertEqual((a["name"], b["name"]), ("threads", "c8-shim-position"))
        index = (store.folder() / store.INDEX).read_text(encoding="utf-8")
        self.assertIn("[Threads](threads.md) (public): Always run with 16 threads.", index)
        self.assertIn("(restricted)", index)
        again = store.save("C8 shim position", "Shim 1 sits at 24.0 cm now.", "public")          # same title: replaced, not lowered
        self.assertTrue(again["replaced"])
        self.assertEqual(again["label"], "restricted")
        self.assertEqual(len(store.notes()), 2)
        self.assertEqual(store.set_label("c8-shim-position", "public")["label"], "public")          # only the user lowers it
        self.assertTrue(store.forget("threads"))
        self.assertEqual([n["name"] for n in store.notes()], ["c8-shim-position"])
        with self.assertRaises(store.MemoryError_):
            store.save("Too long", "x" * (store.MAX_NOTE + 1), "public")

    def test_the_read_tool_carries_the_label(self):
        store.save("C8 shim position", "Shim 1 sits at 23.8 cm.", "restricted")
        out = registry.call("MemoryReadTool", {"name": "c8-shim-position"})
        self.assertEqual((out["ok"], out["facility_data"]), (True, "restricted"))
        from agent import guard
        self.assertFalse(guard.check(out, cloud=True)[0])
        self.assertFalse(registry.call("MemoryReadTool", {"name": "nothing"})["ok"])


class Prompt(Base):
    def provider(self, cloud):
        from agent.llm import Provider
        return (Provider(name="c", kind="cloud", account="personal", backend="openai_compatible", base_url="x", model="m") if cloud
                else Provider(name="l", kind="local", backend="ollama", base_url="x", model="m"))

    def test_what_each_model_sees(self):
        (self.ws / store.AUTOMC_MD).write_text("This project studies configuration 8.\nUse 16 threads.\n", encoding="utf-8")
        store.save("Threads", "Always 16 threads.", "public")
        store.save("Shim", "Shim 1 at 23.8 cm.", "restricted")
        local, _ = store.prompt_block(cloud=False, facility=True)
        self.assertIn("This project studies configuration 8.", local)
        self.assertIn("shim (restricted)", local)
        cloud, info = store.prompt_block(cloud=True, facility=True)
        self.assertNotIn("configuration 8", cloud)                                             # AUTOMC.md not marked public
        self.assertIn("not marked public", cloud)
        self.assertIn("threads (public)", cloud)
        self.assertNotIn("23.8", cloud)
        self.assertIn("1 more note(s) not given to a cloud model", cloud)
        self.assertEqual(info["notes_left_out"], 1)
        (self.ws / store.AUTOMC_MD).write_text("label: public\nThis project studies configuration 8.\n", encoding="utf-8")
        cloud, _ = store.prompt_block(cloud=True, facility=True)
        self.assertIn("This project studies configuration 8.", cloud)
        self.assertNotIn("label: public", cloud)
        opened, _ = store.prompt_block(cloud=True, facility=False)                             # open mode: everything is public
        self.assertIn("Shim 1 at 23.8 cm.", opened)

    def test_the_size_is_capped_and_the_system_prompt_holds_it(self):
        (self.ws / store.AUTOMC_MD).write_text("label: public\n" + "rule. " * 2000, encoding="utf-8")
        text, info = store.prompt_block(cloud=False, facility=True)
        self.assertLess(len(text), store.MAX_AUTOMC_MD + 400)
        self.assertIn("left out", info["cut"][0])
        try:
            import yaml  # noqa: F401
        except ImportError:
            self.skipTest("needs PyYAML")
        from agent import prompt
        system, pinfo = prompt.system_prompt(self.provider(cloud=False))
        self.assertIn("# This workspace\n\n## Project instructions (AUTOMC.md", system)
        self.assertEqual(pinfo["memory"]["automc_md"], "public")


@unittest.skipUnless(HAVE_LANGGRAPH, "needs LangGraph")
class Approval(Base):
    def test_a_note_the_agent_proposes_is_saved_only_after_yes_with_the_conversations_label(self):
        from langchain_core.messages import AIMessage, ToolMessage

        import automc_core
        from tests.agent.test_core import LOCAL, Scripted, call
        for yes in (False, True):
            with self.subTest(yes=yes):
                model = Scripted(replies=[call("MemoryWriteTool", {"title": "Threads", "text": "Always run with 16 threads."}),
                                          AIMessage(content="ok")], seen=[])
                s = automc_core.Session(provider=LOCAL, model=model, audit=False)
                evs = list(s.send("remember that I always want 16 threads"))
                self.assertEqual((evs[-1][0], evs[-1][1]["kind"]), ("approval", "memory_approval"))
                self.assertIn("Always run with 16 threads.", evs[-1][1]["plan_text"])
                list(s.answer(yes))
                result = json.loads([m for m in s.messages() if isinstance(m, ToolMessage)][-1].content)
                if yes:
                    self.assertEqual((result["saved"], result["label"]), ("threads", "public"))
                    self.assertEqual(store.get("threads")["text"], "Always run with 16 threads.")
                else:
                    self.assertTrue(result["declined"])
                    self.assertIsNone(store.get("threads"))

    def test_the_label_is_the_conversations_never_the_models(self):
        from tools.memory import tools as memory_tools
        with conversation.inside(None, "restricted", "t1", None):
            plan = memory_tools.plan_note("Shim", "Shim 1 at 23.8 cm.")
            self.assertEqual(plan["label"], "restricted")
            self.assertNotIn("label", registry.describe()[[t["name"] for t in registry.describe()].index("MemoryWriteTool")]
                             ["parameters"]["properties"])
            out = memory_tools.start(plan, approved_by="test")
        self.assertEqual(out["label"], "restricted")
        with conversation.inside(None, "public", "t2", None):
            with self.assertRaises(Exception):
                memory_tools.start({**plan, "note": "changed"}, approved_by="test")


@unittest.skipUnless(HAVE_LANGGRAPH, "needs LangGraph")
class Commands(Base):
    def test_remember_and_memory_from_the_chat(self):
        from agent.chat import ChatUI
        from langchain_core.messages import AIMessage

        import automc_core
        from tests.agent.test_core import LOCAL, Scripted
        s = automc_core.Session(provider=LOCAL, model=Scripted(replies=[AIMessage(content="x")], seen=[]), audit=False)
        out = io.StringIO()
        answers = ["y", "y"]
        ui = ChatUI(s, ask=lambda q: answers.pop(0) if answers else "", out=out)
        ui.command("/remember Always plot k-eff with error bars. They help.")
        ui.command("/remember --public Use ENDF/B-VIII.0.")
        notes = {n["name"]: n for n in store.notes()}
        self.assertEqual(notes["always-plot-k-eff-with-error-bars"]["label"], "public")      # a public conversation's label
        self.assertEqual(notes["use-endf-b-viii-0"]["label"], "public")
        ui.command("/memory")
        self.assertIn("always-plot-k-eff-with-error-bars", out.getvalue())
        ui.command("/memory forget use-endf-b-viii-0")
        self.assertNotIn("use-endf-b-viii-0", {n["name"] for n in store.notes()})


if __name__ == "__main__":
    unittest.main()
