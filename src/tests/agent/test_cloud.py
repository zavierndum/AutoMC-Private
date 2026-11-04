"""
Cloud details (AutoMC_Agent_Models_Plan.md, MS-4): the tool definitions as Gemini takes them and the arguments parsed back, how each
backend's chat model is made (Gemini through Google's own API, the Responses API for the GPT families that need it, usage reported
while streaming), a failed call in one sentence that loses nothing, and the tokens of a conversation with their cap. Scripted models
and fake errors; no network.
"""
import dataclasses
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

HAVE_YAML = importlib.util.find_spec("yaml") is not None
HAVE_LANGGRAPH = importlib.util.find_spec("langgraph") is not None
HAVE_OPENAI = importlib.util.find_spec("langchain_openai") is not None
HAVE_GENAI = importlib.util.find_spec("langchain_google_genai") is not None
SECRET = "sk-test-NEVER-PRINT-789"

from agent import errors, llm, schemas  # noqa: E402
from agent.llm import Provider  # noqa: E402


def spec_params():
    return {t["function"]["name"]: t["function"]["parameters"] for t in schemas.definitions()}["TRIGAInputSpecTool"]


def strip(o):
    """`o` without additionalProperties at any depth."""
    if isinstance(o, dict):
        return {k: strip(v) for k, v in o.items() if k != "additionalProperties"}
    return [strip(v) for v in o] if isinstance(o, list) else o


@unittest.skipUnless(HAVE_YAML, "needs PyYAML")
class Schemas(unittest.TestCase):
    def test_gemini_gets_text_for_free_form_objects_and_nothing_else_changes(self):
        defs = schemas.definitions()
        before = json.dumps(defs)
        self.assertIs(schemas.for_backend(defs, "openai_compatible"), defs)
        self.assertIs(schemas.for_backend(defs, "ollama"), defs)
        g = schemas.for_backend(defs, "google_genai")
        self.assertEqual(json.dumps(defs), before)                                  # the tools' own schemas are untouched
        self.assertNotIn("additionalProperties", json.dumps(g))
        self.assertEqual([t["function"]["name"] for t in g], [t["function"]["name"] for t in defs])
        changed = 0
        for src, dst in zip(defs, g):
            for name, ps in src["function"]["parameters"].get("properties", {}).items():
                new = dst["function"]["parameters"]["properties"][name]
                free = ps.get("type") == "object" and not ps.get("properties") or \
                    any(x.get("type") == "object" and not x.get("properties") for x in ps.get("anyOf", []))
                if free:
                    changed += 1
                    self.assertEqual(new["type"], "string", name)
                    self.assertIn("JSON text", new["description"])
                else:
                    self.assertEqual(new, strip(ps), name)
        self.assertEqual(changed, 37)                       # the 33 found on 2026-10-27, plus rods (a number or an object) in 4 tools
        rods = {t["function"]["name"]: t for t in g}["TRIGAInputSpecTool"]["function"]["parameters"]["properties"]["rods"]
        self.assertIn("number or object", rods["description"])

    @unittest.skipUnless(HAVE_GENAI, "needs langchain-google-genai (the WSL automc environment)")
    def test_googles_conversion_keeps_every_type_and_warns_about_nothing(self):
        from langchain_core.messages import HumanMessage
        from langchain_google_genai import ChatGoogleGenerativeAI
        g = schemas.for_backend(schemas.definitions(), "google_genai")
        m = ChatGoogleGenerativeAI(model="gemini-3-flash", google_api_key="not-a-key", max_retries=0)
        with self.assertNoLogs("langchain_google_genai", level="WARNING"):
            req = m._prepare_request([HumanMessage("hello")], tools=g)
        decls = {d.name: d.model_dump(exclude_none=True) for t in req["config"].tools for d in t.function_declarations or []}
        self.assertEqual(len(decls), len(g))
        for t in g:
            want = {k: (v.get("type") or "anyOf") for k, v in t["function"]["parameters"].get("properties", {}).items()}
            got = {k: (str(v.get("type") or "anyOf")).lower().replace("type.", "")
                   for k, v in decls[t["function"]["name"]]["parameters"].get("properties", {}).items()}
            self.assertEqual({k: str(v).lower() for k, v in want.items()}, got, t["function"]["name"])

    def test_repair_parses_text_into_the_declared_types(self):
        p = spec_params()
        args = {"spec": '{"pnt": "thermal"}', "rods": "0.5", "name": "c1", "config": "8", "fuel": "not json", "inserts": "[1, 2]",
                "zzz": '{"a": 1}'}
        out, fixed = schemas.repair(p, args)
        self.assertEqual(out["spec"], {"pnt": "thermal"})
        self.assertEqual(out["rods"], 0.5)
        self.assertEqual(out["config"], 8)
        self.assertEqual(sorted(fixed), ["config", "rods", "spec"])
        self.assertEqual((out["name"], out["fuel"], out["inserts"], out["zzz"]), ("c1", "not json", "[1, 2]", '{"a": 1}'))
        self.assertEqual(schemas.repair(p, {"rods": '{"transient": 0.5}'})[0]["rods"], {"transient": 0.5})
        self.assertEqual(schemas.repair(None, {"x": "1"}), ({"x": "1"}, []))


