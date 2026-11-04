"""
Saved conversations (WD-3, devops/AutoMC_Workspace_Plan.md section 5): one JSONL file per conversation, named by a UUID, written as the
conversation goes and also its audit; reloaded whole and continued; restricted content not saved unless the workspace's settings say so;
a reloaded restricted conversation refused to a cloud model, one of unknown origin only after 'yes, send'; a retry reflected; a call of
a tool that no longer exists turned into text; linked workspaces' conversations listed and copied, never changed; past turns found by
their words with their labels; and every model call to a cloud service, web request and trace written to egress.jsonl without content.
"""
import importlib.util
import io
import json
import os
import shutil
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest import mock

import automc_paths
import automc_settings

HAVE_LANGGRAPH = importlib.util.find_spec("langgraph") is not None


@unittest.skipUnless(HAVE_LANGGRAPH, "needs LangGraph")
class Base(unittest.TestCase):
    facility = False

    def setUp(self):
        from agent import guard
        self.ws = Path(tempfile.mkdtemp(prefix="automc_chats_")).resolve()
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

    def session(self, replies, provider=None, **kw):
        import automc_core
        from tests.agent.test_core import LOCAL, Scripted
        model = Scripted(replies=list(replies), seen=[])
        return automc_core.Session(provider=provider or LOCAL, model=model, **kw), model

    @staticmethod
    def drain(events):
        return list(events)

    def lines(self, s):
        return [json.loads(l) for l in s.chat.path.read_text(encoding="utf-8").splitlines()]


class Saving(Base):
    def test_a_conversation_is_saved_as_it_goes_and_an_empty_one_leaves_nothing(self):
        from langchain_core.messages import AIMessage
        from tools.chats import store
        empty, _ = self.session([])
        self.assertFalse(empty.chat.path.exists())
        s, _ = self.session([AIMessage(content="k-eff is about 1.0")])
        uuid.UUID(s.thread_id)                                                               # a full random UUID
        self.drain(s.send("what is k-eff of the sphere?"))
        lines = self.lines(s)
        self.assertEqual(s.chat.path, self.ws / ".automc" / "chats" / f"{s.thread_id}.jsonl")
        meta = lines[0]
        self.assertEqual((meta["event"], meta["id"], meta["format"]), ("session", s.thread_id, store.FORMAT))
        for key in ("automc_version", "fingerprint", "user", "provider_label", "created_at"):
            self.assertIn(key, meta)
        self.assertEqual([l["event"] for l in lines if l["event"] != "message"], ["session", "user", "assistant", "final"])
        kinds = [l["message"]["type"] for l in lines if l["event"] == "message"]
        self.assertEqual(kinds, ["human", "ai"])
        row = store.list_chats()[0]
        self.assertEqual((row["title"], row["turns"], row["label"], row["where"]), ("what is k-eff of the sphere?", 1, "public",
                                                                                     "this workspace"))

    def test_restricted_content_is_not_saved_unless_the_settings_say_so(self):
        from langchain_core.messages import AIMessage
        from tests.agent.test_core import call
        from tools import registry
        restricted = {"ok": True, "facility_data": "restricted", "shim_1": 23.8}
        for save, expect_saved in ((None, False), ("true", True)):
            with self.subTest(save=save):
                if save:
                    automc_settings.write({"conversations": {"save_restricted": True}})
                with mock.patch.object(registry, "call", return_value=restricted):
                    s, _ = self.session([call("FileReadTool", {"path": "netl.txt"}), AIMessage(content="shim 1 is at 23.8 cm")])
                    self.drain(s.send("where is shim 1?"))
                text = s.chat.path.read_text(encoding="utf-8")
                self.assertIn("where is shim 1?", text)                                       # what came before stays
                self.assertEqual("23.8" in text, expect_saved)
                self.assertEqual('"not_saved"' in text, not expect_saved)
                if not expect_saved:
                    final = [l for l in self.lines(s) if l["event"] == "final"][0]
                    self.assertEqual(final.get("content"), "not saved")


