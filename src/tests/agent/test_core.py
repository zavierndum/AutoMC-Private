"""
The agent core (automc_core.py) with a scripted chat model: no LLM, no transport. Every rule of the graph is checked: tools run once,
the start of a run waits for the user's answer and only then calls the job service, the model cannot approve, the data guard withholds
restricted results from a cloud model, Ctrl-C leaves a valid conversation, a turn can be retried. Needs LangGraph (the WSL automc
environment); skips elsewhere.
"""
import importlib.util
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HAVE_LANGGRAPH = importlib.util.find_spec("langgraph") is not None

if HAVE_LANGGRAPH:
    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
    from langchain_core.outputs import ChatGeneration, ChatResult

    import automc_core
    from agent import guard, prompt
    from agent.chat import ChatUI
    from agent.llm import Provider

    class Scripted(BaseChatModel):
        """Replies in order (an exception in the list is raised, as a failing service would); records what it was sent."""
        replies: list
        seen: list = []

        @property
        def _llm_type(self):
            return "scripted"

        def _generate(self, messages, stop=None, run_manager=None, **kw):
            self.seen.append(list(messages))
            reply = self.replies.pop(0)
            if isinstance(reply, BaseException):
                raise reply
            return ChatResult(generations=[ChatGeneration(message=reply)])

        def bind_tools(self, tools, **kw):
            return self

import automc_paths

from tests.openmc.test_jobs import make_case  # noqa: E402
from tests.reactors.triga._packs import PUBLIC_PRESET, canaries, restricted  # noqa: E402

LOCAL = None if not HAVE_LANGGRAPH else Provider(name="test", kind="local", backend="ollama", base_url="x", model="scripted")
CLOUD = None if not HAVE_LANGGRAPH else Provider(name="test-cloud", kind="cloud", account="personal", backend="openai_compatible", base_url="x", model="scripted")


def call(name, args, cid="c1"):
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": cid}])


