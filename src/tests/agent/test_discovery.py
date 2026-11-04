"""
Model discovery and the picker (AutoMC_Agent_Models_Plan.md, MS-3): which listed models reach the chat (local: tools, vision and
thinking; cloud: OpenAI from gpt-4 on and Gemini from gemini-3 on, with RADIANT-LLM's filters), the window each is served at, the one
check a discovered cloud model needs, the hour's cache, and that no key is ever shown. Fake service answers; no network.
"""
import importlib.util
import io
import json
import os
import shutil
import tempfile
import unittest
import urllib.error
import urllib.parse
from pathlib import Path
from unittest import mock

import automc_paths

HAVE_YAML = importlib.util.find_spec("yaml") is not None
HAVE_LANGGRAPH = importlib.util.find_spec("langgraph") is not None
SECRET = "sk-test-NEVER-PRINT-456"
KEYS = {"AUTOMC_T_OPENAI": SECRET, "AUTOMC_T_PORTKEY": SECRET, "AUTOMC_T_GEMINI": SECRET}

ALL = ["completion", "vision", "tools", "thinking"]
OLLAMA = {                                                       # the WSL Ollama of 2026-10-26, plus two models that must stay out
    "qwen3.6:35b-32k-t0": {"capabilities": ALL, "parameters": "num_ctx                        32768\ntemperature                    0"},
    "qwen3.6:35b-128k-t0": {"capabilities": ALL, "parameters": "num_ctx                        131072"},
    "qwen3.5-9b-16k:latest": {"capabilities": ALL, "parameters": "num_ctx                        16384"},
    "qwen3.6:35b-a3b": {"capabilities": ALL, "parameters": "temperature                    1"},
    "qwen3.5:9b": {"capabilities": ALL},
    "qwen2.5-14b-8k:latest": {"capabilities": ["completion", "tools"], "parameters": "num_ctx                        8192"},
    "qwen2.5:14b": {"capabilities": ["completion", "tools"]},
    "llava:7b": {"capabilities": ["completion", "vision"]},
    "old-ollama:latest": {},                                      # capabilities not reported: not offered
}
OPENAI_KEPT = ["gpt-5.4", "gpt-5.4-mini", "gpt-4.1", "gpt-4o", "gpt-4"]
OPENAI_LEFT = ["gpt-3.5-turbo", "o3", "o4-mini", "gpt-4o-2024-08-06", "gpt-4-0613", "gpt-5-codex", "gpt-5-pro", "gpt-5-chat-latest",
               "gpt-live-1", "gpt-4o-realtime-preview", "gpt-4o-mini-tts", "text-embedding-3-large", "whisper-1", "dall-e-3",
               "gpt-image-1", "chatgpt-4o-latest", "gpt-4-32k", "chat-latest", "gpt-6-astra", "omni-moderation-latest", "davinci-002",
               "gpt-4o-search-preview", "computer-use-preview", "codex-mini-latest", "gpt-4o-transcribe", "gpt-audio"]
OPENAI = ([{"id": m, "created": 1000 - i} for i, m in enumerate(OPENAI_KEPT)]
          + [{"id": m, "created": 500 - i} for i, m in enumerate(OPENAI_LEFT)])
PORTKEY = [{"id": m, "created": None} for m in ("@ws-openai/gpt-4.1", "@ws-anthropic/claude-sonnet-4", "@ws-openai/gpt-5.4",
                                                "@ws-openai/gpt-3.5-turbo", "@ws-openai/o3", "@ws-gemini/gemini-3-flash",
                                                "@ws-openai/text-embedding-3-small")]
