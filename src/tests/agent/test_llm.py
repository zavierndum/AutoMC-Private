"""
The providers in config/llm.yaml: the loader's checks, what a provider still needs, the chat model each backend makes, and that a key
is never printed, described or audited. No network: checks against a real service are run by hand (python -m automc models check).
"""
import importlib.util
import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

HAVE_YAML = importlib.util.find_spec("yaml") is not None
HAVE_OPENAI = importlib.util.find_spec("langchain_openai") is not None
HAVE_OLLAMA = importlib.util.find_spec("langchain_ollama") is not None
SECRET = "sk-test-NEVER-PRINT-123"


def cfg(**providers):
    return {"default_provider": next(iter(providers)), "providers": providers}


CLOUD = {"kind": "cloud", "account": "personal", "backend": "openai_compatible", "base_url": "https://example.invalid/v1",
         "model": "some-model", "api_key_env": "AUTOMC_TEST_KEY"}


@unittest.skipUnless(HAVE_YAML, "needs PyYAML")
class Config(unittest.TestCase):
    def test_the_shipped_config_loads_and_names_every_service(self):
        from agent import llm
        names = {p.name: p for p in llm.providers()}
        self.assertEqual(set(names), {"local", "local-128k", "openai", "openai-portkey", "gemini", "claude"})
        self.assertEqual((names["local"].kind, names["local"].account), ("local", "self_hosted"))
        self.assertEqual(names["openai"].base_url, "https://api.openai.com/v1")          # OpenAI directly, no Portkey needed
        self.assertEqual(names["openai"].api_key_env, "OPENAI_API_KEY")
        self.assertEqual(names["openai-portkey"].account, "enterprise")
        self.assertIn("User-Agent", names["openai-portkey"].headers)
        for n in ("openai", "openai-portkey", "gemini", "claude"):
            self.assertTrue(names[n].cloud)
            self.assertEqual(names[n].model, "", n)                                         # no model is guessed
        self.assertEqual({n: p.discover for n, p in names.items()},                         # OpenAI and Gemini only (2026-10-26)
                         {"local": "ollama", "local-128k": None, "openai": "openai", "openai-portkey": "openai", "gemini": "gemini",
                          "claude": None})

    def test_malformed_providers_are_refused(self):
        from agent import llm
        bad = [{**CLOUD, "colour": "blue"}, {**CLOUD, "kind": "remote"}, {**CLOUD, "account": "self_hosted"},
               {**CLOUD, "backend": "magic"}, {k: v for k, v in CLOUD.items() if k != "api_key_env"},
               {**CLOUD, "headers": {"Authorization": "Bearer x"}}, {"kind": "local", "account": "personal", "backend": "ollama"},
               {**CLOUD, "discover": "anthropic"}, {**CLOUD, "discover": "ollama"}, {**CLOUD, "backend": "anthropic", "discover": "openai"},
               {"kind": "local", "backend": "ollama", "base_url": "x", "discover": "gemini"}]
        for p in bad:
            with self.subTest(p=p):
                with self.assertRaises(llm.LLMConfigError):
                    llm.provider("x", config=cfg(x=p), require_ready=False)

    def test_a_provider_says_what_it_still_needs(self):
        from agent import llm
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AUTOMC_TEST_KEY", None)
            p = llm.provider("x", config=cfg(x={**CLOUD, "model": ""}), require_ready=False)
            miss = " ".join(p.missing())
            self.assertIn("model name", miss)
            self.assertIn("AUTOMC_TEST_KEY", miss)
            with self.assertRaises(llm.LLMConfigError) as ctx:
                llm.provider("x", config=cfg(x={**CLOUD, "model": ""}))
            self.assertIn("not ready", str(ctx.exception))

    def test_unknown_provider_and_the_default(self):
        from agent import llm
        with self.assertRaises(llm.LLMConfigError):
            llm.provider("nope")
        with mock.patch.dict(os.environ, {"AUTOMC_LLM_PROVIDER": "gemini"}):
            self.assertEqual(llm.default_name(), "gemini")


