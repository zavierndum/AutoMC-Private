"""
The AutoMC agent: a LangGraph tool-calling loop over every tool in tools/registry.py, with a checkpointer (a turn can be interrupted
and resumed), the user's approval for every start of transport, and the data guard for cloud models (DESIGN.md sections 4 and 7).

    agent  ->  tools  ->  approve (only when a tool asked for approval)  ->  agent  ->  ...  ->  end

- agent: the chat model with the system prompt (agent/prompt.py) and every tool's schema.
- tools: runs each tool call exactly once through the registry, checks each full result with the data guard, and adds what the model
  may read (agent/views.py) as a ToolMessage. A plan that needs approval is held back for `approve`.
- approve: asks the surface (a LangGraph interrupt: the plan goes out, the user's answer comes back) and only then, on yes, starts the
  run through the job service with approved_by naming the surface. Nothing happens in this node before the interrupt, because LangGraph
  runs it again from the top when the answer arrives. The model has no argument with which it could approve anything.

A surface (the CLI chat, later the web UI) drives a Session: send() streams a turn, answer() resumes it after an approval question,
repair() closes a turn the user interrupted with Ctrl-C, retry() re-issues the last message.

A failed model call (a refused key, a rate limit, a timeout) ends the turn with an ("error", sentence) event and loses nothing: the
graph keeps its last checkpoint, so resume() runs the failed step again, with the same model or after a switch. Every model call's
tokens are counted per provider (spent()); a step that would take a conversation past its provider's cap (200,000 tokens for a cloud
provider) is refused before anything is sent.

With AUTOMC_TRACING=langsmith, a model call goes to LangSmith only when it could go to a cloud model as it is (agent/tracing.py): the
conversation is public and the system prompt lists no restricted pack skill. The surface is told when that starts or stops.
"""
from __future__ import annotations

import json
import operator
from pathlib import Path
from typing import Annotated, TypedDict

from langchain_core.messages import (AIMessage, AIMessageChunk, HumanMessage, SystemMessage, ToolMessage, message_to_dict,
                                     messages_from_dict)
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.types import Command, interrupt

import automc_paths
import automc_settings
import automc_version
from agent import context, discovery, errors, guard, llm as llm_mod, prompt, schemas, tracing, views
from tools import conversation, registry
from tools.chats import store as chat_store
from tools.documents import parse as doc_parse
from tools.files.places import Refused
from tools.memory import tools as memory_tools
from tools.openmc import authoring, runs
from tools.openmc.errors import OpenMCError
from tools.python import run as python_run
from tools.web import gate as web_gate

# The tools whose plans the user answers: (kind of question, how the result is shaped). "job": the start returns a job; "result": the
# start returns the tool's own result (Python output, a web page, a saved note).
APPROVALS = {"OpenMCRunStartTool": ("run_approval", "job"), "OpenMCRunResumeTool": ("run_approval", "job"),
             "DocumentParseTool": ("parse_approval", "job"), "PythonRunTool": ("python_approval", "result"),
             "WebSearchTool": ("web_approval", "result"), "WebFetchTool": ("web_approval", "result"),
             "MemoryWriteTool": ("memory_approval", "result"), "OpenMCModelBuildTool": ("build_approval", "result")}
APPROVAL_TOOLS = set(APPROVALS)
# The plans that run code and that a container's python.approval: auto lets run without asking (W2, A6)
CODE_RUNNERS = ("PythonRunTool", "OpenMCModelBuildTool")


def _start_for(name):
    """The function that carries out an approved plan, looked up when it is needed."""
    return {"DocumentParseTool": doc_parse.start, "PythonRunTool": python_run.start, "WebSearchTool": web_gate.start,
            "WebFetchTool": web_gate.start, "MemoryWriteTool": memory_tools.start,
            "OpenMCModelBuildTool": authoring.start_build}.get(name, runs.start)
RECURSION_LIMIT = 50


class AgentState(TypedDict):
    messages: Annotated[list, add_messages]
    pending: list                         # plans waiting for the user's answer: [{tool_call_id, name, plan}]
    labels: Annotated[list, operator.add]  # what each tool result put into the conversation: public, unknown, restricted


