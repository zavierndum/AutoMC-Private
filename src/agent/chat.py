"""
The command-line chat: python -m automc chat [--provider NAME]. From AutoMC/src, in the WSL automc environment.

    /model             the models you can choose, numbered (configured in llm.yaml and discovered: local models with tools, vision
                       and thinking; OpenAI from gpt-4 on; Gemini from gemini-3 on), and what this conversation holds
    /model N, /model NAME   switch to that model, NAME as the list shows it (cloud-portkey/gpt-5.4) or a provider (local); a
                       cloud model only if this conversation holds no restricted data; a discovered cloud model is checked once
                       first, if you agree
    /model --refresh   list the cloud services again
    /new [--model N | NAME]   a new conversation (with another model)
    /jobs              the job registry (directly, without the model)
    /check JOB_ID      one job (directly)
    /pause JOB_ID, /resume JOB_ID, /checkpoint JOB_ID, /stop JOB_ID   control a run directly, at the end of its current batch
                       (/resume of a stopped run shows a restart plan and asks first)
    /cancel JOB_ID     kill a run at once, after you confirm (statepoints written so far stay)
    /sessions          the saved conversations of this workspace and of the linked ones (each conversation is saved as it goes)
    /show N, /load N, /delete N   read one, continue one (a linked one as a copy here), delete one of this workspace's
    /remember TEXT     save a note to this workspace's memory (--public TEXT marks it public)
    /memory            AUTOMC.md and the notes; /memory show|forget|public NAME
    /resume            continue a turn you interrupted
    /retry             ask your last message again
    /tools             the agent's tools
    /prompt            what the model is told (system prompt size, skills, facility packs)
    /help, /exit

Ctrl-C during a turn stops the agent's turn and never touches a run. Ctrl-C at the prompt clears the line; Ctrl-D leaves.
"""
from __future__ import annotations

import argparse
import json
import re
import sys

import automc_paths

DIM, BOLD, RESET, YELLOW, RED = "\033[2m", "\033[1m", "\033[0m", "\033[33m", "\033[31m"


def _short(obj, n=160):
    text = obj if isinstance(obj, str) else json.dumps(obj, default=str)
    return text if len(text) <= n else text[:n - 3] + "..."


def _result_line(content):
    try:
        d = json.loads(content)
    except (TypeError, ValueError):
        return _short(content)
    if d.get("withheld"):
        return f"{YELLOW}withheld from the cloud model{RESET}"
    if d.get("ok") is False:
        err = d.get("error") or {}
        return f"{RED}error{RESET} {err.get('type', '')}: {_short(err.get('message', ''), 140)}"
    if d.get("needs_approval"):
        return "a plan that needs your approval"
    return "ok"