@unittest.skipUnless(HAVE_YAML and HAVE_OPENAI and HAVE_OLLAMA, "needs langchain-openai and langchain-ollama (the WSL automc environment)")
class Models(unittest.TestCase):
    def test_each_backend_makes_its_model_with_the_right_address_headers_and_key(self):
        from agent import llm
        with mock.patch.dict(os.environ, {"AUTOMC_TEST_KEY": SECRET}):
            p = llm.provider("x", config=cfg(x={**CLOUD, "headers": {"User-Agent": "AutoMC"}, "max_output_tokens": 100}))
            m = llm.chat_model(p)
        self.assertEqual(type(m).__name__, "ChatOpenAI")
        self.assertEqual(m.model_name, "some-model")
        self.assertEqual(m.openai_api_base, "https://example.invalid/v1")
        self.assertEqual(m.default_headers, {"User-Agent": "AutoMC"})
        self.assertEqual(m.openai_api_key.get_secret_value(), SECRET)
        self.assertNotIn(SECRET, repr(m))                                                  # the key is kept secret by the client
        local = llm.chat_model(llm.provider("local"))
        self.assertEqual(type(local).__name__, "ChatOllama")
        self.assertEqual(local.num_ctx, 32768)                                 # the 32k default (2026-10-26)

    def test_a_missing_package_is_named(self):
        from agent import llm
        p = llm.provider("x", config=cfg(x={**CLOUD, "backend": "anthropic"}), require_ready=False)
        if importlib.util.find_spec("langchain_anthropic") is None:
            self.assertTrue(any("langchain-anthropic" in m for m in p.missing()))


@unittest.skipUnless(HAVE_YAML, "needs PyYAML")
class NoKeyLeaks(unittest.TestCase):
    def test_the_list_and_describe_never_show_a_key(self):
        from agent import llm, models
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": SECRET, "GEMINI_API_KEY": SECRET, "PORTKEY_API_KEY": SECRET}):
            out = io.StringIO()
            with redirect_stdout(out):
                models.main(["list"])
            self.assertNotIn(SECRET, out.getvalue())
            for p in llm.providers():
                self.assertNotIn(SECRET, p.describe())

    @unittest.skipUnless(importlib.util.find_spec("langgraph") is not None, "needs LangGraph")
    def test_the_chat_audit_never_records_a_key(self):
        import automc_paths
        from tests.agent.test_core import Scripted
        from langchain_core.messages import AIMessage
        import automc_core
        from agent import llm
        ws = Path(tempfile.mkdtemp(prefix="automc_keys_"))
        try:
            with mock.patch.object(automc_paths, "WORKSPACE", ws), mock.patch.dict(os.environ, {"AUTOMC_TEST_KEY": SECRET}):
                p = llm.provider("x", config=cfg(x=CLOUD))
                s = automc_core.Session(provider=p, model=Scripted(replies=[AIMessage(content="hi")], seen=[]))
                list(s.send("hello"))
                self.assertNotIn(SECRET, s.audit_path.read_text(encoding="utf-8"))
        finally:
            shutil.rmtree(ws, ignore_errors=True)


@unittest.skipUnless(HAVE_YAML and importlib.util.find_spec("langchain_core") is not None, "needs langchain-core")
class Check(unittest.TestCase):
    """models.check with a scripted model: the hello and the tool round trip are judged as they should be."""

    def scripted(self, replies):
        from tests.agent.test_core import Scripted
        return Scripted(replies=list(replies), seen=[])

    def test_hello_and_tool_round_trip(self):
        from langchain_core.messages import AIMessage
        from agent import models
        m = self.scripted([AIMessage(content="ready"),
                           AIMessage(content="", tool_calls=[{"name": "echo_number", "args": {"value": 7}, "id": "t1"}]),
                           AIMessage(content="It returned 14."), AIMessage(content="ready")])
        out = models.check("local", tools=True, model=m) if self._local_ready() else self.skipTest("local provider not ready here")
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["tool_call"]["args"], {"value": 7})
        self.assertEqual(out["schemas_answer"], "ready")                                 # the hello with AutoMC's tools bound
        json.dumps(out)
        m = self.scripted([AIMessage(content="ready"),
                           AIMessage(content="", tool_calls=[{"name": "echo_number", "args": {"value": 7}, "id": "t1"}]),
                           AIMessage(content="It returned 14."), AIMessage(content="", tool_calls=[{"name": "OpenMCJobListTool",
                                                                                                   "args": {}, "id": "t2"}])])
        out = models.check("local", tools=True, model=m)
        self.assertFalse(out["ok"])                                                       # a tool call instead of the hello
        self.assertIn("tool definitions", out["error"])

    def test_wrong_answers_fail(self):
        from langchain_core.messages import AIMessage
        from agent import models
        if not self._local_ready():
            self.skipTest("local provider not ready here")
        out = models.check("local", tools=True, model=self.scripted([AIMessage(content="ready"), AIMessage(content="no tools for you")]))
        self.assertFalse(out["ok"])
        self.assertIn("no echo_number tool call", out["error"])
        out = models.check("local", model=self.scripted([AIMessage(content="hello there")]))
        self.assertFalse(out["ok"])

    def _local_ready(self):
        from agent import llm
        return not llm.provider("local", require_ready=False).missing()