GEN = ["generateContent", "countTokens"]
GEMINI = [                                                       # oldest first, as Gemini lists them
    {"name": "models/gemini-1.5-pro", "supportedGenerationMethods": GEN},
    {"name": "models/gemini-2.5-flash", "supportedGenerationMethods": GEN, "inputTokenLimit": 1048576},
    {"name": "models/text-embedding-004", "supportedGenerationMethods": ["embedContent"]},
    {"name": "models/gemma-3-27b-it", "supportedGenerationMethods": GEN},
    {"name": "models/gemini-3-flash", "supportedGenerationMethods": GEN, "inputTokenLimit": 1048576},
    {"name": "models/gemini-3-flash-omni", "supportedGenerationMethods": GEN},
    {"name": "models/gemini-3.5-flash-live", "supportedGenerationMethods": GEN},
    {"name": "models/lyria-3", "supportedGenerationMethods": GEN},
    {"name": "models/gemini-robotics-er-1.5-preview", "supportedGenerationMethods": GEN},
    {"name": "models/antigravity-preview-latest", "supportedGenerationMethods": GEN},
    {"name": "models/gemini-flash-latest", "supportedGenerationMethods": GEN, "inputTokenLimit": 1048576},
    {"name": "models/gemini-3.1-pro", "supportedGenerationMethods": GEN, "inputTokenLimit": 2097152},
]


def config():
    local = {"kind": "local", "account": "self_hosted", "backend": "ollama", "base_url": "http://ollama.invalid", "temperature": 0,
             "reasoning": False}
    cloud = {"kind": "cloud", "backend": "openai_compatible", "model": ""}
    return {"default_provider": "local", "providers": {
        "local": {**local, "model": "qwen3.6:35b-32k-t0", "num_ctx": 32768, "discover": "ollama"},
        "local-128k": {**local, "model": "qwen3.6:35b-128k-t0", "num_ctx": 131072},
        "openai": {**cloud, "account": "personal", "base_url": "https://openai.invalid/v1", "api_key_env": "AUTOMC_T_OPENAI",
                   "discover": "openai"},
        "openai-portkey": {**cloud, "account": "enterprise", "base_url": "https://portkey.invalid/v1", "api_key_env": "AUTOMC_T_PORTKEY",
                           "headers": {"User-Agent": "AutoMC"}, "discover": "openai"},
        "gemini": {**cloud, "account": "personal", "backend": "google_genai", "base_url": "https://gemini.invalid/v1beta",
                   "api_key_env": "AUTOMC_T_GEMINI", "discover": "gemini"},
        "claude": {"kind": "cloud", "account": "personal", "backend": "anthropic", "base_url": "https://claude.invalid", "model": "",
                   "api_key_env": "AUTOMC_T_CLAUDE"},
    }}


class Services:
    """Answers AutoMC's listing requests as the services would; `fail` maps a host to the exception it raises."""

    def __init__(self, fail=None):
        self.calls = []
        self.fail = dict(fail or {})

    def __call__(self, url, headers=None, body=None, timeout=None):
        u = urllib.parse.urlparse(url)
        self.calls.append((u.hostname, u.path, dict(headers or {})))
        if u.hostname in self.fail:
            raise self.fail[u.hostname]
        if u.hostname == "ollama.invalid":
            return {"models": [{"name": n} for n in OLLAMA]} if u.path == "/api/tags" else OLLAMA[body["model"]]
        if u.hostname == "openai.invalid":
            return {"data": OPENAI}
        if u.hostname == "portkey.invalid":
            return {"data": PORTKEY}
        if u.hostname == "gemini.invalid":
            assert u.path == "/v1beta/models", u.path                  # Gemini's own API
            if "pageToken" not in urllib.parse.parse_qs(u.query):
                return {"models": GEMINI[:6], "nextPageToken": "page-2"}
            return {"models": GEMINI[6:]}
        raise AssertionError(f"unexpected request to {url}")

    def count(self, host):
        return sum(1 for h, _, _ in self.calls if h == host)