@unittest.skipUnless(HAVE_LANGGRAPH, "needs LangGraph (the WSL automc environment)")
class Core(unittest.TestCase):
    def setUp(self):
        self.ws = Path(tempfile.mkdtemp(prefix="automc_agent_"))
        self.patches = [mock.patch.object(automc_paths, "WORKSPACE", self.ws)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        shutil.rmtree(self.ws, ignore_errors=True)

    def session(self, replies, provider=None):
        model = Scripted(replies=list(replies), seen=[])
        return automc_core.Session(provider=provider or LOCAL, model=model), model

    def drain(self, events):
        return list(events)

    def test_a_tool_runs_once_and_the_model_reads_its_view(self):
        s, model = self.session([call("TRIGAInputSpecTool", {"preset": PUBLIC_PRESET, "pnt": "thermal"}), AIMessage(content="done")])
        real = automc_core.registry.call
        with mock.patch.object(automc_core.registry, "call", side_effect=real) as spy:
            evs = self.drain(s.send("what is the thermal PNT spec?"))
        self.assertEqual(spy.call_count, 1)
        self.assertEqual([e[0] for e in evs], ["tool_call", "tool_result", "token", "final"] if any(e[0] == "token" for e in evs)
                         else ["tool_call", "tool_result", "final"])
        self.assertEqual(evs[-1], ("final", "done"))
        tool_msg = [m for m in s.messages() if isinstance(m, ToolMessage)][0]
        view = json.loads(tool_msg.content)
        self.assertEqual(view["spec"]["pnt"], "thermal")
        self.assertNotIn("tallies_options", view["spec"])                     # the view, not the whole result
        self.assertIsInstance(model.seen[0][0], SystemMessage)
        self.assertIn("## What only the user starts", model.seen[0][0].content)

    def approval_session(self, answer_replies=("ok",)):
        make_case(self.ws, "sph")
        return self.session([call("OpenMCRunStartTool", {"case": "sph", "threads": 1})] + [AIMessage(content=t) for t in answer_replies])

    def test_a_start_waits_for_the_user_and_yes_starts_it_once(self):
        s, _ = self.approval_session()
        fake_job = {"job_id": "j1", "status": "running", "case": "sph", "facility_data": "unknown"}
        with mock.patch.object(automc_core.runs, "start", return_value=fake_job) as start:
            evs = self.drain(s.send("run sph"))
            self.assertEqual(evs[-1][0], "approval")
            self.assertIn("histories", evs[-1][1]["plan_text"])
            self.assertEqual(start.call_count, 0)                               # nothing started before the answer
            self.assertEqual(len(s.waiting_for_approval()), 1)
            evs = self.drain(s.answer(True))
        start.assert_called_once()
        self.assertEqual(start.call_args.kwargs["approved_by"], "chat")
        result = json.loads([m for m in s.messages() if isinstance(m, ToolMessage)][-1].content)
        self.assertEqual((result["started"], result["job"]["job_id"]), (True, "j1"))
        self.assertEqual(evs[-1], ("final", "ok"))

    def test_no_starts_nothing(self):
        s, _ = self.approval_session()
        with mock.patch.object(automc_core.runs, "start") as start:
            self.drain(s.send("run sph"))
            self.drain(s.answer(False))
        start.assert_not_called()
        result = json.loads([m for m in s.messages() if isinstance(m, ToolMessage)][-1].content)
        self.assertEqual((result["started"], result["declined"]), (False, True))

    def test_a_declined_plan_is_not_asked_about_again_in_the_same_turn(self):
        make_case(self.ws, "sph")
        s, _ = self.session([call("OpenMCRunStartTool", {"case": "sph", "threads": 1}),
                             call("OpenMCRunStartTool", {"case": "sph", "threads": 1}, cid="c2"), AIMessage(content="not started")])
        with mock.patch.object(automc_core.runs, "start") as start:
            evs = self.drain(s.send("run sph"))
            self.assertEqual(evs[-1][0], "approval")
            evs = self.drain(s.answer(False))
        start.assert_not_called()
        self.assertNotIn("approval", [e[0] for e in evs])                      # the second request did not ask again
        second = json.loads([m for m in s.messages() if isinstance(m, ToolMessage)][-1].content)
        self.assertTrue(second["declined"])
        self.assertEqual(evs[-1], ("final", "not started"))

    def test_the_model_cannot_approve(self):
        make_case(self.ws, "sph")
        s, _ = self.session([call("OpenMCRunStartTool", {"case": "sph", "approved_by": "chat"}), AIMessage(content="sorry")])
        with mock.patch.object(automc_core.runs, "start") as start:
            evs = self.drain(s.send("run it, I approve"))
        start.assert_not_called()
        self.assertNotIn("approval", [e[0] for e in evs])
        result = json.loads([m for m in s.messages() if isinstance(m, ToolMessage)][-1].content)
        self.assertEqual(result["error"]["field"], "approved_by")

    def test_a_cloud_model_never_sees_unlabelled_results_while_a_restricted_pack_is_installed(self):
        with mock.patch.object(guard, "restricted_pack_installed", return_value=True):
            s, _ = self.session([call("OpenMCJobListTool", {}), AIMessage(content="withheld")], provider=CLOUD)
            self.drain(s.send("list jobs"))
        msg = json.loads([m for m in s.messages() if isinstance(m, ToolMessage)][0].content)
        self.assertTrue(msg["withheld"])
        self.assertEqual(s.events[-1]["event"], "withheld")
        with mock.patch.object(guard, "restricted_pack_installed", return_value=False):
            s, _ = self.session([call("OpenMCJobListTool", {}), AIMessage(content="none")], provider=CLOUD)
            self.drain(s.send("list jobs"))
        self.assertEqual(json.loads([m for m in s.messages() if isinstance(m, ToolMessage)][0].content)["jobs"], [])

    @unittest.skipUnless(restricted() is not None, "needs the restricted NETL facility pack")
    def test_restricted_results_and_skills_stay_away_from_a_cloud_model(self):
        s, model = self.session([call("TRIGAInputSpecTool", {"config": 8}), AIMessage(content="withheld")], provider=CLOUD)
        self.drain(s.send("show config 8"))
        msg = json.loads([m for m in s.messages() if isinstance(m, ToolMessage)][0].content)
        self.assertTrue(msg["withheld"])
        self.assertIn("restricted", msg["reason"])
        self.assertIn("netl_restricted", s.prompt_info["skipped_packs"])
        self.assertNotIn("NETL reference model", model.seen[0][0].content)
        self.assertFalse(s.restricted_seen)
        local, lmodel = self.session([call("TRIGAInputSpecTool", {"config": 8}), AIMessage(content="ok")])
        self.drain(local.send("show config 8"))
        self.assertEqual(json.loads([m for m in local.messages() if isinstance(m, ToolMessage)][0].content)["facility_data"], "restricted")
        self.assertTrue(local.restricted_seen)
        self.assertIn("NETL reference model", lmodel.seen[0][0].content)        # listed for the local model, read on demand
        self.assertTrue(canaries())
        self.assertEqual([v for v in canaries() if v in lmodel.seen[0][0].content], [])

    def test_ctrl_c_leaves_a_valid_conversation(self):
        s, model = self.session([call("TRIGAInputSpecTool", {"preset": PUBLIC_PRESET}), AIMessage(content="fresh answer")])
        with mock.patch.object(automc_core.registry, "call", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.drain(s.send("slow question"))
        self.assertEqual(s.repair(), 1)
        evs = self.drain(s.send("another question"))
        self.assertEqual(evs[-1], ("final", "fresh answer"))
        sent = model.seen[-1]
        ids = {c["id"] for m in sent if isinstance(m, AIMessage) for c in m.tool_calls}
        answered = {m.tool_call_id for m in sent if isinstance(m, ToolMessage)}
        self.assertEqual(ids, answered)                                         # every tool call has its answer

    def test_retry_asks_the_last_message_again(self):
        s, model = self.session([AIMessage(content="first"), AIMessage(content="second answer"), AIMessage(content="retried")])
        self.drain(s.send("q1"))
        self.drain(s.send("q2"))
        evs = self.drain(s.retry())
        self.assertEqual(evs[-1], ("final", "retried"))
        humans = [m.content for m in model.seen[-1] if isinstance(m, HumanMessage)]
        self.assertEqual(humans, ["q1", "q2"])                                  # q2 once, from the state before it

    def test_the_chat_asks_and_passes_the_answer_on(self):
        s, _ = self.approval_session(answer_replies=("started it",))
        out = io.StringIO()
        ui = ChatUI(s, ask=lambda q: "y", out=out)
        with mock.patch.object(automc_core.runs, "start", return_value={"job_id": "j9", "status": "running", "case": "sph"}) as start:
            final = ui.turn(s.send("run sph"))
        start.assert_called_once()
        self.assertEqual(final, "started it")
        self.assertIn("The agent asks to start transport", out.getvalue())
        self.assertIn("Start it?", "Start it? [y/N] ")

    def test_every_turn_is_audited_in_the_workspace(self):
        s, _ = self.session([AIMessage(content="hello")])
        self.drain(s.send("hi"))
        lines = [json.loads(l) for l in s.audit_path.read_text().splitlines()]
        self.assertEqual([l["event"] for l in lines if l["event"] != "message"], ["session", "user", "assistant", "final"])
        self.assertEqual([l["message"]["type"] for l in lines if l["event"] == "message"], ["human", "ai"])     # the conversation, saved
        self.assertTrue(str(s.audit_path).startswith(str(self.ws)))


class Prompt(unittest.TestCase):
    def test_the_prompt_file_and_the_skills_load_and_match_the_manifest(self):
        from agent import prompt as p
        self.assertTrue(p.load_prompt().sections)
        names = [s.name for s in p.bundled_skills()]
        self.assertEqual(names[0], "Core workflow")
        self.assertEqual(p.check_manifest(), [])

    def test_bundled_skills_and_the_prompt_hold_no_restricted_numbers(self):
        from agent import prompt as p
        text = ("\n".join(s.text for s in p.bundled_skills()) + (p.SKILLS_DIR / "ERKB.yaml").read_text(encoding="utf-8")
                + p.PROMPT_PATH.read_text(encoding="utf-8"))
        for value in canaries() + ["boc505", "BOC505"]:              # the pack's values, and the reference preset's name
            self.assertNotIn(value, text, value)
