"""
GT-4 (devops/AutoMC_General_Tools_Plan.md): the web tools, without the network. Tavily's answers, the sites' responses and the DNS are
faked, so every rule is checked: what may leave a public, an unknown and a restricted conversation; the 'yes, send' question; Tavily's
errors; the counts; only the public internet (each redirect checked); pages, PDFs and paging; the key never in a result.
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
from tools import conversation, registry
from tools.files.places import Refused
from tools.web import fetch, gate, search

FAKE_KEY = "tvly-test-not-a-real-key-0123456789"
HAVE_BS4 = importlib.util.find_spec("bs4") is not None
HAVE_PYPDF = importlib.util.find_spec("pypdf") is not None


class FakeResponse:
    def __init__(self, status=200, body=b"", ctype="text/html", headers=None, data=None):
        self.status_code, self._body, self.encoding = status, body, "utf-8"
        self.headers = {"content-type": ctype, **(headers or {})}
        self._data = data
        self.is_redirect = status in (301, 302, 303, 307, 308)

    def json(self):
        return self._data

    def iter_content(self, n):
        for i in range(0, len(self._body), n):
            yield self._body[i:i + n]

    def close(self):
        pass


def public_dns(host, port):
    return [(2, 1, 6, "", ("93.184.216.34", port))]


TAVILY = {"answer": "Promethium-147 has a half-life of 2.62 years.",
          "results": [{"title": "Isotopes of promethium", "url": "https://en.wikipedia.org/wiki/Isotopes_of_promethium",
                       "content": "x" * 2000, "score": 0.91234}]}


class Base(unittest.TestCase):
    def setUp(self):
        self.ws = Path(tempfile.mkdtemp(prefix="automc_web_")).resolve()
        import automc_settings
        from agent import guard
        self.patches = [mock.patch.object(automc_paths, "WORKSPACE", self.ws), mock.patch.dict(os.environ),
                        mock.patch.object(fetch.socket, "getaddrinfo", side_effect=public_dns),
                        # facility mode, whether or not a restricted pack is installed here: data of unknown origin exists
                        mock.patch.object(automc_settings, "restricted_pack_installed", return_value=True),
                        mock.patch.object(guard, "restricted_pack_installed", return_value=True)]
        for p in self.patches:
            p.start()
        os.environ["AUTOMC_TEST_TAVILY"] = FAKE_KEY
        from agent import keys
        real = keys.value
        self.key = mock.patch.object(keys, "value", side_effect=lambda n: FAKE_KEY if n == search.KEY else real(n))
        self.key.start()
        search._PER_CONVERSATION.clear()

    def tearDown(self):
        self.key.stop()
        for p in reversed(self.patches):
            p.stop()
        shutil.rmtree(self.ws, ignore_errors=True)


class Gate(Base):
    def test_a_public_conversation_searches_at_once(self):
        with mock.patch.object(search, "_post", return_value=FakeResponse(data=TAVILY)) as post, \
                conversation.inside(None, "public", "t1"):
            out = registry.call("WebSearchTool", {"query": "half-life of promethium-147"})
        self.assertTrue(out["ok"], out)
        self.assertEqual((out["facility_data"], out["answer"][:13]), ("public", "Promethium-14"))
        self.assertEqual(len(out["results"][0]["snippet"]), search.SNIPPET + 3)          # cut as AutoSAM cuts it
        self.assertEqual(out["results"][0]["score"], 0.912)
        self.assertEqual(post.call_args.args[0]["search_depth"], "basic")
        self.assertNotIn(FAKE_KEY, json.dumps(out))

    def test_an_unknown_conversation_asks_and_only_yes_send_sends(self):
        with mock.patch.object(search, "_post", return_value=FakeResponse(data=TAVILY)) as post, \
                conversation.inside(None, "unknown", "t1"):
            out = registry.call("WebSearchTool", {"query": "half-life of promethium-147"})
            self.assertEqual((out["needs_approval"], out["plan"]["needs_send"], out["facility_data"]), (True, True, "public"))
            self.assertIn("type 'yes, send'", " ".join(out["plan"]["text"].split()))
            post.assert_not_called()
            for send in (False,):
                with self.assertRaises(Refused):
                    gate.start(out["plan"], approved_by="chat", send=send)
            post.assert_not_called()
            got = gate.start(out["plan"], approved_by="chat", send=True)
        self.assertEqual(got["results"][0]["title"], "Isotopes of promethium")
        post.assert_called_once()

    def test_a_restricted_conversation_never_sends_with_any_model(self):
        with mock.patch.object(search, "_post") as post, mock.patch("requests.get") as get, \
                conversation.inside(None, "restricted", "t1"):
            s = registry.call("WebSearchTool", {"query": "rod worth of the NETL reactor"})
            f = registry.call("WebFetchTool", {"url": "https://example.org/a"})
        for out in (s, f):
            self.assertFalse(out["ok"])
            self.assertEqual(out["error"]["type"], "NotAllowed")
        post.assert_not_called()
        get.assert_not_called()

    def test_outside_a_conversation_the_user_typed_it(self):
        with mock.patch.object(search, "_post", return_value=FakeResponse(data=TAVILY)):
            self.assertTrue(registry.call("WebSearchTool", {"query": "promethium"})["ok"])


class Search(Base):
    def test_tavily_errors_say_what_happened(self):
        for status, kind in ((401, "KeyRefused"), (432, "QuotaSpent"), (429, "QuotaSpent"), (500, "Unavailable")):
            with self.subTest(status=status), mock.patch.object(search, "_post", return_value=FakeResponse(status=status)):
                out = registry.call("WebSearchTool", {"query": "promethium"})
                self.assertEqual(out["error"]["type"], kind)
        with mock.patch.object(search, "_post", side_effect=OSError("no route")):
            self.assertEqual(registry.call("WebSearchTool", {"query": "x y"})["error"]["type"], "Unavailable")
        self.key.stop()
        with mock.patch("agent.keys.value", return_value=""):
            self.assertEqual(registry.call("WebSearchTool", {"query": "x y"})["error"]["type"], "MissingKey")
        self.key.start()

    def test_searches_are_counted_per_conversation_and_month(self):
        with mock.patch.object(search, "_post", return_value=FakeResponse(data=TAVILY)):
            with conversation.inside(None, "public", "a"):
                search.search("one")
                second = search.search("two")
            with conversation.inside(None, "public", "b"):
                other = search.search("three")
        self.assertEqual((second["searches"]["this_conversation"], second["searches"]["this_month"]), (2, 2))
        self.assertEqual((other["searches"]["this_conversation"], other["searches"]["this_month"]), (1, 3))
        usage = json.loads((self.ws / search.WEB / "usage.json").read_text())
        self.assertEqual(sum(usage.values()), 3)


@unittest.skipUnless(HAVE_BS4, "needs beautifulsoup4")
class Fetch(Base):
    def test_only_the_public_internet(self):
        private = {"localhost": "127.0.0.1", "ollama.local": "10.1.2.3", "router": "192.168.1.1", "meta": "169.254.169.254",
                   "six": "::1"}
        dns = lambda host, port: [(2, 1, 6, "", (private.get(host, "93.184.216.34"), port))]
        with mock.patch.object(fetch.socket, "getaddrinfo", side_effect=dns):
            for host in private:
                with self.subTest(host=host):
                    out = registry.call("WebFetchTool", {"url": f"http://{host}:11434/api/tags"})
                    self.assertEqual(out["error"]["type"], "NotAllowed", out)
            for url in ("file:///etc/passwd", "ftp://example.org/x"):
                self.assertEqual(registry.call("WebFetchTool", {"url": url})["error"]["type"], "InvalidArgument")
            hop = FakeResponse(status=302, headers={"location": "http://router/admin"})
            with mock.patch("requests.get", side_effect=[hop]) as get:
                out = registry.call("WebFetchTool", {"url": "https://example.org/go"})
            self.assertEqual(out["error"]["type"], "NotAllowed")                             # the redirect is checked too
            self.assertEqual(get.call_count, 1)

    def test_a_page_as_text_paged(self):
        html = (b"<html><head><title>Pm</title><script>var key='x'</script><style>p{}</style></head><body><nav>menu</nav>"
                b"<p>Promethium-147 decays to samarium-147.</p>" + b"<p>more text</p>" * 3000 + b"</body></html>")
        with mock.patch("requests.get", return_value=FakeResponse(body=html)):
            out = registry.call("WebFetchTool", {"url": "https://example.org/pm", "max_chars": 1000})
        self.assertTrue(out["ok"], out)
        self.assertEqual((out["title"], out["facility_data"], out["start"]), ("Pm", "public", 0))
        self.assertIn("decays to samarium-147", out["text"])
        self.assertNotIn("var key", out["text"])
        self.assertNotIn("menu", out["text"])
        self.assertEqual(len(out["text"]), 1000)
        with mock.patch("requests.get", return_value=FakeResponse(body=html)):
            nxt = registry.call("WebFetchTool", {"url": "https://example.org/pm", "max_chars": 1000, "start": out["next_start"]})
        self.assertEqual(nxt["start"], 1000)

    @unittest.skipUnless(HAVE_PYPDF, "needs pypdf")
    def test_a_pdf_is_saved_for_a_knowledge_base_and_read_as_text(self):
        from pypdf import PdfWriter
        buf = io.BytesIO()
        w = PdfWriter()
        w.add_blank_page(width=612, height=792)
        w.write(buf)
        with mock.patch("requests.get", return_value=FakeResponse(body=buf.getvalue(), ctype="application/pdf")):
            out = registry.call("WebFetchTool", {"url": "https://www.osti.gov/servlets/purl/1718987"})
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["saved_to"], f"{search.WEB}/downloads/www.osti.gov/1718987.pdf")
        self.assertTrue((self.ws / out["saved_to"]).is_file())
        self.assertIn("[page 1]", out["text"])
        self.assertIn("DocumentParseTool", out["next"])

    def test_other_content_is_refused(self):
        with mock.patch("requests.get", return_value=FakeResponse(body=b"\x00\x01", ctype="application/zip")):
            self.assertEqual(registry.call("WebFetchTool", {"url": "https://example.org/a.zip"})["error"]["type"], "NotAllowed")
        with mock.patch("requests.get", return_value=FakeResponse(status=404)):
            self.assertEqual(registry.call("WebFetchTool", {"url": "https://example.org/gone"})["error"]["type"], "Unavailable")


@unittest.skipUnless(importlib.util.find_spec("langgraph") is not None, "needs LangGraph")
class Agent(Base):
    def session(self, replies, first_label):
        from langchain_core.messages import AIMessage

        import automc_core
        from tests.agent.test_core import LOCAL, Scripted, call
        local = LOCAL
        real = registry.call

        def fake(name, args=None):
            if name == "FileReadTool":
                return {"ok": True, "text": "notes", "facility_data": first_label}
            return real(name, args)
        model = Scripted(replies=[call("FileReadTool", {"path": "notes.txt"})] + replies + [AIMessage(content="done")], seen=[])
        p = mock.patch.object(automc_core.registry, "call", side_effect=fake)
        p.start()
        self.addCleanup(p.stop)
        return automc_core.Session(provider=local, model=model, audit=False)

    def test_after_unknown_data_the_chat_asks_for_yes_send(self):
        from tests.agent.test_core import call
        with mock.patch.object(search, "_post", return_value=FakeResponse(data=TAVILY)) as post:
            s = self.session([call("WebSearchTool", {"query": "promethium half-life"})], "unknown")
            evs = list(s.send("read my notes, then search the web"))
            self.assertEqual((evs[-1][0], evs[-1][1]["kind"], evs[-1][1]["needs_send"]), ("approval", "web_approval", True))
            list(s.answer(True, send=False))                                                  # a plain yes is not enough
            post.assert_not_called()
            s = self.session([call("WebSearchTool", {"query": "promethium half-life"})], "unknown")
            list(s.send("read my notes, then search the web"))
            list(s.answer(True, send=True))
        post.assert_called_once()

    def test_after_restricted_data_nothing_is_sent_or_asked(self):
        from tests.agent.test_core import call
        with mock.patch.object(search, "_post") as post:
            s = self.session([call("WebSearchTool", {"query": "promethium half-life"})], "restricted")
            evs = list(s.send("read my notes, then search the web"))
        self.assertNotIn("approval", [e[0] for e in evs])
        post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