@unittest.skipUnless(HAVE_YAML, "needs PyYAML")
class Filters(unittest.TestCase):
    def test_openai_keeps_gpt_from_4_on_newest_first_and_leaves_out_the_rest(self):
        from agent import discovery
        offered, left = discovery.openai_models(OPENAI)
        self.assertEqual([m["model"] for m in offered], OPENAI_KEPT)
        self.assertEqual(sorted(left), sorted(OPENAI_LEFT))

    def test_portkey_ids_keep_their_slug_and_other_providers_stay_out(self):
        from agent import discovery
        offered, left = discovery.openai_models(PORTKEY)
        self.assertEqual([m["model"] for m in offered], ["@ws-openai/gpt-5.4", "@ws-openai/gpt-4.1"])   # by version: no `created`
        self.assertIn("@ws-anthropic/claude-sonnet-4", left)
        self.assertIn("@ws-gemini/gemini-3-flash", left)                 # a Gemini model is not an OpenAI model, even via Portkey

    def test_gemini_keeps_3_on_with_generate_content_newest_first(self):
        from agent import discovery
        offered, left = discovery.gemini_models(GEMINI)
        self.assertEqual([m["model"] for m in offered], ["gemini-3.1-pro", "gemini-flash-latest", "gemini-3-flash"])
        self.assertEqual(offered[0]["context_window"], 2097152)
        for m in ("gemini-1.5-pro", "gemini-2.5-flash", "gemma-3-27b-it", "gemini-3-flash-omni", "gemini-3.5-flash-live", "lyria-3",
                  "gemini-robotics-er-1.5-preview", "antigravity-preview-latest", "text-embedding-004"):
            self.assertIn(m, left)

    def test_local_models_need_tools_vision_and_thinking_and_keep_their_own_window(self):
        from agent import discovery
        offered, left = discovery.ollama_models({"models": [{"name": n} for n in OLLAMA]}, OLLAMA.get)
        self.assertEqual({m["model"]: m["num_ctx"] for m in offered},
                         {"qwen3.6:35b-32k-t0": 32768, "qwen3.6:35b-128k-t0": 131072, "qwen3.5-9b-16k:latest": 16384,
                          "qwen3.6:35b-a3b": None, "qwen3.5:9b": None})
        self.assertIn("qwen2.5:14b (lacks vision, thinking)", left)
        self.assertIn("llava:7b (lacks tools, thinking)", left)
        self.assertIn("old-ollama:latest (lacks tools, vision, thinking)", left)


class Fakes(unittest.TestCase):
    """The shipped llm.yaml replaced by config(), the services by Services, the workspace by a folder of its own."""

    def setUp(self):
        from agent import discovery, llm
        self.ws = Path(tempfile.mkdtemp(prefix="automc_disc_"))
        self.services = Services()
        real = importlib.util.find_spec
        self.patches = [mock.patch.object(automc_paths, "WORKSPACE", self.ws),
                        mock.patch.object(llm, "load_config", side_effect=lambda path=None: config()),
                        mock.patch.object(discovery, "_request", side_effect=lambda *a, **k: self.services(*a, **k)),
                        mock.patch.object(llm.importlib.util, "find_spec",                  # the model packages count as installed
                                          side_effect=lambda n, *a, **k: object() if n in llm.BACKENDS.values() else real(n, *a, **k)),
                        mock.patch.dict(os.environ, KEYS)]
        for p in self.patches:
            p.start()
        discovery._CACHE.clear()

    def tearDown(self):
        from agent import discovery
        for p in reversed(self.patches):
            p.stop()
        discovery._CACHE.clear()
        shutil.rmtree(self.ws, ignore_errors=True)