LABEL_ORDER = {"public": 0, "unknown": 1, "restricted": 2}
AFTER_ERROR = " Nothing was lost: /resume tries this step again, /retry asks your message again, /model switches."


class Session:
    def __init__(self, provider_name=None, model=None, provider=None, surface="chat", thread_id=None, audit=True, resume=False):
        """`audit`: save the conversation in the workspace (.automc/chats/<id>.jsonl, tools/chats/store.py). `resume`: `thread_id` names
        a saved conversation this session continues (load_saved)."""
        self.surface = surface
        self.restricted_seen = False                  # a restricted result has entered this conversation (local models only)
        self.events = []                              # guard decisions, approvals, switches, for the surface to show
        self.notices = []                             # one-line notices for the surface (context, switches), drained by _stream
        self.usage = {}                               # provider name -> {input, output, calls, estimated}: tokens of every model call
        self._trace_said = None                       # the last LangSmith decision the surface was told: on, off or config
        self._saved_ids = set()                       # the messages already in the conversation's file
        self.label_floor = "public"                   # a loaded conversation's label from what it held but did not save (load_saved)
        self._use(provider or discovery.resolve(provider_name), model)
        self.thread_id = thread_id or chat_store.new_id()
        self.config = {"configurable": {"thread_id": self.thread_id}, "recursion_limit": RECURSION_LIMIT}
        self.chat = self.audit_path = None
        if audit:
            self.chat = chat_store.ChatFile(self.thread_id, existing=resume, meta={
                "automc_version": automc_version.VERSION, "fingerprint": self.prompt_info.get("fingerprint"), "surface": surface,
                "provider": self.provider.full_name(), "provider_label": self.provider.label, "kind": self.provider.kind,
                "prompt": self.prompt_info})
            self.audit_path = self.chat.path
        self.graph = self._build()

    def _use(self, provider, model=None):
        """Make `provider` the session's model: its chat model with the tools bound, and the system prompt built for it."""
        self.provider = provider
        self.model = model if model is not None else llm_mod.chat_model(provider)
        definitions = schemas.definitions()
        self._params = {t["function"]["name"]: t["function"]["parameters"] for t in definitions}   # the tools' own schemas
        self.tool_defs = schemas.for_backend(definitions, provider.backend)                         # as this service takes them
        self.bound = self.model.bind_tools(self.tool_defs)
        self.system, self.prompt_info = prompt.system_prompt(provider)
        self.policy_ok = llm_mod.restricted_allowed(provider)
        self._schema_chars = len(json.dumps(self.tool_defs))

    # ---- bookkeeping -------------------------------------------------------------------------------------------------------
    def _audit(self, event, **fields):
        if self.chat is not None:
            self.chat.event(event, **fields)

    def _persist(self):
        """Append the messages not yet in the conversation's file, each with the label it put in (a tool result's own; for the rest,
        the conversation's so far)."""
        if self.chat is None:
            return
        so_far = self.conversation_label()
        if so_far == "restricted" and not chat_store.save_restricted():
            self.chat.stop_content("the conversation holds restricted data and the workspace's settings say not to save it "
                                   "(conversations.save_restricted)")
        for m in self.messages():
            if m.id in self._saved_ids:
                continue
            self._saved_ids.add(m.id)
            label = m.artifact.get("label") if isinstance(m, ToolMessage) and isinstance(m.artifact, dict) else so_far
            self.chat.message(message_to_dict(m), label or so_far)

    def _tool_message(self, call_id, name, result):
        """(ToolMessage, label it puts into the conversation)."""
        allowed, reason = guard.check(result, self.provider.cloud, self.policy_ok)
        label = sorted(guard.labels(result)) or None
        entered = guard.entered_label(result, self.provider.cloud, self.policy_ok)
        if self.provider.cloud and self.policy_ok and entered != "public":
            self._audit("policy_applied", tool=name, provider=self.provider.name, label=entered,
                        rule=f"policy.restricted_allowed_on and {llm_mod.POLICY_ENV}=1")
        shown = None
        if allowed:
            if guard.holds_restricted(result):
                self.restricted_seen = True
            content = json.dumps(views.view(name, result), default=str)
        elif name == "PythonRunTool" and isinstance(result, dict) and result.get("started"):
            # the user approved the code and it ran on their machine: the output is theirs to see, only the cloud model may not
            shown = python_run.for_the_user(result)
            content = guard.withheld(name, reason, python_run.WITHHELD_NOTE.format(file=result.get("output_file") or "its folder"))
            self.events.append({"event": "withheld", "tool": name, "reason": reason, "shown_to_user": True})
        else:
            content = guard.withheld(name, reason)
            self.events.append({"event": "withheld", "tool": name, "reason": reason})
        self._audit("tool_result", tool=name, ok=result.get("ok") if isinstance(result, dict) else None, labels=label,
                    withheld=not allowed, entered=entered, chars=len(content), shown_to_user=shown is not None)
        if entered == "restricted" and self.chat is not None and not chat_store.save_restricted():
            self.chat.stop_content("restricted data entered the conversation and the workspace's settings say not to save it "
                                   "(conversations.save_restricted)")
        # artifact: the label this result put in (for the saved conversation) and what the surface shows the user; LangChain never
        # sends a ToolMessage's artifact to a model
        return ToolMessage(content=content, tool_call_id=call_id, name=name, artifact={"label": entered, "shown": shown}), entered

    # ---- graph nodes -------------------------------------------------------------------------------------------------------
    def _agent(self, state):
        request = [SystemMessage(self.system)] + state["messages"]
        if self.provider.window:
            request, info = context.fit(request, self.provider.window, self._schema_chars)
            if info["notice"]:
                self.notices.append(info["notice"])
            if info["shortened"] or info["refuse"]:
                self._audit("context", **{k: info[k] for k in ("tokens", "window", "shortened", "refuse")})
            if info["refuse"]:
                return {"messages": [AIMessage(content=f"[AutoMC] {info['refuse']}")]}
        cap = self.provider.cap
        if cap:
            spent, need = self.spent(self.provider.name)["total"], context.estimate(request, self._schema_chars)
            if spent + need > cap:
                self._audit("cap", provider=self.provider.name, spent=spent, need=need, cap=cap)
                return {"messages": [AIMessage(content=(
                    f"[AutoMC] Not sent: this step would bring this conversation's use of {self.provider.name} to about "
                    f"{spent + need:,} tokens, over its cap of {cap:,} per conversation (max_tokens_per_session in llm.yaml). "
                    f"Switch to the local model (/model local) or start a new conversation (/new)."))]}
        so_far = max((state.get("labels") or []) + [self.label_floor], key=LABEL_ORDER.get)
        traced = self._traced(so_far)
        if self.provider.cloud:                      # what left the machine: recorded whatever becomes of the conversation's file
            chat_store.egress(f"{self.provider.label} ({self.provider.account} account)", so_far, chat=self.thread_id, what="model call")
        if traced:
            from langchain_core.runnables import ensure_config
            chat_store.egress("LangSmith", so_far, chat=self.thread_id, what="trace of a model call")
            meta = {**ensure_config().get("metadata", {}), "thread_id": self.thread_id, "automc_provider": self.provider.label}
            with tracing.call(self.provider):
                ai = self.bound.invoke(request, config={"metadata": meta})
        else:
            ai = self.bound.invoke(request)
        self._count(ai, request)
        self._audit("assistant", text=llm_mod.content_text(ai.content), tool_calls=[{"name": c["name"], "args": c["args"]}
                                                                                    for c in ai.tool_calls],
                    usage=getattr(ai, "usage_metadata", None), traced=traced)
        return {"messages": [ai]}

    def _traced(self, label):
        """Whether this model call goes to LangSmith: tracing on, the conversation public, and a system prompt a cloud model could get.
        The surface gets a notice whenever the answer changes."""
        t = tracing.settings()
        failed = tracing.take_failure()
        if failed:
            self.notices.append(f"LangSmith refused the traces ({failed}); check {t.get('key') or 'its key'} with `python -m automc "
                                f"models list`. The conversation goes on untouched.")
        if not t["on"]:
            if t["requested"] and self._trace_said != "config":
                self._trace_said = "config"
                self.notices.append(f"LangSmith tracing is {t['message']}.")
            return False
        why = None
        if label != "public":
            why = f"this conversation holds {label} data"
        elif not self.provider.cloud and guard.restricted_pack_installed():           # agent/prompt.py lists their skills
            why = "the local model's system prompt lists skills of a restricted facility pack"
        said = "off" if why else "on"
        if said != self._trace_said:
            self._trace_said = said
            self.notices.append(f"LangSmith: not traced, because {why}; only what a cloud model may see is traced." if why else
                                f"LangSmith: this conversation's model calls are traced to project {t['project']}.")
        return not why

    def _count(self, ai, request):
        """Add one model call's tokens to self.usage: the service's own report, else an estimate (3.5 characters per token)."""
        u = getattr(ai, "usage_metadata", None) or {}
        inp, out, est = u.get("input_tokens") or 0, u.get("output_tokens") or 0, False
        if not inp:
            inp = context.estimate(request, self._schema_chars)
            out = int(len(llm_mod.content_text(ai.content) + json.dumps(ai.tool_calls)) / context.CHARS_PER_TOKEN)
            est = True
        row = self.usage.setdefault(self.provider.name, {"input": 0, "output": 0, "calls": 0, "estimated": 0})
        row["input"] += int(inp)
        row["output"] += int(out)
        row["calls"] += 1
        row["estimated"] += est

    def spent(self, name=None):
        """Tokens this conversation has used: on provider `name`, or on all of them. {input, output, total, calls, estimated}."""
        rows = [self.usage[name]] if name in self.usage else [] if name else list(self.usage.values())
        out = {k: sum(r[k] for r in rows) for k in ("input", "output", "calls", "estimated")}
        out["total"] = out["input"] + out["output"]
        return out

    @staticmethod
    def _declined_this_turn(messages):
        """plan_ids the user declined since their last message: the same plan is not asked about twice in one turn."""
        out = set()
        for m in reversed(messages):
            if isinstance(m, HumanMessage):
                break
            if isinstance(m, ToolMessage):
                try:
                    d = json.loads(m.content)
                except (TypeError, ValueError):
                    continue
                if isinstance(d, dict) and d.get("declined") and d.get("plan_id"):
                    out.add(d["plan_id"])
        return out

    def _tools(self, state):
        ai = state["messages"][-1]
        out, pending, labels = [], [], []
        declined = self._declined_this_turn(state["messages"])
        for call in ai.tool_calls:
            args, fixed = schemas.repair(self._params.get(call["name"]), call.get("args") or {})
            if fixed:
                self._audit("arguments_parsed", tool=call["name"], fields=fixed)
            so_far = max((state.get("labels") or []) + labels + [self.label_floor], key=LABEL_ORDER.get)
            # the chat model (a public folder's page images may use its service), and what the web and Python tools may know of
            # this conversation: its label so far, its id, the model's window
            with doc_parse.chat_model(self.provider), conversation.inside(self.provider, so_far, self.thread_id, self.provider.window):
                result = registry.call(call["name"], args)
                if (call["name"] in CODE_RUNNERS and isinstance(result, dict) and result.get("needs_approval")
                        and automc_settings.python_runs_without_asking()):
                    result = self._without_asking(call["name"], result["plan"])
            if call["name"] in APPROVAL_TOOLS and isinstance(result, dict) and result.get("ok") and result.get("needs_approval"):
                allowed, _ = guard.check(result, self.provider.cloud, self.policy_ok)
                if allowed and result["plan"].get("plan_id") in declined:
                    result = {"ok": True, "started": False, "declined": True, "plan_id": result["plan"]["plan_id"],
                              "facility_data": result["plan"].get("facility_data"),
                              "message": "The user already declined this plan in this turn. Do not ask again; tell the user nothing "
                                         "was started and wait for their next message."}
                elif allowed:
                    pending.append({"tool_call_id": call["id"], "name": call["name"], "plan": result["plan"]})
                    continue
            msg, entered = self._tool_message(call["id"], call["name"], result)
            out.append(msg)
            labels.append(entered)
        return {"messages": out, "pending": pending, "labels": labels}

    def _without_asking(self, name, plan):
        """Inside a container whose settings say python.approval: auto (W2, A6): run a Python plan or a model build at once, the code
        shown in the chat and saved, as the user's standing approval of this workspace's container."""
        by = f"{self.surface}, standing approval (python.approval: auto, in a container)"
        self._audit("approval", tool=name, approved=True, send=False, plan_id=plan.get("plan_id"), automatic=True)
        try:
            out = _start_for(name)(plan, approved_by=by)
        except (Refused, OpenMCError) as e:
            return {"ok": False, "started": False, "error": e.payload()}
        where = f"workspace/{out.get('code_file')}" if name == "PythonRunTool" else f"workspace/{out.get('case')}/build.py"
        what = "Python ran" if name == "PythonRunTool" else "The model build ran"
        self.notices.append(f"{what} without asking (python.approval: auto, inside a container); its code is in {where}")
        return {"ok": True, "started": True, "approved_by": by, **out}

    def _approve(self, state):
        item = state["pending"][0]
        kind, shape = APPROVALS[item["name"]]
        start = _start_for(item["name"])
        plan = item["plan"]
        answer = interrupt({"kind": kind, "tool": item["name"], "plan_text": plan["text"], "plan": plan,
                            "needs_send": bool(plan.get("needs_send")), "send_option": bool(plan.get("send_option"))})
        approved = bool(answer.get("approved")) if isinstance(answer, dict) else bool(answer)
        send = bool(answer.get("send")) if isinstance(answer, dict) else False
        if plan.get("needs_send") and not send:                    # a web request from an unknown conversation: only 'yes, send'
            approved = False
        self._audit("approval", tool=item["name"], approved=approved, send=send, plan_id=plan.get("plan_id"))
        self.events.append({"event": "approval", "approved": approved, "plan_id": plan.get("plan_id")})
        if approved:
            extra = {"send": send} if shape == "result" else {}
            try:
                label = max((state.get("labels") or []) + [self.label_floor], key=LABEL_ORDER.get)
                with conversation.inside(self.provider, label, self.thread_id, self.provider.window):
                    out = start(plan, approved_by=self.surface, **extra)
                result = ({"ok": True, "started": True, "job": out, "facility_data": plan.get("facility_data")} if shape == "job" else
                          {"ok": True, "started": True, **out})
            except (OpenMCError, Refused) as e:
                result = {"ok": False, "started": False, "error": e.payload(), "facility_data": plan.get("facility_data")}
        else:
            result = {"ok": True, "started": False, "declined": True, "plan_id": item["plan"].get("plan_id"),
                      "facility_data": item["plan"].get("facility_data"),
                      "message": "The user declined. Nothing was started. Do not ask again unless the user asks for the run again."}
        msg, entered = self._tool_message(item["tool_call_id"], item["name"], result)
        return {"messages": [msg], "pending": state["pending"][1:], "labels": [entered]}

    def _build(self):
        g = StateGraph(AgentState)
        g.add_node("agent", self._agent)
        g.add_node("tools", self._tools)
        g.add_node("approve", self._approve)
        g.add_edge(START, "agent")
        g.add_conditional_edges("agent", lambda s: "tools" if getattr(s["messages"][-1], "tool_calls", None) else END)
        g.add_conditional_edges("tools", lambda s: "approve" if s.get("pending") else "agent")
        g.add_conditional_edges("approve", lambda s: "approve" if s.get("pending") else "agent")
        return g.compile(checkpointer=InMemorySaver())

    # ---- driving a turn ----------------------------------------------------------------------------------------------------
    def _head(self):
        """The thread's latest checkpoint (self.config may point at an earlier one while a retry forks from it)."""
        return {"configurable": {"thread_id": self.thread_id}, "recursion_limit": RECURSION_LIMIT}

    def _stream(self, payload):
        """Events: ("token", text), ("tool_call", name, args), ("tool_result", name, view), ("user_output", name, text: for the user's
        eyes only, such as Python output withheld from a cloud model), ("approval", request), ("notice", text), ("error", sentence),
        ("final", text)."""
        try:
            for mode, data in self.graph.stream(payload, self.config, stream_mode=["messages", "updates"]):
                while self.notices:
                    yield ("notice", self.notices.pop(0))
                if mode == "messages":
                    chunk, meta = data
                    if isinstance(chunk, AIMessageChunk) and meta.get("langgraph_node") == "agent":
                        text = llm_mod.content_text(chunk.content)
                        if text:
                            yield ("token", text)
                    continue
                for node, update in data.items():
                    if node == "__interrupt__":
                        self._persist()
                        for intr in update:
                            yield ("approval", intr.value)
                        return
                    for m in (update or {}).get("messages", []) if isinstance(update, dict) else []:
                        if isinstance(m, AIMessage):
                            for c in m.tool_calls:
                                yield ("tool_call", c["name"], c["args"])
                        elif isinstance(m, ToolMessage):
                            yield ("tool_result", m.name, m.content)
                            if isinstance(m.artifact, dict) and m.artifact.get("shown"):
                                yield ("user_output", m.name, m.artifact["shown"])
        except Exception as e:                        # the service failed; the checkpoint before the failed step stays
            kind, text = errors.explain(e, self.provider)
            self._audit("model_error", provider=self.provider.choice, kind=kind, text=text)
            self._persist()
            while self.notices:
                yield ("notice", self.notices.pop(0))
            yield ("error", text + AFTER_ERROR)
            yield ("final", "")
            return
        while self.notices:
            yield ("notice", self.notices.pop(0))
        final = self.last_answer()
        self._audit("final", text=final)
        self._persist()
        yield ("final", final)

    def send(self, text):
        self._audit("user", text=text)
        yield from self._stream({"messages": [HumanMessage(text)], "pending": [], "labels": []})

    def answer(self, approved, send=False):
        """Resume a turn that is waiting for the user's answer to an approval question. `send`: the user typed 'yes, send' (a web
        request from a conversation of unknown origin; Python output a cloud model may read)."""
        yield from self._stream(Command(resume={"approved": bool(approved), "send": bool(send)}))

    def resume(self):
        """Continue a turn that was interrupted (Ctrl-C) or is waiting, from its last checkpoint."""
        yield from self._stream(None)

    # ---- the conversation's label and switching models ------------------------------------------------------------------
    def conversation_label(self):
        """public, unknown or restricted: the most restricted of what the tool results put into this conversation."""
        st = self.graph.get_state(self._head())
        found = (st.values or {}).get("labels", []) if st else []
        return max(found + [self.label_floor], key=LABEL_ORDER.get)

    def switch_model(self, name, confirm=False, model=None):
        """
        Make `name` (a provider, or PROVIDER/MODEL from discovery.catalog) the model of this conversation, if the privacy rule allows it
        (AutoMC_Agent_Models_Plan.md, D1); a discovered model counts as its provider:
          - to a local model: always;
          - to a cloud model: public conversation, with a notice; unknown origin, only with confirm=True (the surface asks the user to
            type it); restricted, refused (offer a new conversation), unless the data owners' two-key policy names that provider.
        Returns {"switched", "notice" | "needs_confirmation" | "refused", "message", "label", "provider"}.
        """
        new = discovery.resolve(name)
        label = self.conversation_label()
        out = {"provider": new.describe(), "label": label, "switched": False}
        if self.waiting_for_approval():
            return {**out, "refused": True, "message": "Answer the pending question about starting a run first."}
        if new.cloud and not llm_mod.restricted_allowed(new):
            if label == "restricted":
                self._audit("switch_refused", to=new.choice, label=label)
                return {**out, "refused": True, "message": (
                    f"Not switched: this conversation holds restricted facility data, which may not go to {new.label} "
                    f"({new.account} account, cloud). Start a new conversation with it instead (/new --model {new.label}); nothing "
                    f"from this one is carried over.")}
            if label == "unknown" and not confirm:
                return {**out, "needs_confirmation": True, "message": (
                    f"This conversation holds results of unknown origin (built before results recorded their facility data). "
                    f"If any of it is restricted, it must not go to {new.label}. Type 'yes, send' to switch anyway, or start a new "
                    f"conversation (/new --model {new.label}).")}
        old = self.provider
        self._use(new, model)
        where = "stays on this machine" if not new.cloud else f"goes to {new.label} ({new.account} account, outside this machine)"
        self._audit("switch", frm=old.choice, to=new.choice, label=label, confirmed=bool(confirm))
        self.events.append({"event": "switch", "from": old.choice, "to": new.choice, "label": label})
        return {**out, "switched": True, "notice": True, "message": f"Now using {new.describe()}. From the next message the conversation "
                                                                     f"{where}."}

    def waiting_for_approval(self):
        st = self.graph.get_state(self._head())
        return [i.value for i in st.interrupts] if st.interrupts else []

    def repair(self):
        """After Ctrl-C: answer tool calls that never ran, so the conversation stays valid. Returns how many were closed."""
        st = self.graph.get_state(self._head())
        msgs = st.values.get("messages", []) if st.values else []
        if st.interrupts or not msgs or not isinstance(msgs[-1], AIMessage) or not msgs[-1].tool_calls:
            return 0
        done = [ToolMessage(content=json.dumps({"ok": False, "error": {"type": "Interrupted", "message":
                                                "not run: the user interrupted the turn before this tool ran"}}),
                            tool_call_id=c["id"], name=c["name"]) for c in msgs[-1].tool_calls]
        self.graph.update_state(self._head(), {"messages": done, "pending": []}, as_node="agent")
        self._audit("repair", closed=len(done))
        return len(done)

    def retry(self):
        """Re-issue the last user message from the state just before it (the earlier answer stays in the checkpoint history)."""
        history = list(self.graph.get_state_history(self._head()))
        current = history[0].values.get("messages", []) if history else []
        humans = [i for i, m in enumerate(current) if isinstance(m, HumanMessage)]
        if not humans:
            raise ValueError("there is no earlier message to retry")
        idx = humans[-1]
        text = current[idx].content
        self._audit("retry", text=text)
        self._audit("rewind", keep=idx)              # a saved conversation keeps only the messages before the retried one
        self._saved_ids = {m.id for m in current[:idx]}
        if idx == 0:                                  # the first message: start the conversation again
            self.thread_id = chat_store.new_id()
            self.config = {"configurable": {"thread_id": self.thread_id}, "recursion_limit": RECURSION_LIMIT}
            yield from self._stream({"messages": [HumanMessage(text)], "pending": []})
            return
        before = next((h for h in history if len(h.values.get("messages", [])) == idx and not h.next), None)
        if before is None:
            raise ValueError("the turn before the last message did not finish; send the message again instead")
        self.config = {"configurable": dict(before.config["configurable"]), "recursion_limit": RECURSION_LIMIT}
        try:
            yield from self._stream({"messages": [HumanMessage(text)], "pending": []})
        finally:
            self.config = {"configurable": {"thread_id": self.thread_id}, "recursion_limit": RECURSION_LIMIT}

    def last_answer(self):
        st = self.graph.get_state(self._head())
        for m in reversed(st.values.get("messages", []) if st.values else []):
            if isinstance(m, AIMessage) and not m.tool_calls:
                return llm_mod.content_text(m.content)
        return ""

    def messages(self):
        st = self.graph.get_state(self._head())
        return st.values.get("messages", []) if st.values else []