class Backends(unittest.TestCase):
    def provider(self, model, backend="openai_compatible", **kw):
        return Provider(name="t", kind="cloud", account="personal", backend=backend, base_url="https://x.invalid/v1", model=model,
                        api_key_env="AUTOMC_T_KEY", **kw)

    def test_the_gpt_families_that_need_the_responses_api(self):
        for model, want in (("gpt-5.4", True), ("gpt-5.4-mini", True), ("gpt-5.5", True), ("gpt-5.10", True), ("gpt-6", True),
                            ("gpt-6.1", True), ("gpt-6-luna", True), ("@ws-openai/gpt-5.4", True), ("gpt-5.1", False),
                            ("gpt-5.3", False), ("gpt-5", False), ("gpt-5-mini", False), ("gpt-4.1", False), ("gpt-4o", False),
                            ("gpt-60", False)):
            with self.subTest(model=model):
                self.assertEqual(self.provider(model).responses_api, want)
        self.assertFalse(Provider(name="l", kind="local", backend="ollama", base_url="x", model="gpt-5.4").responses_api)

    def test_caps_and_content_text(self):
        self.assertEqual(self.provider("m").cap, 200_000)
        self.assertEqual(self.provider("m", max_tokens_per_session=5_000).cap, 5_000)
        self.assertIsNone(Provider(name="l", kind="local", backend="ollama", base_url="x", model="m").cap)
        self.assertEqual(llm.content_text([{"type": "reasoning", "summary": []}, {"type": "text", "text": "a"}, "b",
                                           {"type": "output_text", "text": "c"}, {"type": "thinking", "thinking": "x"}]), "abc")
        self.assertEqual(llm.content_text("plain"), "plain")

    @unittest.skipUnless(HAVE_YAML, "needs PyYAML")
    def test_the_shipped_cloud_providers(self):
        names = {p.name: p for p in llm.providers()}
        self.assertEqual((names["gemini"].backend, names["gemini"].discover), ("google_genai", "gemini"))
        for n in ("openai", "openai-portkey", "gemini", "claude"):
            self.assertEqual(names[n].cap, 200_000, n)
        self.assertIsNone(names["local"].cap)
        self.assertEqual((names["openai"].window, names["openai-portkey"].window), (128_000, 128_000))

    def test_malformed_caps_are_refused(self):
        base = {"kind": "cloud", "account": "personal", "backend": "openai_compatible", "base_url": "x", "model": "m",
                "api_key_env": "K"}
        for bad in ({**base, "max_tokens_per_session": 0}, {**base, "max_tokens_per_session": "lots"},
                    {**base, "max_tokens_per_session": True}, {**base, "kind": "local", "account": "self_hosted",
                                                                "backend": "google_genai"}):
            with self.subTest(bad=bad):
                with self.assertRaises(llm.LLMConfigError):
                    llm.provider("x", config={"providers": {"x": bad}}, require_ready=False)

    @unittest.skipUnless(HAVE_OPENAI, "needs langchain-openai (the WSL automc environment)")
    def test_openai_models_stream_usage_and_use_the_responses_api_when_needed(self):
        with mock.patch.dict(os.environ, {"AUTOMC_T_KEY": SECRET}):
            new = llm.chat_model(self.provider("gpt-5.4"))
            old = llm.chat_model(self.provider("gpt-4.1"))
        self.assertTrue(new.use_responses_api)
        self.assertFalse(old.use_responses_api)
        self.assertTrue(new.stream_usage and old.stream_usage)
        self.assertEqual(new.max_retries, 2)

    @unittest.skipUnless(HAVE_GENAI, "needs langchain-google-genai (the WSL automc environment)")
    def test_gemini_goes_through_googles_own_api(self):
        with mock.patch.dict(os.environ, {"AUTOMC_T_KEY": SECRET}):
            m = llm.chat_model(self.provider("gemini-3-flash", backend="google_genai", max_output_tokens=4000))
        self.assertEqual(type(m).__name__, "ChatGoogleGenerativeAI")
        self.assertIn("gemini-3-flash", m.model)
        self.assertEqual((m.max_retries, m.max_output_tokens), (2, 4000))
        self.assertEqual(m.model_kwargs, {"stream_usage": True})
        self.assertNotIn(SECRET, repr(m))