@unittest.skipUnless(HAVE_YAML, "needs PyYAML")
class Catalog(Fakes):
    def test_configured_models_then_discovered_ones_without_duplicates(self):
        from agent import discovery
        cat = discovery.catalog()
        rows = {e["choice"]: e for e in cat["entries"]}
        self.assertEqual([e["choice"] for e in cat["entries"]][:5],
                         ["local", "local/qwen3.5-9b-16k:latest", "local/qwen3.6:35b-a3b", "local/qwen3.5:9b", "local-128k"])
        self.assertNotIn("local/qwen3.6:35b-128k-t0", rows)                       # local-128k offers it already
        self.assertEqual([e["n"] for e in cat["entries"]], list(range(1, len(cat["entries"]) + 1)))
        self.assertEqual(rows["local/qwen3.5-9b-16k:latest"]["window"], 16384)    # its own num_ctx
        self.assertEqual(rows["local/qwen3.6:35b-a3b"]["window"], 32768)          # none of its own: the provider's
        self.assertEqual(rows["gemini/gemini-3.1-pro"]["window"], 2097152)
        self.assertIsNone(rows["openai/gpt-5.4"]["window"])
        self.assertIn("openai-portkey/@ws-openai/gpt-5.4", rows)
        self.assertEqual(rows["openai-portkey/@ws-openai/gpt-5.4"]["account"], "enterprise")   # kind and account from the provider
        self.assertEqual(rows["gemini/gemini-3-flash"]["kind"], "cloud")
        self.assertFalse(any(c.startswith("claude") for c in rows))               # Claude is not discovered
        self.assertTrue(any(u["provider"] == "claude" and "model name" in u["reason"] for u in cat["unavailable"]))
        self.assertEqual(rows["local/qwen3.5:9b"]["state"], "ready")
        self.assertIn("not checked yet", rows["openai/gpt-5.4"]["state"])
        self.assertFalse(rows["openai/gpt-5.4"]["ready"])
        local_left = [i for i in cat["left_out"] if i["provider"] == "local"][0]["models"]
        self.assertIn("qwen2.5:14b (lacks vision, thinking)", local_left)

    def test_no_key_no_list_and_the_key_is_never_shown(self):
        from agent import discovery
        from agent.models import picker_lines
        os.environ.pop("AUTOMC_T_OPENAI")
        self.services.fail["gemini.invalid"] = RuntimeError(f"refused key {SECRET}")
        cat = discovery.catalog()
        reasons = {u["provider"]: u["reason"] for u in cat["unavailable"]}
        self.assertIn("no key in AUTOMC_T_OPENAI", reasons["openai"])
        self.assertEqual(self.services.count("openai.invalid"), 0)
        self.assertIn("***", reasons["gemini"])
        text = json.dumps(cat) + "\n".join(picker_lines(cat))
        self.assertNotIn(SECRET, text)
        sent = {h: hd for h, _, hd in self.services.calls}
        self.assertEqual(sent["portkey.invalid"]["Authorization"], f"Bearer {SECRET}")   # the key goes only in the request header
        self.assertEqual(sent["portkey.invalid"]["User-Agent"], "AutoMC")

    def test_cloud_lists_are_kept_an_hour_and_a_failed_listing_serves_the_last(self):
        from agent import discovery
        discovery.catalog()
        discovery.catalog()
        self.assertEqual(self.services.count("openai.invalid"), 1)
        self.assertEqual(self.services.count("ollama.invalid"), 2 * (1 + len(OLLAMA)))   # the local list is read each time
        discovery.catalog(refresh=True)
        self.assertEqual(self.services.count("openai.invalid"), 2)
        self.services.fail["openai.invalid"] = urllib.error.URLError("down")
        cat = discovery.catalog(refresh=True)
        self.assertIn("openai/gpt-5.4", [e["choice"] for e in cat["entries"]])
        self.assertTrue(any(u["provider"] == "openai" and "showing the list of" in u["reason"] for u in cat["unavailable"]))

    def test_ollama_not_running(self):
        from agent import discovery
        self.services.fail["ollama.invalid"] = urllib.error.URLError("connection refused")
        cat = discovery.catalog()
        self.assertEqual([e["choice"] for e in cat["entries"] if e["kind"] == "local"], ["local", "local-128k"])
        self.assertTrue(any(u["provider"] == "local" and "not reachable" in u["reason"] for u in cat["unavailable"]))


