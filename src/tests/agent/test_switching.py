"""
Switching models during a conversation (AutoMC_Agent_Models_Plan.md, MS-2): the privacy rule, the two-key policy, the labels that
results put into a conversation, the facility-pack skills read on demand, and the context guard. Scripted models; no network.
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

HAVE_LANGGRAPH = importlib.util.find_spec("langgraph") is not None

if HAVE_LANGGRAPH:
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

    import automc_core
    from agent import context, guard, llm
    from agent.chat import ChatUI
    from agent.llm import Provider
    from tests.agent.test_core import Scripted, call

import automc_paths

from tests.reactors.triga._packs import PUBLIC_PRESET, canaries, restricted  # noqa: E402


def provider(name, kind, account, window=None):
    return Provider(name=name, kind=kind, account=account, backend="ollama" if kind == "local" else "openai_compatible",
                    base_url="x", model="scripted", num_ctx=window)


@unittest.skipUnless(HAVE_LANGGRAPH, "needs LangGraph (the WSL automc environment)")
class Switching(unittest.TestCase):
    def setUp(self):
        self.ws = Path(tempfile.mkdtemp(prefix="automc_switch_"))
        self.p = mock.patch.object(automc_paths, "WORKSPACE", self.ws)
        self.p.start()
        self.local = provider("local", "local", "self_hosted")
        self.cloud = provider("gemini", "cloud", "personal")
        self.enterprise = provider("openai-portkey", "cloud", "enterprise")
        self.providers = {p.name: p for p in (self.local, self.cloud, self.enterprise)}
        self.q = mock.patch.object(llm, "provider", side_effect=lambda name=None, **kw: self.providers[name])
        self.q.start()

    def tearDown(self):
        self.q.stop()
        self.p.stop()
        shutil.rmtree(self.ws, ignore_errors=True)

    def session(self, replies, start=None):
        model = Scripted(replies=list(replies), seen=[])
        return automc_core.Session(provider=start or self.local, model=model), model

    def switch(self, s, name, **kw):
        return s.switch_model(name, model=Scripted(replies=[AIMessage(content="after the switch")], seen=[]), **kw)

    def test_a_public_conversation_switches_with_a_notice_and_keeps_its_history(self):
        s, _ = self.session([call("TRIGAInputSpecTool", {"preset": PUBLIC_PRESET}), AIMessage(content="public done")])
        list(s.send("public question"))
        self.assertEqual(s.conversation_label(), "public")
        out = self.switch(s, "gemini")
        self.assertTrue(out["switched"])
        self.assertIn("outside this machine", out["message"])
        self.assertTrue(s.provider.cloud)
        evs = list(s.send("next"))
        self.assertEqual(evs[-1], ("final", "after the switch"))
        humans = [m.content for m in s.model.seen[-1] if isinstance(m, HumanMessage)]
        self.assertEqual(humans, ["public question", "next"])                   # the history went with the switch

    @unittest.skipUnless(restricted() is not None, "needs the restricted NETL facility pack")
    def test_a_restricted_conversation_is_refused_and_offered_a_new_one(self):
        s, _ = self.session([call("TRIGAInputSpecTool", {"config": 8}), AIMessage(content="restricted done")])
        list(s.send("show config 8"))
        self.assertEqual(s.conversation_label(), "restricted")
        out = self.switch(s, "gemini")
        self.assertFalse(out["switched"])
        self.assertTrue(out["refused"])
        self.assertIn("/new --model cloud-gemini/scripted", out["message"])
        self.assertFalse(s.provider.cloud)                                       # still local
        self.assertTrue(self.switch(s, "local")["switched"])                    # local is always allowed

    def test_unknown_origin_needs_a_typed_confirmation(self):
        with mock.patch.object(guard, "restricted_pack_installed", return_value=True):
            s, _ = self.session([call("OpenMCJobListTool", {}), AIMessage(content="listed")])
            list(s.send("jobs?"))
            self.assertEqual(s.conversation_label(), "unknown")                 # unlabelled while a restricted pack is installed
            out = self.switch(s, "gemini")
            self.assertTrue(out["needs_confirmation"])
            self.assertFalse(out["switched"])
            self.assertTrue(self.switch(s, "gemini", confirm=True)["switched"])

    @unittest.skipUnless(restricted() is not None, "needs the restricted NETL facility pack")
    def test_the_two_key_policy_and_never_for_a_personal_account(self):
        s, _ = self.session([call("TRIGAInputSpecTool", {"config": 8}), AIMessage(content="ok")])
        list(s.send("config 8"))
        cfg = {"policy": {"restricted_allowed_on": ["openai-portkey", "gemini"]}, "providers": {}}
        with mock.patch.object(llm, "load_config", return_value=cfg):
            self.assertTrue(self.switch(s, "openai-portkey")["refused"])        # one key only: the list without the variable
            with mock.patch.dict(os.environ, {llm.POLICY_ENV: "1"}):
                self.assertTrue(self.switch(s, "gemini")["refused"])            # personal account: never
                out = self.switch(s, "openai-portkey")
                self.assertTrue(out["switched"])
                self.assertTrue(s.policy_ok)

    def test_a_withheld_result_puts_only_public_text_into_the_conversation(self):
        with mock.patch.object(guard, "restricted_pack_installed", return_value=True):
            s, _ = self.session([call("OpenMCJobListTool", {}), AIMessage(content="withheld")], start=self.cloud)
            list(s.send("jobs?"))
        self.assertEqual(s.conversation_label(), "public")

    def test_no_switch_while_a_run_waits_for_approval(self):
        from tests.openmc.test_jobs import make_case
        make_case(self.ws, "sph")
        s, _ = self.session([call("OpenMCRunStartTool", {"case": "sph", "threads": 1})])
        evs = list(s.send("run sph"))
        self.assertEqual(evs[-1][0], "approval")
        self.assertTrue(self.switch(s, "gemini")["refused"])

    def test_the_chat_asks_for_the_typed_confirmation(self):
        with mock.patch.object(guard, "restricted_pack_installed", return_value=True):
            s, _ = self.session([call("OpenMCJobListTool", {}), AIMessage(content="listed")])
            list(s.send("jobs?"))
            with mock.patch.object(llm, "chat_model", return_value=Scripted(replies=[], seen=[])):
                out = io.StringIO()
                ui = ChatUI(s, ask=lambda q: "no", out=out)
                ui.command("/model gemini")
                self.assertIn("not switched", out.getvalue())
                self.assertFalse(s.provider.cloud)
                ui = ChatUI(s, ask=lambda q: "yes, send", out=out)
                ui.command("/model gemini")
                self.assertTrue(s.provider.cloud)


@unittest.skipUnless(HAVE_LANGGRAPH, "needs LangGraph (the WSL automc environment)")
class PackSkills(unittest.TestCase):
    def setUp(self):
        self.ws = Path(tempfile.mkdtemp(prefix="automc_skills_"))
        self.p = mock.patch.object(automc_paths, "WORKSPACE", self.ws)
        self.p.start()

    def tearDown(self):
        self.p.stop()
        shutil.rmtree(self.ws, ignore_errors=True)

    @unittest.skipUnless(restricted() is not None, "needs the restricted NETL facility pack")
    def test_restricted_skills_are_listed_for_a_local_model_and_read_on_demand(self):
        model = Scripted(replies=[call("SkillLookupTool", {"name": "NETL reference model"}), AIMessage(content="bias read")], seen=[])
        s = automc_core.Session(provider=provider("local", "local", "self_hosted"), model=model)
        system = model.seen[0][0].content if model.seen else s.system
        self.assertIn("SkillLookupTool", s.system)
        self.assertIn("NETL reference model", s.system)                        # listed by name
        self.assertEqual([v for v in canaries() if v in s.system], [])         # but its text is not in the prompt
        self.assertEqual(s.conversation_label(), "public")
        list(s.send("how biased is the reference model?"))
        self.assertEqual(s.conversation_label(), "restricted")                  # read, so the conversation holds it now
        tool_msg = json.loads([m for m in s.messages() if isinstance(m, ToolMessage)][-1].content)
        self.assertTrue([v for v in canaries() if v in tool_msg["text"]])        # the skill's text, with the pack's values

    @unittest.skipUnless(restricted() is not None, "needs the restricted NETL facility pack")
    def test_a_cloud_model_is_not_told_about_restricted_skills_and_cannot_read_them(self):
        model = Scripted(replies=[call("SkillLookupTool", {"name": "NETL reference model"}), AIMessage(content="withheld")], seen=[])
        s = automc_core.Session(provider=provider("gemini", "cloud", "personal"), model=model)
        self.assertNotIn("NETL reference model", s.system)
        list(s.send("how biased is it?"))
        self.assertTrue(json.loads([m for m in s.messages() if isinstance(m, ToolMessage)][-1].content)["withheld"])
        self.assertEqual(s.conversation_label(), "public")


@unittest.skipUnless(HAVE_LANGGRAPH, "needs LangGraph (the WSL automc environment)")
class ContextGuard(unittest.TestCase):
    def msgs(self, n_old_tools, size):
        out = [SystemMessage("s" * 400), HumanMessage("first")]
        for i in range(n_old_tools):
            out += [AIMessage(content="", tool_calls=[{"name": "T", "args": {}, "id": f"c{i}"}]),
                    ToolMessage(content="x" * size, tool_call_id=f"c{i}", name="T")]
        out += [AIMessage(content="answer"), HumanMessage("the current question")]
        return out

    def test_below_the_warning_nothing_happens(self):
        m = self.msgs(1, 100)
        out, info = context.fit(m, window=10_000)
        self.assertIs(out, m)
        self.assertIsNone(info["notice"])

    def test_a_warning_near_the_limit(self):
        m = self.msgs(2, 3_000)                                                    # about 1,900 tokens of 2,400
        _, info = context.fit(m, window=2_400)
        self.assertIn("window", info["notice"] or "")
        self.assertEqual(info["shortened"], 0)

    def test_old_tool_results_are_shortened_and_the_conversation_is_not_changed(self):
        m = self.msgs(4, 4_000)
        out, info = context.fit(m, window=4_000)
        self.assertGreater(info["shortened"], 0)
        self.assertLessEqual(info["fraction"], context.TARGET + 0.01)
        self.assertEqual(len(m[3].content), 4_000)                               # the original messages are untouched
        self.assertIn("shortened", out[3].content)
        self.assertEqual(out[-1].content, "the current question")

    def test_a_turn_that_cannot_fit_is_refused_without_calling_the_model(self):
        ws = Path(tempfile.mkdtemp(prefix="automc_ctx_"))
        try:
            with mock.patch.object(automc_paths, "WORKSPACE", ws):
                model = Scripted(replies=[AIMessage(content="never sent")], seen=[])
                s = automc_core.Session(provider=provider("tiny", "local", "self_hosted", window=1_000), model=model)
                evs = list(s.send("hello"))
            self.assertEqual(model.seen, [])
            self.assertIn("[AutoMC]", evs[-1][1])
            self.assertIn("/new", evs[-1][1])
        finally:
            shutil.rmtree(ws, ignore_errors=True)

    def test_the_default_local_model_is_the_32k_one_with_its_window(self):
        p = llm.provider("local", require_ready=False)
        self.assertEqual((p.model, p.window), ("qwen3.6:35b-32k-t0", 32768))
        self.assertEqual(llm.provider("local-128k", require_ready=False).window, 131072)