def fake(cls_name, status=None, text="", base=Exception, cause=None):
    """An exception of class `cls_name` with status_code `status`, raised from `cause`."""
    cls = type(cls_name, (base,), {"status_code": status})
    try:
        if cause is not None:
            raise cls(text) from cause
        raise cls(text)
    except BaseException as e:
        return e


class Errors(unittest.TestCase):
    def setUp(self):
        self.cloud = Provider(name="gemini", kind="cloud", account="personal", backend="google_genai", base_url="https://g.invalid",
                              model="gemini-3-flash", api_key_env="AUTOMC_T_KEY", timeout_s=600)
        self.local = Provider(name="local", kind="local", backend="ollama", base_url="http://127.0.0.1:11434", model="qwen",
                              timeout_s=1800)
        self.env = mock.patch.dict(os.environ, {"AUTOMC_T_KEY": SECRET})
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def google(self, code, status, text):
        client = type("ClientError", (Exception,), {})(f"{code} {status}. {text}")
        client.code, client.status = code, status
        return fake("ChatGoogleGenerativeAIError", None, f"Error calling model 'gemini-3-flash' ({status}): {code} {status}",
                    cause=client)

    def test_each_failure_in_one_sentence_that_names_the_provider_and_never_the_key(self):
        cases = [
            (fake("AuthenticationError", 401, f"Error code: 401 - Incorrect API key provided: {SECRET}"), "key", "AUTOMC_T_KEY"),
            (fake("PermissionDeniedError", 403, "forbidden"), "denied", "403"),
            (fake("NotFoundError", 404, "The model `gpt-9` does not exist"), "model", "/model"),
            (fake("RateLimitError", 429, "Error code: 429 - Rate limit reached"), "rate", "429"),
            (self.google(429, "RESOURCE_EXHAUSTED", "You exceeded your current quota"), "rate", "quota"),
            (self.google(400, "INVALID_ARGUMENT", f"API key not valid. Please pass a valid API key. {SECRET}"), "key", "key"),
            (self.google(400, "INVALID_ARGUMENT", "functionDeclaration parameters schema is invalid"), "refused", "--tools"),
            (fake("GoogleContextOverflowError", 400, "too long"), "too_long", "/new"),
            (fake("BadRequestError", 400, "Error code: 400 - {'error': {'code': 'context_length_exceeded'}}"), "too_long", "context"),
            (fake("InternalServerError", 500, "boom"), "server", "server error"),
            (fake("APITimeoutError", None, "Request timed out."), "timeout", "600 s"),
            (TimeoutError("timed out"), "timeout", "within"),
            (fake("Weird", None, f"strange failure {SECRET}"), "other", "Weird"),
        ]
        for e, kind, needle in cases:
            with self.subTest(kind=kind, e=type(e).__name__):
                k, text = errors.explain(e, self.cloud)
                self.assertEqual(k, kind)
                self.assertIn(needle, text)
                self.assertNotIn(SECRET, text)
                self.assertIn("gemini", text)

    def test_a_local_model_that_does_not_answer(self):
        k, text = errors.explain(fake("ConnectError", None, "[Errno 111] Connection refused"), self.local)
        self.assertEqual(k, "unreachable")
        self.assertIn("ollama serve", text)
        k, text = errors.explain(fake("ReadTimeout", None, "timed out"), self.local)
        self.assertEqual(k, "timeout")
        self.assertIn("timeout_s", text)
        k, _ = errors.explain(fake("ResponseError", 404, 'model "qwen9" not found, try pulling it first'), self.local)
        self.assertEqual(k, "model")