@unittest.skipUnless(HAVE_YAML, "needs PyYAML")
class Resolve(Fakes):
    def test_a_discovered_local_model_keeps_its_provider_and_its_own_window(self):
        from agent import discovery
        p = discovery.resolve("local/qwen3.5-9b-16k")                             # without :latest, as Ollama allows
        self.assertEqual((p.name, p.kind, p.account, p.model), ("local", "local", "self_hosted", "qwen3.5-9b-16k:latest"))
        self.assertEqual(p.choice, "local/qwen3.5-9b-16k:latest")
        self.assertEqual((p.window, p.reasoning, p.temperature), (16384, False, 0))

    def test_the_configured_model_written_out_is_the_provider_itself(self):
        from agent import discovery
        p = discovery.resolve("local/qwen3.6:35b-32k-t0")
        self.assertEqual((p.choice, p.discovered), ("local", False))
        self.assertEqual(discovery.resolve("local-128k").window, 131072)

    def test_what_the_filters_leave_out_cannot_be_chosen_by_hand(self):
        from agent import discovery, llm
        for choice, why in (("local/qwen2.5:14b", "tools, vision and thinking"), ("openai/gpt-3.5-turbo", "gpt-4"),
                            ("openai/o3", "gpt-4"), ("gemini/gemini-2.5-flash", "gemini-3"), ("gemini/gemma-3-27b-it", "gemini-3"),
                            ("openai-portkey/@ws-anthropic/claude-sonnet-4", "gpt-4"), ("claude/any-model", "does not discover")):
            with self.subTest(choice=choice):
                with self.assertRaises(llm.LLMConfigError) as ctx:
                    discovery.resolve(choice)
                self.assertIn(why, str(ctx.exception))

    def test_an_unreadable_list_is_named_as_such_not_as_a_missing_model(self):
        """2026-11-02: WSL behind the UT VPN reached no site, and the eval said "openai-portkey does not offer gpt-5.4"."""
        from agent import discovery, llm
        self.services.fail["openai.invalid"] = urllib.error.URLError(OSError(101, "Network is unreachable"))
        with self.assertRaises(llm.LLMConfigError) as ctx:
            discovery.resolve("openai/gpt-5.4")
        self.assertIn("could not be read", str(ctx.exception))
        self.assertIn("Network is unreachable", str(ctx.exception))
        self.assertNotIn("does not offer", str(ctx.exception))

    def test_a_discovered_cloud_model_needs_one_passed_check_with_tools(self):
        from agent import discovery
        with self.assertRaises(discovery.NotChecked) as ctx:
            discovery.resolve("gemini/gemini-3-flash")
        self.assertIn("models check cloud-gemini/gemini-3-flash --tools", str(ctx.exception))
        discovery.record_check("gemini/gemini-3-flash", {"ok": True, "seconds": 1.0}, tools=False)
        with self.assertRaises(discovery.NotChecked):
            discovery.resolve("gemini/gemini-3-flash")                           # a hello alone is not enough
        discovery.record_check("gemini/gemini-3-flash", {"ok": True, "seconds": 1.0, "tool_seconds": 2.0}, tools=True)
        p = discovery.resolve("gemini/gemini-3-flash")
        self.assertEqual((p.kind, p.account, p.model, p.window), ("cloud", "personal", "gemini-3-flash", 1048576))
        saved = json.loads((self.ws / ".automc" / "models" / "checks.json").read_text(encoding="utf-8"))
        self.assertEqual(set(saved["gemini/gemini-3-flash"]), {"ok", "tools", "t", "seconds", "tool_seconds"})
        discovery.record_check("gemini/gemini-3-flash", {"ok": False}, tools=True)
        with self.assertRaises(discovery.NotChecked):
            discovery.resolve("gemini/gemini-3-flash")                           # the latest check counts

    @unittest.skipUnless(importlib.util.find_spec("langchain_core") is not None, "needs langchain-core")
    def test_only_a_real_check_is_recorded(self):
        from langchain_core.messages import AIMessage
        from agent import discovery, models
        from tests.agent.test_core import Scripted
        m = Scripted(replies=[AIMessage(content="ready"),
                              AIMessage(content="", tool_calls=[{"name": "echo_number", "args": {"value": 7}, "id": "t1"}]),
                              AIMessage(content="It returned 14."), AIMessage(content="ready")], seen=[])
        self.assertTrue(models.check("gemini/gemini-3-flash", tools=True, model=m)["ok"])
        self.assertFalse(discovery.passed("gemini/gemini-3-flash"))             # a scripted model proves nothing about the service

    @unittest.skipUnless(importlib.util.find_spec("langchain_core") is not None, "needs langchain-core")
    def test_a_check_without_a_given_model_builds_the_provider_s_own_and_is_recorded(self):
        from langchain_core.messages import AIMessage
        from agent import discovery, llm, models
        from tests.agent.test_core import Scripted
        m = Scripted(replies=[AIMessage(content="ready"),
                              AIMessage(content="", tool_calls=[{"name": "echo_number", "args": {"value": 7}, "id": "t1"}]),
                              AIMessage(content="It returned 14."), AIMessage(content="ready")], seen=[])
        with mock.patch.object(llm, "chat_model", return_value=m) as made:
            out = models.check("gemini/gemini-3-flash", tools=True)
        self.assertTrue(out["ok"], out.get("error"))
        self.assertEqual(made.call_args.args[0].choice, "gemini/gemini-3-flash")
        self.assertTrue(discovery.passed("gemini/gemini-3-flash"))