# ----------------------------------------------------------------------------------------------------------------------
# saved conversations (devops/AutoMC_Workspace_Plan.md, section 5, WD-3)
# ----------------------------------------------------------------------------------------------------------------------
def rebuild(events, known=None):
    """(messages, labels) of a saved conversation, from its message lines, its rewinds applied. A call to a tool that no longer exists
    (or was renamed) becomes plain text with its result, and a turn cut off before its tools ran is closed, so the conversation is valid
    for any model."""
    known = set(registry.names()) if known is None else set(known)
    rows = []
    for e in events:
        if e.get("event") == "rewind":
            rows = rows[:int(e.get("keep") or 0)]
        elif e.get("event") == "message" and isinstance(e.get("message"), dict):
            try:
                rows.append((messages_from_dict([e["message"]])[0], e.get("label") or "unknown"))
            except Exception:                         # a message this LangChain cannot read: left out
                continue
    msgs, labels, dropped = [], [], {}
    for m, label in rows:
        if isinstance(m, AIMessage) and m.tool_calls and any(c["name"] not in known for c in m.tool_calls):
            gone = [c for c in m.tool_calls if c["name"] not in known]
            dropped.update({c["id"]: c for c in gone})
            text = llm_mod.content_text(m.content)
            note = "; ".join(f"[an earlier call of {c['name']}, a tool AutoMC no longer has, with {json.dumps(c['args'])[:300]}]" for c in gone)
            m = AIMessage(content=(text + "\n" + note).strip(), tool_calls=[c for c in m.tool_calls if c["name"] in known], id=m.id)
        if isinstance(m, ToolMessage) and m.tool_call_id in dropped:
            msgs.append(AIMessage(content=f"[its result: {str(m.content)[:1500]}]", id=m.id))
            labels.append(label)
            continue
        msgs.append(m)
        if isinstance(m, ToolMessage):
            labels.append(label)
    if msgs and isinstance(msgs[-1], AIMessage) and msgs[-1].tool_calls:
        msgs += [ToolMessage(content=json.dumps({"ok": False, "error": {"type": "Interrupted", "message": "not run: the conversation "
                                                 "was saved before this tool ran"}}), tool_call_id=c["id"], name=c["name"])
                 for c in msgs[-1].tool_calls]
    return msgs, labels