@unittest.skipUnless(HAVE_LANGGRAPH, "needs LangGraph (the WSL automc environment)")
class SessionCalls(unittest.TestCase):
    def setUp(self):
        self.ws = Path(tempfile.mkdtemp(prefix="automc_cloud_"))
        self.p = mock.patch.object(automc_paths, "WORKSPACE", self.ws)
        self.p.start()

    def tearDown(self):
        self.p.stop()
        shutil.rmtree(self.ws, ignore_errors=True)

    def session(self, replies, provider=None):
        import automc_core
        from tests.agent.test_core import LOCAL, Scripted
        model = Scripted(replies=list(replies), seen=[])
        return automc_core.Session(provider=provider or LOCAL, model=model), model

    @staticmethod
    def usage(inp, out):
        return {"input_tokens": inp, "output_tokens": out, "total_tokens": inp + out}

    def test_a_failed_call_ends_the_turn_and_resume_tries_the_step_again(self):
        from langchain_core.messages import AIMessage, HumanMessage
        s, model = self.session([fake("RateLimitError", 429, "Error code: 429 - Rate limit reached"), AIMessage(content="answered")])
        evs = list(s.send("hello"))
        kinds = [e[0] for e in evs]
        self.assertIn("error", kinds)
        self.assertEqual(evs[-1], ("final", ""))
        err = [e[1] for e in evs if e[0] == "error"][0]
        self.assertIn("429", err)
        self.assertIn("/resume", err)
        evs = list(s.resume())
        self.assertEqual(evs[-1], ("final", "answered"))
        self.assertEqual([m.content for m in model.seen[-1] if isinstance(m, HumanMessage)], ["hello"])   # asked once, kept once
        audit = s.audit_path.read_text(encoding="utf-8")
        self.assertIn('"model_error"', audit)

    def test_retry_after_a_failed_call(self):
        from langchain_core.messages import AIMessage
        s, _ = self.session([fake("InternalServerError", 500, "boom"), AIMessage(content="second time")])
        self.assertEqual(list(s.send("hello"))[-1], ("final", ""))
        self.assertEqual(list(s.retry())[-1], ("final", "second time"))

    def test_text_arguments_are_parsed_before_the_tool_runs(self):
        import automc_core
        from langchain_core.messages import AIMessage
        from tests.agent.test_core import call
        from tests.reactors.triga._packs import PUBLIC_PRESET
        s, _ = self.session([call("TRIGAInputSpecTool", {"preset": PUBLIC_PRESET, "spec": '{"pnt": "thermal"}'}),
                             AIMessage(content="done")])
        real = automc_core.registry.call
        with mock.patch.object(automc_core.registry, "call", side_effect=real) as spy:
            list(s.send("thermal PNT?"))
        self.assertEqual(spy.call_args[0][1]["spec"], {"pnt": "thermal"})
        self.assertIn('"arguments_parsed"', s.audit_path.read_text(encoding="utf-8"))

    def test_tokens_come_from_the_service_report_or_an_estimate(self):
        from langchain_core.messages import AIMessage
        from tests.agent.test_core import call
        first = call("OpenMCJobListTool", {})
        first.usage_metadata = self.usage(9_000, 50)
        s, _ = self.session([first, AIMessage(content="done", usage_metadata=self.usage(9_500, 20)), AIMessage(content="no report")])
        list(s.send("jobs?"))
        self.assertEqual(s.spent(), {"input": 18_500, "output": 70, "calls": 2, "estimated": 0, "total": 18_570})
        list(s.send("again"))
        after = s.spent("test")
        self.assertEqual((after["calls"], after["estimated"]), (3, 1))
        self.assertGreater(after["input"], 18_500 + 1_000)                       # the estimate counts the prompt and the tools

    def test_the_cap_stops_a_step_before_anything_is_sent(self):
        from langchain_core.messages import AIMessage
        from tests.agent.test_core import CLOUD, call
        first = call("OpenMCJobListTool", {})
        first.usage_metadata = self.usage(59_000, 500)
        # the cap leaves room for the first step (the system prompt and the tool schemas, about 23k tokens), not for a second one
        s, model = self.session([first, AIMessage(content="never sent")], provider=dataclasses.replace(CLOUD, max_tokens_per_session=60_000))
        evs = list(s.send("jobs?"))
        self.assertEqual(len(model.seen), 1)                                     # the second step was not sent
        self.assertIn("[AutoMC] Not sent", evs[-1][1])
        self.assertIn("cap of 60,000", evs[-1][1])
        s, model = self.session([AIMessage(content="never sent")], provider=dataclasses.replace(CLOUD, max_tokens_per_session=1_000))
        self.assertIn("[AutoMC] Not sent", list(s.send("hello"))[-1][1])
        self.assertEqual(model.seen, [])

    def test_the_chat_shows_the_error_and_the_tokens(self):
        from langchain_core.messages import AIMessage
        from agent.chat import ChatUI
        from tests.agent.test_core import CLOUD
        s, _ = self.session([AIMessage(content="hi", usage_metadata=self.usage(9_000, 5)),
                             fake("RateLimitError", 429, "Error code: 429 - Rate limit reached")], provider=CLOUD)
        out = io.StringIO()
        ui = ChatUI(s, ask=lambda q: "", out=out)
        ui.turn(s.send("hello"))
        self.assertIn("tokens: 9,000 in, 5 out over 1 model call(s); cloud-test-cloud/scripted in this conversation: 9,005 of its cap of 200,000",
                      out.getvalue())
        ui.turn(s.send("again"))
        after = out.getvalue().split("200,000")[-1]
        self.assertIn("HTTP 429", after)
        self.assertIn("/resume", after)
        self.assertNotIn("tokens:", after)                                       # a failed call spent nothing that was counted


if __name__ == "__main__":
    unittest.main()