class ChatUI:
    def __init__(self, session, ask=None, out=None):
        self.s = session
        self.out = out or sys.stdout
        self._ask = ask
        self._entries = None                          # the last list /model showed, so that /model N means what the user saw

    def say(self, text="", end="\n"):
        self.out.write(text + end)
        self.out.flush()

    def ask(self, question):
        if self._ask:
            return self._ask(question)
        from prompt_toolkit import prompt as pt_prompt
        return pt_prompt(question)

    def run_events(self, events):
        """Show a turn's events; asks the approval questions and goes on with the user's answer. Returns the final text."""
        streaming = False
        while True:
            approval = None
            for ev in events:
                kind = ev[0]
                if kind == "token":
                    if not streaming:
                        self.say(f"{BOLD}automc>{RESET} ", end="")
                        streaming = True
                    self.say(ev[1], end="")
                elif kind == "tool_call":
                    if streaming:
                        self.say()
                        streaming = False
                    self.say(f"{DIM}  -> {ev[1]} {_short(ev[2], 140)}{RESET}")
                elif kind == "tool_result":
                    self.say(f"{DIM}  <- {ev[1]}: {_result_line(ev[2])}{RESET}")
                elif kind == "user_output":
                    self.say(f"  {YELLOW}shown to you only (not to the cloud model):{RESET}")
                    self.say("\n".join(f"    {line}" for line in str(ev[2]).split("\n")))
                elif kind == "approval":
                    approval = ev[1]
                elif kind == "notice":
                    if streaming:
                        self.say()
                        streaming = False
                    self.say(f"{YELLOW}  note: {ev[1]}{RESET}")
                elif kind == "error":
                    if streaming:
                        self.say()
                        streaming = False
                    self.say(f"{RED}  {ev[1]}{RESET}")
                elif kind == "final":
                    if streaming:
                        self.say()
                    return ev[1]
            if approval is None:
                return None
            if streaming:
                self.say()
                streaming = False
            kind = approval.get("kind")
            what = {"parse_approval": "parse documents into a knowledge base", "python_approval": "run Python code",
                    "web_approval": "send a request to the web", "memory_approval": "save a note to this workspace's memory",
                    "build_approval": "build an OpenMC model from a script"}.get(kind, "start transport")
            self.say(f"\n{YELLOW}The agent asks to {what}:{RESET}\n{approval['plan_text']}")
            if approval.get("needs_send"):
                answer = (self.ask("Type 'yes, send' to send it: ") or "").strip().lower()
                yes = send = answer in ("yes, send", "yes,send")
            elif approval.get("send_option"):
                answer = (self.ask("Run it? [y/N, or 'yes, send' to let the cloud model read the output] ") or "").strip().lower()
                send = answer in ("yes, send", "yes,send")
                yes = send or answer in ("y", "yes")
            else:
                question = {"memory_approval": "Save it? [y/N] ", "build_approval": "Build it? [y/N] "}.get(kind, "Start it? [y/N] ")
                answer = (self.ask(question) or "").strip().lower()
                yes, send = answer in ("y", "yes"), False
            done, not_done = {"web_approval": ("sending", "not sent"), "python_approval": ("running", "not run"),
                              "memory_approval": ("saving", "not saved"), "build_approval": ("building", "not built")
                              }.get(kind, ("starting", "not started"))
            self.say(done if yes else not_done)
            events = self.s.answer(yes, send)

    def turn(self, events):
        before = self.s.spent()
        try:
            return self.run_events(events)
        except KeyboardInterrupt:
            closed = self.s.repair()
            self.say(f"\n{YELLOW}Turn interrupted; no run was touched{RESET}" + (f" ({closed} tool call(s) not run)" if closed else "")
                     + ". /resume continues it, /retry asks again, or write a new message.")
            return None
        finally:
            self.usage_line(before)

    def usage_line(self, before):
        """One dim line after a turn: its tokens, and the conversation's on this provider against the cap."""
        now = self.s.spent()
        calls = now["calls"] - before["calls"]
        if not calls:
            return
        p = self.s.provider
        mine = self.s.spent(p.name)["total"]
        est = " (estimated)" if now["estimated"] > before["estimated"] else ""
        cap = f" of its cap of {p.cap:,}" if p.cap else ""
        self.say(f"{DIM}  tokens: {now['input'] - before['input']:,} in, {now['output'] - before['output']:,} out over {calls} model "
                 f"call(s){est}; {p.label} in this conversation: {mine:,}{cap}{RESET}")

    def _choice(self, arg):
        """A number from the /model list, or a provider or PROVIDER/MODEL as typed."""
        if not arg.isdigit():
            return arg
        from agent import discovery
        from agent.llm import LLMConfigError
        entries = self._entries or discovery.catalog()["entries"]
        if not 1 <= int(arg) <= len(entries):
            raise LLMConfigError(f"there is no model {arg}; /model lists them")
        return entries[int(arg) - 1]["choice"]

    def _usable(self, choice):
        """True when `choice` may be used now. A discovered cloud model that has not been checked is checked first, if the user
        agrees (fixed text and the tool definitions only, about 6,000 tokens). Raises LLMConfigError for anything else wrong with it."""
        from agent import discovery, models
        try:
            discovery.resolve(choice)
            return True
        except discovery.NotChecked as e:
            self.say(f"{YELLOW}{e}{RESET}")
        if (self.ask("Check it now? [y/N] ") or "").strip().lower() not in ("y", "yes"):
            self.say("not switched")
            return False
        out = models.check(choice, tools=True)
        if not out.get("ok"):
            self.say(f"{RED}the check failed: {out.get('error')}{RESET}; not switched")
            return False
        self.say(f"  checked: it answered in {out.get('seconds')} s, called the tool correctly in {out.get('tool_seconds')} s and "
                 f"accepted AutoMC's tool definitions")
        return True

    def job_command(self, command, args):
        """/pause, /resume, /checkpoint, /stop or /cancel JOB_ID: a run's controls, used directly (not through the model), as the jobs
        panel's buttons will be. Cancel kills the run, so it asks first; resuming a stopped run is a restart, which shows its plan and
        asks, as the command line does."""
        from tools.openmc import runs
        from tools.openmc.errors import OpenMCError
        if not args:
            self.say(f"/{command} needs a job id; /jobs lists them")
            return
        try:
            job = runs.check(job_id=args[0])
            if command == "cancel":
                question = (f"Cancel job {job['job_id']} ({job['case']}) now? It is killed at once; the statepoints written so far "
                            f"stay. [y/N] ")
                if (self.ask(question) or "").strip().lower() not in ("y", "yes"):
                    self.say("not cancelled")
                    return
                out = runs.cancel(job["job_id"])
                self.say(f"  {out['job_id']}  {out['status']}")
            elif command == "resume" and job["status"] in ("stopped", "interrupted", "cancelled"):
                plan = runs.plan_restart(job["job_id"])
                self.say(f"\n{YELLOW}Continuing it from its last statepoint is a restart:{RESET}\n{plan['text']}")
                if (self.ask("Start it? [y/N] ") or "").strip().lower() not in ("y", "yes"):
                    self.say("not started")
                    return
                out = runs.restart(plan, approved_by="chat")
                self.say(f"  {out.get('job_id')}  {out.get('status')}")
            else:
                out = runs.control(job["job_id"], command)
                self.say(f"  {out['job_id']}  {out['status']}: {command} requested; {out['note']}")
        except OpenMCError as e:
            self.say(f"{RED}{e}{RESET}")

    # ---- saved conversations (WD-3) and memory (WD-4) ----------------------------------------------------------------------
    def sessions(self):
        import console
        from tools.chats import store
        rows = store.list_chats()
        if not rows:
            self.say("  no saved conversations yet (each conversation is saved as it goes)")
            return
        self.say(console.table(["N", "Updated", "Turns", "Data", "Where", "Title"],
                               [[f"{'*' if r['id'] == self.s.thread_id else ' '}{i}", r["updated_at"][:16].replace("T", " "), r["turns"],
                                 r["label"] + ("" if r["saved"] else ", partly saved"), r["where"], r["title"]]
                                for i, r in enumerate(rows, 1)], right={2}, limits={5: 60}))
        self.say(f"  {DIM}/show N reads one; /load N continues it; /delete N removes one of this workspace's{RESET}")

    def saved(self, action, args):
        import automc_core
        from tools.chats import store
        row = store.find(args[0]) if args else None
        if row is None:
            self.say(f"/{action} needs a number or an id from /sessions")
            return
        if action == "show":
            for e in store.read(row["path"]):
                if e.get("event") == "user":
                    self.say(f"{BOLD}you>{RESET} {e.get('text') or '(not saved)'}")
                elif e.get("event") == "final":
                    self.say(f"{BOLD}automc>{RESET} {e.get('text') or '(not saved)'}\n")
                elif e.get("event") == "not_saved":
                    self.say(f"{YELLOW}  (from here on not saved: {e.get('reason')}){RESET}")
            return
        if action == "delete":
            if row["where"] != "this workspace" or row["id"] == self.s.thread_id:
                self.say("only another conversation of this workspace can be deleted")
                return
            if (self.ask(f"Delete '{row['title']}'? What left the machine stays recorded. [y/N] ") or "").strip().lower() in ("y", "yes"):
                store.delete(row["id"])
                self.say("deleted")
            else:
                self.say("not deleted")
            return
        chat_id = row["id"]
        if row["where"] != "this workspace":
            if (self.ask("It is in a linked workspace: continue it here, as a copy (the original stays as it is)? [y/N] ") or ""
                ).strip().lower() not in ("y", "yes"):
                self.say("not loaded")
                return
            chat_id = store.copy_here(row["path"])
        s, out = automc_core.load_saved(chat_id, provider=self.s.provider, model=self.s.model, surface=self.s.surface)
        if out.get("needs_confirmation"):
            self.say(f"{YELLOW}{out['message']}{RESET}")
            if (self.ask("> ") or "").strip().lower() != "yes, send":
                self.say("not loaded")
                return
            s, out = automc_core.load_saved(chat_id, provider=self.s.provider, model=self.s.model, surface=self.s.surface, confirm=True)
        if s is None:
            self.say(f"{YELLOW}{out['message']}{RESET}")
            return
        self.s = s
        self.say(out["message"])

    def remember(self, text):
        from tools.memory import store
        public = text.startswith("--public")
        text = text[len("--public"):].strip() if public else text
        if not text:
            self.say("/remember TEXT saves a note (with this conversation's label); /remember --public TEXT marks it public")
            return
        title = re.split(r"(?<=[.!?])\s", text, maxsplit=1)[0][:60]
        try:
            note = store.save(title, text, "public" if public else self.s.conversation_label(), chat=self.s.thread_id)
        except store.MemoryError_ as e:
            self.say(f"{RED}{e}{RESET}")
            return
        self.say(f"  saved as {note['name']} ({note['label']}){' (replacing the note of that title)' if note['replaced'] else ''}; "
                 f"new conversations see it")

    def memory(self, args):
        import console
        from tools.memory import store
        if not args:
            notes = store.notes()
            md, label = store.automc_md()
            self.say(f"  AUTOMC.md: {'none (write one at the workspace root)' if md is None else f'{len(md):,} characters, {label}'}")
            if not notes:
                self.say("  no notes yet (/remember TEXT, or the agent proposes one and you approve it)")
                return
            self.say(console.table(["Name", "Label", "Updated", "Note"], [[n["name"], n["label"], (n.get("updated") or "")[:10],
                                                                         n["text"].replace("\n", " ")] for n in notes], limits={3: 70}))
            self.say(f"  {DIM}/memory show NAME, /memory forget NAME, /memory public NAME{RESET}")
            return
        action, name = args[0], (args[1] if len(args) > 1 else "")
        note = store.get(name) if name else None
        if note is None:
            self.say(f"no note {name!r}; /memory lists them")
        elif action == "show":
            self.say(f"  {note['name']} ({note['label']}, updated {note.get('updated')}):\n{note['text']}")
        elif action == "forget":
            if (self.ask(f"Forget {note['name']}? [y/N] ") or "").strip().lower() in ("y", "yes"):
                store.forget(note["name"])
                self.say("forgotten")
        elif action == "public":
            if (self.ask(f"Mark {note['name']} public? A cloud model will then see it. [y/N] ") or "").strip().lower() in ("y", "yes"):
                store.set_label(note["name"], "public")
                self.say("marked public")
        else:
            self.say("/memory, /memory show NAME, /memory forget NAME, /memory public NAME")

    def command(self, line):
        """A slash command; returns False to leave."""
        from tools import registry
        from tools.openmc import runs
        from tools.openmc.errors import OpenMCError
        parts = line.strip().split()
        cmd, args = parts[0].lower(), parts[1:]
        if cmd in ("/exit", "/quit"):
            return False
        if cmd == "/help":
            self.say(__doc__.strip())
        elif cmd == "/jobs":
            jobs = [runs.summarize(j) for j in runs.list_jobs()]
            for j in jobs[-15:]:
                k = j.get("keff") or {}
                kt = f"k-eff {k['value']:.5f} +/- {k['sigma']:.5f}" if k.get("value") is not None and k.get("sigma") else ""
                self.say(f"  {j['job_id']}  {j['status']:11s} {j['case']}  {kt}")
            if not jobs:
                self.say("  no jobs")
        elif cmd == "/check":
            try:
                self.say(json.dumps(runs.check(job_id=args[0] if args else None), indent=2, default=str))
            except OpenMCError as e:
                self.say(f"{RED}{e}{RESET}")
        elif cmd in ("/pause", "/resume", "/checkpoint", "/stop", "/cancel"):
            self.job_command(cmd[1:], args)
        elif cmd == "/sessions":
            self.sessions()
        elif cmd in ("/show", "/load", "/delete"):
            self.saved(cmd[1:], args)
        elif cmd == "/remember":
            self.remember(line.strip()[len("/remember"):].strip())
        elif cmd == "/memory":
            self.memory(args)
        elif cmd == "/tools":
            for t in registry.describe():
                self.say(f"  {t['name']}: {_short(t['description'], 110)}")
        elif cmd == "/prompt":
            info = self.s.prompt_info
            self.say(f"  AutoMC {info['version']}, prompt and skills {info['fingerprint']}: {len(self.s.system):,} characters; skills: "
                     f"{', '.join(info['skills'])}")
            if info["skipped_packs"]:
                self.say(f"  skills of restricted packs left out for this cloud model: {', '.join(info['skipped_packs'])}")
        elif cmd == "/resume":
            self.turn(self.s.resume())
        elif cmd == "/retry":
            try:
                self.turn(self.s.retry())
            except ValueError as e:
                self.say(f"{RED}{e}{RESET}")
        elif cmd == "/model" and (not args or args[0] == "--refresh"):
            from agent import discovery
            from agent.models import picker_lines
            cat = discovery.catalog(refresh=bool(args))
            self._entries = cat["entries"]
            self.say(f"  now: {self.s.provider.describe()}; this conversation holds {self.s.conversation_label()} data")
            for text in picker_lines(cat, now=self.s.provider.choice):
                self.say(text)
            self.say(f"  {DIM}/model N or /model NAME (as listed) switches; /model --refresh lists the cloud services again{RESET}")
        elif cmd == "/model":
            from agent.llm import LLMConfigError
            try:
                choice = self._choice(args[0])
                if not self._usable(choice):
                    return True
                out = self.s.switch_model(choice)
                if out.get("needs_confirmation"):
                    self.say(f"{YELLOW}{out['message']}{RESET}")
                    if (self.ask("> ") or "").strip().lower() == "yes, send":
                        out = self.s.switch_model(choice, confirm=True)
                    else:
                        self.say("not switched")
                        return True
                self.say(f"{YELLOW if not out.get('switched') else ''}{out['message']}{RESET}")
            except LLMConfigError as e:
                self.say(f"{RED}{e}{RESET}")
        elif cmd == "/new":
            from agent.llm import LLMConfigError
            from automc_core import Session
            name = args[args.index("--model") + 1] if "--model" in args and args.index("--model") + 1 < len(args) else None
            try:
                if name:
                    name = self._choice(name)
                    if not self._usable(name):
                        return True
                    self.s = Session(provider_name=name, surface=self.s.surface)
                else:
                    self.s = Session(provider=self.s.provider, model=self.s.model, surface=self.s.surface)
            except LLMConfigError as e:
                self.say(f"{RED}{e}{RESET}")
                return True
            self.say(f"new conversation {self.s.thread_id} with {self.s.provider.describe()}")
        else:
            self.say(f"unknown command {cmd}; /help lists them")
        return True