def load_saved(chat_id, provider=None, model=None, surface="chat", confirm=False):
    """Continue a saved conversation of this workspace with `provider` (default: the default model). A conversation holding restricted
    data is refused to a cloud model (start a new one, or use the local model); one holding data of unknown origin needs confirm=True
    (the user typed 'yes, send'), as a switch of model does. Returns (Session or None, {loaded, refused, needs_confirmation, message,
    label})."""
    path = chat_store.folder() / f"{chat_id}.jsonl"
    events = chat_store.read(path)
    if not events:
        return None, {"loaded": False, "refused": True, "message": f"no saved conversation {chat_id} in this workspace"}
    msgs, labels = rebuild(events)
    # the conversation's label: of what its file holds, and of what it held but did not save (restricted data entered and the settings
    # said not to save it): such a conversation stays restricted even though the reloaded part is not
    recorded = [e.get("entered") for e in events if e.get("event") == "tool_result" and e.get("entered")]
    recorded += ["restricted" for e in events if e.get("event") == "not_saved"]
    label = max(labels + recorded, key=LABEL_ORDER.get, default="public")
    prov = provider or discovery.resolve(None)
    out = {"loaded": False, "label": label}
    if prov.cloud and not llm_mod.restricted_allowed(prov):
        if label == "restricted":
            return None, {**out, "refused": True, "message": (
                f"Not loaded: this conversation holds restricted facility data, which may not go to {prov.label}. Load it with the "
                f"local model (/model local, then /load), or start a new conversation.")}
        if label == "unknown" and not confirm:
            return None, {**out, "needs_confirmation": True, "message": (
                f"This conversation holds results of unknown origin; if any of it is restricted, it must not go to {prov.label}. "
                f"Type 'yes, send' to load it with this model anyway, or load it with the local model.")}
    s = Session(provider=prov, model=model, surface=surface, thread_id=chat_id, resume=True)
    if msgs:
        s.graph.update_state(s._head(), {"messages": msgs, "pending": [], "labels": labels}, as_node="agent")
    s.label_floor = label                             # the whole conversation's label, saved part or not
    s._saved_ids = {m.id for m in msgs}
    if label == "restricted" and not chat_store.save_restricted():
        s.chat.stop_content("the conversation holds restricted data and the workspace's settings say not to save it")
    s._audit("resumed", provider=prov.full_name(), label=label, messages=len(msgs))
    turns = sum(1 for m in msgs if isinstance(m, HumanMessage))
    return s, {**out, "loaded": True, "message": f"Loaded: {turns} earlier turn(s), {label} data; the model is {prov.label}."}
