"""
GT-1 (devops/AutoMC_General_Tools_Plan.md): keys from a file outside AutoMC, and LangSmith tracing of what a cloud model may see.

The keys file: only the variables AutoMC names are kept, never in os.environ; the shell wins; a file inside AutoMC is refused; no
value reaches any output. Tracing: LangChain's own switches are off; a conversation is traced only while it is public and its prompt
could go to a cloud model, checked against a fake LangSmith server on this machine (nothing leaves it).
"""
import contextlib
import importlib.util
import io
import json
import os
import shutil
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest import mock

import automc_paths
from agent import keys, models, tracing

HAVE_DOTENV = importlib.util.find_spec("dotenv") is not None
HAVE_LANGGRAPH = importlib.util.find_spec("langgraph") is not None and importlib.util.find_spec("langsmith") is not None
SECRET = "sk-file-secret-0123456789"
OTHER = "other-secret-9876543210"
KEY_VARS = ("OPENAI_API_KEY", "PORTKEY_API_KEY", "GEMINI_API_KEY", "TAVILY_API_KEY", "LANGSMITH_API_KEY", "LANGCHAIN_API_KEY",
            "AUTOMC_TRACING", "AUTOMC_LANGSMITH_PROJECT", "LANGSMITH_ENDPOINT", "AUTOMC_LLM_PROVIDER", keys.ENV_VAR)

if HAVE_LANGGRAPH:
    from langchain_core.load import dumpd
    from langchain_core.messages import AIMessage

    import automc_core
    from agent import guard, llm
    from tests.agent.test_core import CLOUD, LOCAL, Scripted, call

    class Quiet(Scripted):
        """The scripted model without its replies in its repr, which LangSmith records (a real model's repr holds no replies)."""
        def __repr__(self):
            return "Scripted()"


class Clean(unittest.TestCase):
    """Each test starts with none of the key variables set and an empty cache."""

    def setUp(self):
        self.env = mock.patch.dict(os.environ)
        self.env.start()
        for n in KEY_VARS:
            os.environ.pop(n, None)
        keys.reset()
        self.tmp = Path(tempfile.mkdtemp(prefix="automc_keys_"))

    def tearDown(self):
        self.env.stop()
        keys.reset()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def keys_file(self, text, name="keys.env", mode=0o600):
        f = self.tmp / name
        f.write_text(text, encoding="utf-8")
        f.chmod(mode)
        os.environ[keys.ENV_VAR] = str(f)
        keys.reset()
        return f