class Loading(Base):
    def conversation(self, label="public"):
        from langchain_core.messages import AIMessage
        from tests.agent.test_core import call
        from tools import registry
        with mock.patch.object(registry, "call", return_value={"ok": True, "facility_data": label, "value": 7}):
            s, _ = self.session([call("FileReadTool", {"path": "x.txt"}), AIMessage(content="it says 7")])
            self.drain(s.send("read x.txt"))
        return s.thread_id

    def test_a_saved_conversation_is_continued_whole(self):
        import automc_core
        from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
        from tests.agent.test_core import LOCAL, Scripted
        chat_id = self.conversation()
        model = Scripted(replies=[AIMessage(content="still 7")], seen=[])
        s, out = automc_core.load_saved(chat_id, provider=LOCAL, model=model)
        self.assertTrue(out["loaded"], out)
        self.assertEqual(s.thread_id, chat_id)
        self.drain(s.send("and now?"))
        sent = model.seen[-1]
        self.assertEqual([m.content for m in sent if isinstance(m, HumanMessage)], ["read x.txt", "and now?"])
        self.assertTrue(any(isinstance(m, ToolMessage) for m in sent))
        lines = [json.loads(l) for l in s.chat.path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(sum(1 for l in lines if l["event"] == "session"), 1)                  # one file, one session line
        self.assertIn("resumed", [l["event"] for l in lines])
        self.assertEqual(sum(1 for l in lines if l["event"] == "user"), 2)

    def test_restricted_is_refused_to_a_cloud_model_and_unknown_needs_yes_send(self):
        import automc_core
        from agent import guard
        from tests.agent.test_core import CLOUD, LOCAL, Scripted
        with mock.patch.object(automc_settings, "restricted_pack_installed", return_value=True), \
                mock.patch.object(guard, "restricted_pack_installed", return_value=True):            # facility mode
            secret = self.conversation("restricted")
            unknown = self.conversation("unknown")
            s, out = automc_core.load_saved(secret, provider=CLOUD, model=object())
            self.assertIsNone(s)
            self.assertTrue(out["refused"])
            self.assertIn("restricted facility data", out["message"])
            s, out = automc_core.load_saved(unknown, provider=CLOUD, model=object())
            self.assertTrue(out["needs_confirmation"])
            s, out = automc_core.load_saved(unknown, provider=CLOUD, model=Scripted(replies=[], seen=[]), confirm=True)
            self.assertTrue(out["loaded"])
            self.assertEqual(s.conversation_label(), "unknown")
            s, out = automc_core.load_saved(secret, provider=LOCAL, model=Scripted(replies=[], seen=[]))
            self.assertEqual((out["loaded"], s.conversation_label()), (True, "restricted"))

    def test_a_retry_is_what_a_reload_shows(self):
        import automc_core
        from langchain_core.messages import AIMessage
        from tests.agent.test_core import LOCAL, Scripted
        s, model = self.session([AIMessage(content="a1"), AIMessage(content="a2"), AIMessage(content="a2 again")])
        self.drain(s.send("q1"))
        self.drain(s.send("q2"))
        self.drain(s.retry())
        s2, _ = automc_core.load_saved(s.thread_id, provider=LOCAL, model=Scripted(replies=[], seen=[]))
        self.assertEqual([m.content for m in s2.messages()], ["q1", "a1", "q2", "a2 again"])

    def test_a_call_of_a_tool_that_no_longer_exists_becomes_text(self):
        import automc_core
        from langchain_core.messages import AIMessage, HumanMessage, ToolMessage, message_to_dict
        msgs = [HumanMessage("q", id="1"), AIMessage(content="", tool_calls=[{"name": "OldTool", "args": {"x": 1}, "id": "c1"}], id="2"),
                ToolMessage(content='{"ok": true, "v": 5}', tool_call_id="c1", name="OldTool", id="3"), AIMessage(content="v is 5", id="4"),
                AIMessage(content="", tool_calls=[{"name": "FileReadTool", "args": {"path": "a"}, "id": "c2"}], id="5")]
        events = [{"event": "message", "message": message_to_dict(m), "label": "public"} for m in msgs]
        out, labels = automc_core.rebuild(events)
        self.assertIn("an earlier call of OldTool", out[1].content)
        self.assertFalse(out[1].tool_calls)
        self.assertIn('"v": 5', out[2].content)
        self.assertNotIsInstance(out[2], ToolMessage)
        self.assertIsInstance(out[-1], ToolMessage)                                           # the dangling call closed
        self.assertIn("Interrupted", out[-1].content)


class Linked(Base):
    def test_a_linked_workspaces_conversations_are_listed_and_copied_never_changed(self):
        from tools.chats import store
        other = self.ws.parent / f"{self.ws.name}-projA"
        try:
            d = other / ".automc" / "chats"
            d.mkdir(parents=True)
            src = d / "c0ffee00-0000-4000-8000-000000000000.jsonl"
            src.write_text("\n".join(json.dumps(e) for e in [
                {"event": "session", "id": "c0ffee00-0000-4000-8000-000000000000", "format": 1},
                {"event": "user", "text": "thermal shield thickness"}, {"event": "final", "text": "0.127 m"},
                {"event": "message", "label": "public", "message": {"type": "human", "data": {"content": "thermal shield thickness",
                                                                                              "type": "human"}}}]) + "\n",
                           encoding="utf-8")
            before = src.read_bytes()
            automc_settings.write({"links": [{"name": "projA", "path": str(other)}]})
            rows = store.list_chats()
            self.assertEqual([(r["where"], r["title"]) for r in rows], [("linked: projA", "thermal shield thickness")])
            new = store.copy_here(rows[0]["path"])
            self.assertEqual(src.read_bytes(), before)
            copied = store.read(store.folder() / f"{new}.jsonl")
            self.assertEqual((copied[0]["id"], copied[0]["copied_from"]), (new, str(src)))
            hits = store.search("thickness of the thermal shield")
            self.assertEqual({h["where"] for h in hits}, {"this workspace", "linked: projA"})
            self.assertEqual(hits[0]["answer"], "0.127 m")
            self.assertEqual(store.search("thermal shield", exclude=new)[0]["where"], "linked: projA")
        finally:
            shutil.rmtree(other, ignore_errors=True)


class SearchTool(Base):
    def test_past_turns_come_back_with_their_labels_and_not_the_current_one(self):
        from langchain_core.messages import AIMessage
        from tools import conversation, registry
        s, _ = self.session([AIMessage(content="The transient rod worth is 2.1 dollars.")])
        self.drain(s.send("what is the transient rod worth?"))
        out = registry.call("ChatSearchTool", {"query": "transient rod worth"})
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["results"][0]["answer"], "The transient rod worth is 2.1 dollars.")
        self.assertEqual(out["facility_data"], "public")
        with conversation.inside(None, "public", s.thread_id, None):
            self.assertEqual(registry.call("ChatSearchTool", {"query": "transient rod worth"})["results"], [])