@unittest.skipUnless(HAVE_YAML and HAVE_LANGGRAPH, "needs LangGraph (the WSL automc environment)")
class Picker(Fakes):
    def setUp(self):
        super().setUp()
        import automc_core
        from agent import llm
        from tests.agent.test_core import Scripted
        from langchain_core.messages import AIMessage
        self.chat_patch = mock.patch.object(llm, "chat_model", side_effect=lambda p: Scripted(replies=[AIMessage(content="hi")],
                                                                                                seen=[]))
        self.chat_patch.start()
        self.s = automc_core.Session(provider_name="local")

    def tearDown(self):
        self.chat_patch.stop()
        super().tearDown()

    def ui(self, answers=()):
        from agent.chat import ChatUI
        answers = list(answers)
        self.out = io.StringIO()
        return ChatUI(self.s, ask=lambda q: answers.pop(0) if answers else "", out=self.out)

    def number(self, choice):
        from agent import discovery
        return [e["n"] for e in discovery.catalog()["entries"] if e["choice"] == choice][0]

    def test_the_list_is_numbered_and_a_number_switches(self):
        ui = self.ui()
        ui.command("/model")
        text = self.out.getvalue()
        self.assertIn("on this machine", text)
        self.assertIn("local-ollama/qwen3.5-9b-16k ", text)
        self.assertNotIn("qwen2.5:14b ", text.split("left out")[0])               # listed only as left out
        ui.command(f"/model {self.number('local/qwen3.5-9b-16k:latest')}")
        self.assertEqual(self.s.provider.choice, "local/qwen3.5-9b-16k:latest")
        self.assertEqual(self.s.provider.window, 16384)
        self.assertIn("stays on this machine", self.out.getvalue())

    def test_an_unchecked_cloud_model_is_checked_only_if_the_user_agrees(self):
        from agent import discovery, models
        n = self.number("openai/gpt-5.4")
        ui = self.ui(["n"])
        ui.command(f"/model {n}")
        self.assertIn("not switched", self.out.getvalue())
        self.assertFalse(self.s.provider.cloud)
        ok = {"ok": True, "seconds": 0.5, "tool_seconds": 1.5}
        with mock.patch.object(models, "check", side_effect=lambda c, tools: (discovery.record_check(c, ok, tools), ok)[1]) as chk:
            ui = self.ui(["y"])
            ui.command(f"/model {n}")
        chk.assert_called_once_with("openai/gpt-5.4", tools=True)
        self.assertEqual(self.s.provider.choice, "openai/gpt-5.4")
        self.assertTrue(self.s.provider.cloud)
        self.assertIn("outside this machine", self.out.getvalue())

    def test_a_typed_model_the_filters_leave_out_is_refused(self):
        ui = self.ui()
        ui.command("/model local/qwen2.5:14b")
        self.assertIn("tools, vision and thinking", self.out.getvalue())
        self.assertEqual(self.s.provider.choice, "local")


if __name__ == "__main__":
    unittest.main()