class KeysFile(Clean):
    @unittest.skipUnless(HAVE_DOTENV, "needs python-dotenv (the WSL automc environment)")
    def test_only_the_named_variables_are_kept_and_never_in_the_environment(self):
        self.keys_file(f"OPENAI_API_KEY={SECRET}\nexport TAVILY_API_KEY=\"{SECRET}-t\"\nHost_Key={OTHER}\nSOME_OTHER={OTHER}\n"
                       f"GEMINI_API_KEY=\n")
        self.assertEqual(keys.value("OPENAI_API_KEY"), SECRET)
        self.assertEqual(keys.value("TAVILY_API_KEY"), f"{SECRET}-t")
        self.assertEqual(keys.value("Host_Key"), "")
        self.assertEqual(keys.value("GEMINI_API_KEY"), "")                  # an empty value counts as not set
        st = keys.status()
        self.assertEqual((st["used"], st["other"], st["error"]), (["OPENAI_API_KEY", "TAVILY_API_KEY"], 2, None))
        for n in ("OPENAI_API_KEY", "TAVILY_API_KEY", "Host_Key", "SOME_OTHER"):
            self.assertNotIn(n, os.environ)                                     # no child process inherits them
        self.assertNotIn(OTHER, json.dumps(keys._state(), default=str))

    @unittest.skipUnless(HAVE_DOTENV, "needs python-dotenv (the WSL automc environment)")
    def test_the_shell_wins_and_each_key_says_where_it_came_from(self):
        self.keys_file(f"OPENAI_API_KEY={SECRET}\n")
        self.assertEqual(keys.source("OPENAI_API_KEY"), keys.FILE)
        os.environ["OPENAI_API_KEY"] = "from-the-shell"
        self.assertEqual((keys.value("OPENAI_API_KEY"), keys.source("OPENAI_API_KEY")), ("from-the-shell", keys.SHELL))
        self.assertIsNone(keys.source("PORTKEY_API_KEY"))

    @unittest.skipUnless(HAVE_DOTENV, "needs python-dotenv (the WSL automc environment)")
    def test_a_provider_takes_its_key_from_the_file(self):
        from agent import llm
        p = next(p for p in llm.providers() if p.api_key_env == "OPENAI_API_KEY")
        self.assertTrue(any("OPENAI_API_KEY" in m for m in p.missing()))
        self.keys_file(f"OPENAI_API_KEY={SECRET}\n")
        self.assertEqual(p.key(), SECRET)
        self.assertFalse(any("OPENAI_API_KEY" in m for m in p.missing()))

    @unittest.skipUnless(HAVE_DOTENV, "needs python-dotenv (the WSL automc environment)")
    def test_the_keys_file_can_choose_the_model_a_chat_starts_with(self):
        from agent import llm
        self.assertEqual(llm.default_name(), llm.load_config()["default_provider"])
        self.keys_file("# AUTOMC_LLM_PROVIDER=openai-portkey/@slug/gpt-5.4\nAUTOMC_LLM_PROVIDER=openai/gpt-5.4\n")
        self.assertEqual(llm.default_name(), "openai/gpt-5.4")                   # the commented line is ignored
        os.environ["AUTOMC_LLM_PROVIDER"] = "gemini"
        self.assertEqual(llm.default_name(), "gemini")                           # the shell wins

    def test_a_file_inside_the_automc_folder_is_refused(self):
        with mock.patch.object(automc_paths, "AUTOMC_ROOT", self.tmp):
            self.keys_file(f"OPENAI_API_KEY={SECRET}\n")
            self.assertEqual(keys.value("OPENAI_API_KEY"), "")
            self.assertIn("refused", keys.status()["error"])
            self.assertIn("published", keys.status()["error"])

    def test_a_missing_file_is_reported_and_nothing_breaks(self):
        os.environ[keys.ENV_VAR] = str(self.tmp / "nowhere.env")
        self.assertEqual(keys.value("OPENAI_API_KEY"), "")
        self.assertIn("does not exist", keys.status()["error"])

    def test_without_python_dotenv_the_file_is_reported_not_read(self):
        self.keys_file(f"OPENAI_API_KEY={SECRET}\n")
        with mock.patch.dict("sys.modules", {"dotenv": None}):
            keys.reset()
            self.assertEqual(keys.value("OPENAI_API_KEY"), "")
            self.assertIn("python-dotenv", keys.status()["error"])

    @unittest.skipUnless(HAVE_DOTENV and os.name == "posix" and not tempfile.gettempdir().startswith("/mnt/"), "needs a Linux file")
    def test_a_file_others_can_read_is_warned_about(self):
        self.keys_file(f"OPENAI_API_KEY={SECRET}\n", mode=0o644)
        self.assertIn("chmod 600", keys.status()["warning"])
        self.keys_file(f"OPENAI_API_KEY={SECRET}\n", mode=0o600)
        self.assertIsNone(keys.status()["warning"])

    @unittest.skipUnless(HAVE_DOTENV, "needs python-dotenv (the WSL automc environment)")
    def test_models_list_shows_sources_never_values(self):
        self.keys_file(f"OPENAI_API_KEY={SECRET}\nLANGSMITH_API_KEY={SECRET}-ls\nHost_Key={OTHER}\n")
        os.environ["AUTOMC_TRACING"] = "langsmith"
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            models.main(["list"])
        text = out.getvalue()
        self.assertIn("OPENAI_API_KEY (keys file)", text)
        self.assertIn("PORTKEY_API_KEY (not set)", text)
        self.assertIn("2 variable(s) used, 1 other(s) ignored", text)
        self.assertIn("LangSmith tracing: on: project automc" if importlib.util.find_spec("langsmith")
                      else "LangSmith tracing: off: the Python package langsmith is not installed", text)
        self.assertNotIn(SECRET, text)
        self.assertNotIn(OTHER, text)


class Settings(Clean):
    def test_langchain_s_own_switches_are_off(self):
        tracing.force_off()
        self.assertEqual({os.environ[s] for s in tracing.SWITCHES}, {"false"})

    def test_off_unless_asked_for_with_a_key(self):
        self.assertEqual((tracing.settings()["requested"], tracing.settings()["on"]), (False, False))
        os.environ["AUTOMC_TRACING"] = "langsmith"
        t = tracing.settings()
        self.assertEqual((t["requested"], t["on"]), (True, False))
        self.assertIn("no key", t["message"])
        os.environ["AUTOMC_TRACING"] = "phoenix"
        self.assertIn("the only tracer is langsmith", tracing.settings()["message"])

    @unittest.skipUnless(importlib.util.find_spec("langsmith"), "needs langsmith")
    def test_on_with_the_key_under_either_name(self):
        os.environ["AUTOMC_TRACING"] = "langsmith"
        for name in tracing.KEY_NAMES:
            with self.subTest(name=name), mock.patch.dict(os.environ, {name: SECRET}):
                t = tracing.settings()
                self.assertEqual((t["on"], t["project"], t["key"]), (True, "automc", name))
                self.assertNotIn(SECRET, t["message"])
        with mock.patch.dict(os.environ, {"LANGSMITH_API_KEY": SECRET, "AUTOMC_LANGSMITH_PROJECT": "mine"}):
            self.assertEqual(tracing.settings()["project"], "mine")

    @unittest.skipUnless(importlib.util.find_spec("langsmith"), "needs langsmith")
    def test_a_placeholder_under_the_first_name_is_passed_over(self):
        os.environ.update({"AUTOMC_TRACING": "langsmith", "LANGSMITH_API_KEY": "langsmith", "LANGCHAIN_API_KEY": SECRET})
        t = tracing.settings()
        self.assertEqual((t["on"], t["key"]), (True, "LANGCHAIN_API_KEY"))
        self.assertIn("LANGSMITH_API_KEY ignored", t["message"])
        os.environ.pop("LANGCHAIN_API_KEY")
        self.assertFalse(tracing.settings()["on"])