class Egress(Base):
    def test_what_left_the_machine_is_recorded_without_content(self):
        from langchain_core.messages import AIMessage
        from tests.agent.test_core import CLOUD
        from tools import conversation
        from tools.chats import store
        from tools.web import search
        s, _ = self.session([AIMessage(content="the secret answer 42")], provider=CLOUD)
        self.drain(s.send("tell me the secret question"))
        fake = mock.Mock(status_code=200, json=lambda: {"results": [], "answer": None})
        with mock.patch.object(search, "_post", return_value=fake), mock.patch("agent.keys.value", return_value="tvly-test"), \
                conversation.inside(None, "public", s.thread_id, None):
            search.search("promethium-147 half-life")
        lines = store.egress_lines()
        self.assertEqual([(l["service"], l["label"], l["what"]) for l in lines],
                         [(f"{CLOUD.label} ({CLOUD.account} account)", "public", "model call"),
                          ("web search (Tavily)", "public", "a search query")])
        self.assertEqual({l["chat"] for l in lines}, {s.thread_id})
        text = (self.ws / ".automc" / "egress.jsonl").read_text(encoding="utf-8")
        for said in ("secret", "promethium"):                                                 # words, not digits: ids hold digits
            self.assertNotIn(said, text)
        store.delete(s.thread_id)
        self.assertEqual(len(store.egress_lines()), 2)                                        # deleting a chat keeps the record


class Commands(Base):
    def test_sessions_show_load_and_delete_from_the_chat(self):
        from langchain_core.messages import AIMessage
        from agent.chat import ChatUI
        from tools.chats import store
        first, _ = self.session([AIMessage(content="first answer")])
        self.drain(first.send("first question"))
        second, _ = self.session([AIMessage(content="second answer")])
        self.drain(second.send("second question"))
        out = io.StringIO()
        answers = ["y"]
        ui = ChatUI(second, ask=lambda q: answers.pop(0) if answers else "", out=out)
        ui.command("/sessions")
        self.assertIn("first question", out.getvalue())
        self.assertIn("second question", out.getvalue())
        n = next(i for i, r in enumerate(store.list_chats(), 1) if r["id"] == first.thread_id)
        ui.command(f"/show {n}")
        self.assertIn("first answer", out.getvalue())
        ui.command(f"/load {n}")
        self.assertEqual(ui.s.thread_id, first.thread_id)
        self.assertIn("Loaded: 1 earlier turn(s), public data", out.getvalue())
        ui.command(f"/delete {first.thread_id[:8]}")                                           # the current one: refused
        self.assertIn("only another conversation", out.getvalue())
        ui.command(f"/delete {second.thread_id[:8]}")                                          # another: asked, then deleted
        self.assertEqual([r["id"] for r in store.list_chats()], [first.thread_id])


if __name__ == "__main__":
    unittest.main()