def header(session, stream=None):
    """The chat's opening, as the TRIGA Control Room agent opens its own: a banner, then the model and what it was given."""
    import console
    info = session.prompt_info
    rule = "=" * console.RULE
    title = f"AutoMC chat   {session.provider.label}"
    lines = [console.paint(rule, console.CYAN, stream), console.paint(f" {title}", console.BOLD, stream),
             console.paint(rule, console.CYAN, stream)]
    lines.append(console.fields(
        [("Model", session.provider.describe()),
         ("Prompt", f"{len(session.system):,} characters of system prompt (AutoMC {info.get('version')}, prompt and skills "
                    f"{info.get('fingerprint')})"),
         ("Skills", ", ".join(info["skills"])),
         ("Cloud", "results labelled restricted are withheld from this model" if session.provider.cloud else None),
         ("Packs", f"skills of restricted packs left out: {', '.join(info['skipped_packs'])}" if info["skipped_packs"] else None),
         ("Manifest", f"skills differ from MANIFEST.json: {', '.join(f'{f} ({w})' for f, w in info['manifest'][:5])}"
          if info["manifest"] else None),
         ("Keys", "Ctrl-C stops a turn (never a run); /help for commands; Ctrl-D to leave")]))
    return "\n".join(lines) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m automc chat", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--provider", help="a model as `python -m automc models discover` shows it (cloud-portkey/gpt-5.4), a provider in "
                                       "config/llm.yaml (local), or PROVIDER/MODEL (default: AUTOMC_LLM_PROVIDER, else default_provider)")
    ap.add_argument("--load", metavar="ID", help="continue a saved conversation (a number or an id from /sessions)")
    a = ap.parse_args(argv)
    from agent.llm import LLMConfigError
    from automc_core import Session
    try:
        session = Session(provider_name=a.provider, surface="chat")
    except LLMConfigError as e:
        print(f"cannot start the chat: {e}", file=sys.stderr)
        return 4
    ui = ChatUI(session)
    ui.say(header(session, ui.out))
    if a.load:
        ui.saved("load", [a.load])
    from prompt_toolkit import PromptSession
    from prompt_toolkit.history import FileHistory
    history = automc_paths.state("chats", ".history")
    history.parent.mkdir(parents=True, exist_ok=True)
    prompt = PromptSession(history=FileHistory(str(history)))
    while True:
        try:
            line = prompt.prompt(f"you ({ui.s.provider.label})> ")
        except KeyboardInterrupt:
            continue
        except EOFError:
            break
        if not line.strip():
            continue
        if line.strip().startswith("/"):
            if not ui.command(line):
                break
            continue
        ui.turn(ui.s.send(line))
    ui.say("bye")
    return 0