class FakeLangSmith(BaseHTTPRequestHandler):
    """Takes LangSmith's requests and keeps their bodies; answers uploads with `status` (403 plays a refused key)."""
    got = None
    status = 200

    def _reply(self, body=b"{}"):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._reply(b'{"version": "test", "batch_ingest_config": {"size_limit": 100, "size_limit_bytes": 20971520, '
                    b'"scale_up_qsize_trigger": 1000, "scale_up_nthreads_limit": 1, "scale_down_nempty_trigger": 4}}')

    def do_POST(self):
        self.got.append(self.rfile.read(int(self.headers.get("Content-Length") or 0)))
        if self.status != 200:
            self.send_response(self.status)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self._reply()

    do_PATCH = do_POST

    def log_message(self, *a):
        pass


@unittest.skipUnless(HAVE_LANGGRAPH, "needs LangGraph and langsmith (the WSL automc environment)")
class Conversations(Clean):
    def setUp(self):
        super().setUp()
        self.got = []
        tracing._failures.update(new=None, said=set())
        handler = self.handler = type("Handler", (FakeLangSmith,), {"got": self.got})
        self.server = HTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        os.environ.update({"AUTOMC_TRACING": "langsmith", "LANGSMITH_API_KEY": SECRET,
                           "LANGSMITH_ENDPOINT": f"http://127.0.0.1:{self.server.server_port}",
                           "LANGSMITH_DISABLE_RUN_COMPRESSION": "true"})
        self.ws = self.tmp / "ws"
        import automc_settings
        from agent import guard
        self.patches = [mock.patch.object(automc_paths, "WORKSPACE", self.ws),
                        # facility mode, whether or not a restricted pack is installed here: unknown data counts as not public
                        mock.patch.object(automc_settings, "restricted_pack_installed", return_value=True),
                        mock.patch.object(guard, "restricted_pack_installed", return_value=True)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for c in tracing._clients.values():
            c.flush()
        tracing._clients.clear()
        for p in self.patches:
            p.stop()
        self.server.shutdown()
        self.server.server_close()
        super().tearDown()

    def turn(self, replies, text, provider=None, pack_installed=False, result=None):
        model = Quiet(replies=list(replies), seen=[])
        with mock.patch.object(guard, "restricted_pack_installed", return_value=pack_installed):
            s = automc_core.Session(provider=provider or LOCAL, model=model)
            if result is None:
                evs = list(s.send(text))
            else:
                with mock.patch.object(automc_core.registry, "call", return_value=result):
                    evs = list(s.send(text))
        for c in tracing._clients.values():
            c.flush()
        return s, evs, b"".join(self.got)

    def notices(self, evs):
        return [e[1] for e in evs if e[0] == "notice"]

    def audited(self, s):
        return [json.loads(line).get("traced") for line in s.audit_path.read_text(encoding="utf-8").splitlines()
                if json.loads(line)["event"] == "assistant"]

    def test_a_public_conversation_is_traced_with_its_thread(self):
        s, evs, body = self.turn([AIMessage(content="ANSWER-PUBLIC")], "QUESTION-PUBLIC")
        self.assertEqual(evs[-1], ("final", "ANSWER-PUBLIC"))
        for marker in (b"QUESTION-PUBLIC", b"ANSWER-PUBLIC", s.thread_id.encode()):
            self.assertIn(marker, body)
        self.assertNotIn(SECRET.encode(), body)
        self.assertIn("traced to project automc", " ".join(self.notices(evs)))
        self.assertEqual(self.audited(s), [True])
        self.assertEqual({os.environ[sw] for sw in tracing.SWITCHES}, {"false"})

    def test_tracing_stops_when_restricted_or_unknown_data_enters(self):
        import automc_settings
        os.environ[automc_settings.USER_ENV] = str(self.tmp / "no-user-settings.yaml")
        automc_settings.write({"mode": "facility"})          # unknown data is not public in facility mode (WD-2), no pack needed
        for label, data in (("restricted", "restricted"), ("unknown", None)):
            with self.subTest(label=label):
                self.got.clear()
                result = {"ok": True, "jobs": [{"case": f"ROW-{label.upper()}"}], "facility_data": data}
                s, evs, body = self.turn([call("OpenMCJobListTool", {}), AIMessage(content=f"AFTER-{label.upper()}")],
                                         f"QUESTION-{label.upper()}", result=result)
                self.assertEqual(s.conversation_label(), label)
                self.assertIn(f"ROW-{label.upper()}", json.dumps([m.content for m in s.messages()]))   # the model did read it
                self.assertIn(f"QUESTION-{label.upper()}".encode(), body)        # the first call was public
                self.assertNotIn(f"ROW-{label.upper()}".encode(), body)
                self.assertNotIn(f"AFTER-{label.upper()}".encode(), body)
                self.assertEqual(self.audited(s), [True, False])
                self.assertIn(f"not traced, because this conversation holds {label} data", " ".join(self.notices(evs)))

    def test_a_local_model_is_not_traced_while_a_restricted_pack_is_installed(self):
        s, evs, body = self.turn([AIMessage(content="ANSWER-LOCAL")], "QUESTION-LOCAL", pack_installed=True)
        self.assertEqual(body, b"")
        self.assertIn("restricted facility pack", " ".join(self.notices(evs)))
        self.assertEqual(self.audited(s), [False])

    def test_a_cloud_model_is_traced_while_a_restricted_pack_is_installed(self):
        s, evs, body = self.turn([AIMessage(content="ANSWER-CLOUD")], "QUESTION-CLOUD", provider=CLOUD, pack_installed=True)
        self.assertIn(b"QUESTION-CLOUD", body)
        self.assertEqual(self.audited(s), [True])

    def test_nothing_is_traced_or_said_unless_asked_for(self):
        os.environ.pop("AUTOMC_TRACING")
        s, evs, body = self.turn([AIMessage(content="ANSWER-OFF")], "QUESTION-OFF")
        self.assertEqual((body, self.notices(evs), self.audited(s)), (b"", [], [False]))

    def test_what_langsmith_gets_about_a_model_holds_no_key(self):
        # A traced call sends the model's serialization and its invocation parameters (langchain_core chat_models.py,
        # on_chat_model_start); neither may carry the key.
        backends = [("openai_compatible", "https://api.openai.invalid/v1", "gpt-5.4"),
                    ("google_genai", "https://generativelanguage.googleapis.com/v1beta", "gemini-3-flash"),
                    ("anthropic", "https://api.anthropic.invalid", "claude-x")]
        checked = 0
        with mock.patch.dict(os.environ, {"AUTOMC_T_KEY": SECRET}):
            for backend, url, model in backends:
                if importlib.util.find_spec(llm.BACKENDS[backend]) is None:
                    continue
                with self.subTest(backend=backend):
                    p = llm.Provider(name="t", kind="cloud", account="enterprise", backend=backend, base_url=url, model=model,
                                     api_key_env="AUTOMC_T_KEY", headers={"User-Agent": "AutoMC"})
                    chat = llm.chat_model(p)
                    sent = json.dumps(dumpd(chat), default=str) + json.dumps(chat._get_invocation_params(), default=str) + repr(chat)
                    self.assertNotIn(SECRET, sent)
                    checked += 1
        self.assertGreaterEqual(checked, 2)

    def test_a_refused_upload_is_told_once_in_one_sentence(self):
        self.handler.status = 403
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.turn([AIMessage(content="first")], "QUESTION-1")
            model = Quiet(replies=[AIMessage(content="second"), AIMessage(content="third")], seen=[])
            with mock.patch.object(guard, "restricted_pack_installed", return_value=False):
                s = automc_core.Session(provider=LOCAL, model=model)
                second = list(s.send("QUESTION-2"))
                for c in tracing._clients.values():
                    c.flush()
                third = list(s.send("QUESTION-3"))
        told = [n for n in self.notices(second + third) if "refused the traces" in n]
        self.assertEqual(len(told), 1)
        self.assertIn("HTTP 403: the key was refused", told[0])
        self.assertIn("LANGSMITH_API_KEY", told[0])
        self.assertNotIn(SECRET, told[0])
        self.assertNotIn("Failed to", err.getvalue())                          # LangSmith's own log lines are not printed
        self.assertEqual(third[-1], ("final", "third"))

    def test_asked_for_without_a_key_says_so_once(self):
        os.environ.pop("LANGSMITH_API_KEY")
        s, evs, body = self.turn([call("OpenMCJobListTool", {}), AIMessage(content="done")], "QUESTION-NOKEY")
        self.assertEqual(body, b"")
        self.assertEqual(len([n for n in self.notices(evs) if "no key" in n]), 1)


if __name__ == "__main__":
    unittest.main()
