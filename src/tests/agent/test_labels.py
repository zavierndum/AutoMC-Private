"""
What the screen shows of a model (WD-0, devops/AutoMC_Workspace_Plan.md): KIND-SERVICE/MODEL, never a Portkey workspace's @slug/ (it
names the account), and the same text typed back chooses the model; a Portkey model may also be typed without its slug. The full id
stays inside AutoMC (checks, the audit). Fake services; no network.
"""
import unittest
from types import SimpleNamespace
from unittest import mock

from tests.agent import test_discovery
from tests.agent.test_discovery import HAVE_YAML, Fakes


@unittest.skipUnless(HAVE_YAML, "needs PyYAML")
class Labels(Fakes):
    def setUp(self):
        super().setUp()
        from agent import discovery
        self.checked = mock.patch.object(discovery, "passed", return_value=True)    # every discovered cloud model checked once
        self.checked.start()

    def tearDown(self):
        self.checked.stop()
        super().tearDown()

    def test_each_model_shows_kind_service_and_model(self):
        from agent import discovery
        rows = {e["choice"]: e["label"] for e in discovery.catalog()["entries"]}
        self.assertEqual(rows["local"], "local-ollama/qwen3.6:35b-32k-t0")
        self.assertEqual(rows["local-128k"], "local-ollama/qwen3.6:35b-128k-t0")
        self.assertEqual(rows["local/qwen3.5-9b-16k:latest"], "local-ollama/qwen3.5-9b-16k")         # without :latest
        self.assertEqual(rows["openai-portkey/@ws-openai/gpt-5.4"], "cloud-portkey/gpt-5.4")
        self.assertEqual(rows["openai/gpt-5.4"], "cloud-openai/gpt-5.4")
        self.assertEqual(rows["gemini/gemini-3-flash"], "cloud-gemini/gemini-3-flash")
        self.assertFalse([label for label in rows.values() if "@" in label])

    def test_a_label_typed_back_chooses_the_same_model(self):
        from agent import discovery, llm
        for typed, choice in (("cloud-portkey/gpt-5.4", "openai-portkey/@ws-openai/gpt-5.4"),
                              ("openai-portkey/gpt-5.4", "openai-portkey/@ws-openai/gpt-5.4"),        # the provider, without the slug
                              ("openai-portkey/@ws-openai/gpt-5.4", "openai-portkey/@ws-openai/gpt-5.4"),   # the old full name
                              ("cloud-openai/gpt-5.4", "openai/gpt-5.4"),
                              ("cloud-gemini/gemini-3-flash", "gemini/gemini-3-flash"),
                              ("local-ollama/qwen3.6:35b-32k-t0", "local"),
                              ("local-ollama/qwen3.6:35b-128k-t0", "local-128k"),
                              ("local-ollama/qwen3.5-9b-16k", "local/qwen3.5-9b-16k:latest")):
            with self.subTest(typed=typed):
                p = discovery.resolve(typed)
                self.assertEqual(p.choice, choice)
                self.assertEqual(discovery.resolve(p.label).choice, choice)                       # what the screen shows works too
        for typed, why in (("cloud-nowhere/gpt-5.4", "served by nowhere"), ("cloud-portkey/gpt-3.5-turbo", "gpt-4"),
                           ("local-ollama/qwen2.5:14b", "tools, vision and thinking")):
            with self.subTest(typed=typed), self.assertRaises(llm.LLMConfigError) as ctx:
                discovery.resolve(typed)
            self.assertIn(why, str(ctx.exception))

    def test_one_model_from_two_portkey_providers_is_chosen_by_number(self):
        from agent import discovery, llm
        two = test_discovery.PORTKEY + [{"id": "@ws-second/gpt-5.4", "created": None}]
        with mock.patch.object(test_discovery, "PORTKEY", two), self.assertRaises(llm.LLMConfigError) as ctx:
            discovery.resolve("cloud-portkey/gpt-5.4")
        self.assertIn("choose one by its number", str(ctx.exception))
        with mock.patch.object(test_discovery, "PORTKEY", two):
            discovery._CACHE.clear()
            self.assertEqual(discovery.resolve("openai-portkey/@ws-second/gpt-5.4").choice, "openai-portkey/@ws-second/gpt-5.4")

    def test_the_screen_never_shows_the_slug_and_the_audit_keeps_it(self):
        from agent import chat, discovery, errors, llm
        from agent.models import picker_lines
        p = discovery.resolve("cloud-portkey/gpt-5.4")
        self.assertEqual(p.label, "cloud-portkey/gpt-5.4")
        self.assertNotIn("@ws-openai", p.describe())
        self.assertIn("@ws-openai/gpt-5.4", p.full_name())
        self.assertNotIn("@ws-", "\n".join(picker_lines(discovery.catalog(), now=p.choice)))
        session = SimpleNamespace(provider=p, system="x", prompt_info={"skills": ["Core"], "skipped_packs": [], "manifest": []})
        banner = chat.header(session)
        self.assertIn("AutoMC chat   cloud-portkey/gpt-5.4", banner)
        self.assertNotIn("@ws-", banner)
        self.assertNotIn("@ws-", errors.explain(RuntimeError("Error code: 404 - model @ws-openai/gpt-5.4 not found"), p)[1])
        self.assertEqual(llm.screen_name("openai-portkey/@ws-openai/gpt-5.4"), "openai-portkey/gpt-5.4")


if __name__ == "__main__":
    unittest.main()
