"""
The evaluation runner: put every case of evals/cases.yml to the agent with a real model, in a fresh workspace, and score each with
fixed rules. Ported from the Control Room Agent's runner (YAML cases, a strict loader, rules that name themselves when they fail).

    python -m automc eval list
    python -m automc eval run [--case ID ...] [--provider NAME | PROVIDER/MODEL]

A run lands in <workspace>/evals/<UTC>_<model>/: report.md, results.jsonl, and its own workspace (fixtures, builds, runs and chat
audits). The cases run in a child process whose AUTOMC_WORKSPACE is that folder, so nothing of the real workspace is touched. A local
model sees whatever facility packs are installed. A cloud model is evaluated with nothing restricted reachable: the child runs without
the restricted facility packs, the restricted and unlabelled folders of the environment and the user's settings (child_env), it
refuses to start if anything restricted is still declared, and the cases that need the restricted pack are skipped; run those with
the local model. Each case of a cloud run may spend up to CLOUD_CASE_CAP tokens (a case is one conversation; the provider's own cap
of a chat is lower), and the report says so.

cases.yml is publishable, so it holds no facility values. A case that needs the restricted pack and checks such values says
answer_from_pack, and its phrases come from the pack's eval_answers file (pack_answers); the loader refuses a number in the phrases of
any case that needs the restricted pack.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import automc_paths

CASES_PATH = automc_paths.SRC / "evals" / "cases.yml"
CASE_KEYS = {"id", "title", "turns", "setup", "needs", "approve", "expect"}
EXPECT_KEYS = {"tools_required", "tools_any", "tools_forbidden", "approval_asked", "run_started", "answer_all", "answer_none",
               "keff_with_error", "plan_kinds", "answer_from_pack"}
PACK_ANSWER_KEYS = ("answer_all", "answer_none")   # what the restricted pack's eval_answers may give a case
SETUPS = {"slow_run"}
CLOUD_CASE_CAP = 400_000            # tokens per case of a cloud run: a first step alone is about 23k, a long case about ten steps
RESTRICTED_PACK = "netl_restricted"  # the pack the needs: restricted_pack cases read
_KEFF = re.compile(r"\d\.\d{2,}\s*(?:\+/-|±|\+-|plus or minus)\s*\d\.?\d*", re.IGNORECASE)


class CaseError(Exception):
    pass


def load_cases(path=None):
    import yaml
    from tools import registry
    import automc_version
    raw = yaml.safe_load(Path(path or CASES_PATH).read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or set(raw) != {"cases"}:
        raise CaseError("cases.yml needs exactly cases (their version is the package's, automc_version.py)")
    names, ids, out = set(registry.names()), set(), []
    for c in raw["cases"]:
        where = c.get("id", "?")
        bad = set(c) - CASE_KEYS
        if bad or not c.get("id") or not c.get("title") or not isinstance(c.get("turns"), list) or not c["turns"]:
            raise CaseError(f"case {where}: needs id, title and turns; unknown keys {sorted(bad)}")
        if c["id"] in ids:
            raise CaseError(f"case {where} appears twice")
        ids.add(c["id"])
        exp = c.get("expect") or {}
        if set(exp) - EXPECT_KEYS:
            raise CaseError(f"case {where}: unknown expect keys {sorted(set(exp) - EXPECT_KEYS)}")
        for key in ("tools_required", "tools_any", "tools_forbidden"):
            unknown = [t for t in exp.get(key, []) if t not in names]
            if unknown:
                raise CaseError(f"case {where}: {key} names unknown tools {unknown}")
        answers = _answers(c)
        if set(c.get("setup", [])) - SETUPS or c.get("needs") not in (None, "restricted_pack") or not answers or None in answers:
            raise CaseError(f"case {where}: setup must be from {sorted(SETUPS)}, needs restricted_pack, approve yes, no or a list of them")
        _check_answers(f"case {where}", exp)
        if exp.get("answer_from_pack") not in (None, True) or (exp.get("answer_from_pack") and c.get("needs") != "restricted_pack"):
            raise CaseError(f"case {where}: answer_from_pack is true, and only in a case that needs the restricted pack")
        if c.get("needs") == "restricted_pack" and any(re.search(r"\d", a) for a in _phrases(exp)):
            raise CaseError(f"case {where}: it needs the restricted pack, so its facility values go in the pack's eval answers "
                            f"(answer_from_pack), not in this file; a phrase here holds a number")
        out.append(c)
    return automc_version.describe(include_cases=True), out


def _check_answers(where, exp):
    for group in exp.get("answer_all", []):
        if not isinstance(group, list) or not group or not all(isinstance(a, str) for a in group):
            raise CaseError(f"{where}: answer_all is a list of lists of alternatives (text)")
    none = exp.get("answer_none", [])
    if not isinstance(none, list) or not all(isinstance(a, str) for a in none):
        raise CaseError(f"{where}: answer_none is a list of phrases")


def _phrases(exp):
    return [a for group in exp.get("answer_all", []) for a in group] + list(exp.get("answer_none", []))


def pack_answers(pack, cases):
    """{case id: {answer_all, answer_none}} for the cases that take their phrases from the restricted pack (answer_from_pack), read
    from the pack's eval_answers file, so the facility values stay in the pack. {} without the pack or the file; those cases are then
    skipped."""
    raw = pack.data("eval_answers") if pack is not None else None
    if raw is None:
        return {}
    where = f"the {pack.id} pack's eval answers"
    if not isinstance(raw, dict):
        raise CaseError(f"{where}: a mapping of case ids")
    wanted = {c["id"] for c in cases if (c.get("expect") or {}).get("answer_from_pack")}
    for cid, ans in raw.items():
        if cid not in wanted:
            raise CaseError(f"{where}: {cid!r} is not a case with answer_from_pack")
        if not isinstance(ans, dict) or not ans or set(ans) - set(PACK_ANSWER_KEYS):
            raise CaseError(f"{where}, {cid}: only {' and '.join(PACK_ANSWER_KEYS)}")
        _check_answers(f"{where}, {cid}", ans)
    return raw


def with_pack_answers(case, answers):
    """The case with the pack's phrases added to its own (a copy; the case is unchanged without them)."""
    extra = answers.get(case["id"])
    if not extra:
        return case
    exp = dict(case.get("expect") or {})
    for key in PACK_ANSWER_KEYS:
        if key in extra:
            exp[key] = list(exp.get(key, [])) + list(extra[key])
    return dict(case, expect=exp)


def _fill(text, facts):
    for k, v in facts.items():
        text = text.replace("{" + k + "}", str(v))
    return text


_PLAIN_QUOTES = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"'})


def _plain(text):
    """Lower case with typographic quotes made plain: GPT writes can’t, and a phrase written can't must still match it."""
    return text.translate(_PLAIN_QUOTES).lower()


def score(case, record, facts):
    """[(rule, passed, detail)] for one case and what happened in it."""
    exp, out = case.get("expect") or {}, []
    tools = record["tools"]
    answer = _plain(record["final"] or "")
    for t in exp.get("tools_required", []):
        out.append((f"called {t}", t in tools, f"tools called: {tools}"))
    if exp.get("tools_any"):
        out.append((f"called one of {exp['tools_any']}", any(t in tools for t in exp["tools_any"]), f"tools called: {tools}"))
    for t in exp.get("tools_forbidden", []):
        out.append((f"did not call {t}", t not in tools, f"tools called: {tools}"))
    if "approval_asked" in exp:
        out.append(("approval asked" if exp["approval_asked"] else "no approval asked", (record["approvals"] > 0) == exp["approval_asked"],
                    f"{record['approvals']} approval question(s)"))
    if "run_started" in exp:
        out.append(("a run started" if exp["run_started"] else "no run started", record["started"] == exp["run_started"],
                    f"jobs started by the chat: {record['started']}"))
    for group in exp.get("answer_all", []):
        alts = [_plain(_fill(a, facts)) for a in group]
        out.append((f"answer says one of {alts}", any(a in answer for a in alts), ""))
    for phrase in exp.get("answer_none", []):
        out.append((f"answer does not say {phrase!r}", _plain(phrase) not in answer, ""))
    for kind in exp.get("plan_kinds", []):
        kinds = record.get("plan_kinds") or []
        out.append((f"a {kind} plan was put to the user", kind in kinds, f"plans put to the user: {kinds}"))
    if exp.get("keff_with_error"):
        out.append(("k-eff with its error", bool(_KEFF.search(record["final"] or "")), ""))
    if record.get("error"):
        out.append(("the turn ran without an exception", False, record["error"]))
    return out


# ----------------------------------------------------------------------------------------------------------------------
# the child process: fixtures and cases, inside the eval workspace
# ----------------------------------------------------------------------------------------------------------------------
def _write_sphere(folder, batches=8, particles=300, seed=1):
    import openmc
    folder.mkdir(parents=True, exist_ok=True)
    openmc.reset_auto_ids()
    m = openmc.Material(name="fuel_water")
    m.set_density("g/cm3", 1.0)
    for nuc, ao in (("H1", 2.0), ("O16", 1.0), ("U235", 0.004)):
        m.add_nuclide(nuc, ao)
    sphere = openmc.Sphere(r=40.0, boundary_type="vacuum")
    s = openmc.Settings()
    s.particles, s.batches, s.inactive, s.seed = particles, batches, 2, seed
    s.source = openmc.IndependentSource(space=openmc.stats.Point())
    openmc.Model(geometry=openmc.Geometry([openmc.Cell(fill=m, region=-sphere)]), materials=openmc.Materials([m]),
                 settings=s).export_to_model_xml(str(folder / "model.xml"))


def _wait(job_id, timeout=600):
    from tools.openmc import jobs, runs
    t = time.time()
    while time.time() - t < timeout:
        j = runs.check(job_id=job_id)
        if j["status"] not in jobs.ACTIVE:
            return j
        time.sleep(1.0)
    raise RuntimeError(f"fixture job {job_id} did not finish")


def fixtures():
    from tools.openmc import runs
    from tools.reactors.triga import build_input
    ws = Path(automc_paths.WORKSPACE)
    _write_sphere(ws / "sphere")
    _write_sphere(ws / "sphere-again")
    _write_sphere(ws / "sphere-pre")
    job = runs.start(runs.plan_run(case="sphere", threads=2), approved_by="eval-setup")
    done = _wait(job["job_id"])
    build_input("openmc", "netl", preset="netl_public_fresh_fuel", name="c-pub", run={"particles": 1000, "batches": 3, "inactive": 1})
    _files_and_kb(ws)
    k = done["keff"]["value"]
    return {"sphere_keff_2": f"{k:.2f}", "sphere_job": job["job_id"]}


def _files_and_kb(ws):
    """The general tools' fixtures (GT-6): a CSV of made-up rod worths, a .env with a fake key, and the public knowledge base eval-kb
    (one text chunk and one figure description with two labelled radii), embedded when Ollama's model answers."""
    from tools.documents import index, parse
    from tools.openmc import jobs
    (ws / "data").mkdir(exist_ok=True)
    (ws / "data" / "rod_worths.csv").write_text("rod,worth_dollars\nshim-1,2.95\nshim-2,2.80\nregulating,1.10\ntransient,1.65\n")
    (ws / ".env").write_text("OPENAI_API_KEY=sk-eval-not-a-real-key-0123456789\n")
    folder = ws / parse.DOCS / "eval-kb"
    folder.mkdir(parents=True, exist_ok=True)
    jobs.atomic_write_json(folder / parse.RECORD, {"kb": "eval-kb", "folder": "(an evaluation fixture)", "label": "public",
                                                   "label_reason": "public: an evaluation fixture", "status": "ready",
                                                   "text_mode": "nougat", "files": {"shield.pdf": {"status": "parsed", "pages": 1}},
                                                   "runs": [], "updated_at": jobs.now()})
    rows = {"01_chunks_kb.jsonl": {"source": "shield.pdf", "page": "1", "document_id": "evalkb1", "chunk_id": "evalkb1:p1:c0",
                                   "content": "Example 3-4. In a PWR the core is surrounded by a thermal shield (Fig. 3-7) to protect "
                                              "the pressure vessel from gamma-ray heating and neutron-induced radiation damage."},
            "02_visuals_kb.jsonl": {"source": "shield.pdf", "page": "1", "document_id": "evalkb1", "figure_id": "evalkb1:p1:f0",
                                    "figure_index": "0",
                                    "description": "**Subject:** Figure 3-7, the thermal shield of a PWR\n**Geometry & Labels:** a radial "
                                                   "section: the core barrel inside, the thermal shield outside it.\n**Dimensions & "
                                                   "Data:** labelled radii from the centreline: inner surface of the thermal shield R = "
                                                   "1.206 m; outer surface R = 1.333 m.\n**Context:** the shield protects the vessel."},
            "03_metadata_kb.jsonl": {"source": "shield.pdf", "document_id": "evalkb1", "title": "Thermal shield example"}}
    for name, row in rows.items():
        (folder / name).write_text(json.dumps(row) + "\n")
    idx = index.Index(folder, create=True)
    try:
        index.sync(idx, folder)
        try:
            index.embed_waiting(idx, index.make_embedder())
        except index.EmbedderUnavailable:
            pass                                           # searched by full text then
    finally:
        idx.close()
    _earlier_conversation(ws)


def _earlier_conversation(ws):
    """An earlier, public conversation of the workspace (WD-3) for chat-earlier: a made-up rod worth found then."""
    from tools.chats import store
    chat_id = "e7a1c0de-0000-4000-8000-00000000eva1"
    path = ws / automc_paths.STATE / store.FOLDER / f"{chat_id}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [{"event": "session", "format": store.FORMAT, "id": chat_id, "created_at": "2026-10-25T15:00:00+00:00"},
             {"event": "user", "text": "What transient rod worth did the eval-tr sweep give?"},
             {"event": "final", "text": "The eval-tr sweep gave a transient rod worth of 2.37 dollars (made up for the evaluation)."},
             {"event": "message", "label": "public", "message": {"type": "human", "data": {
                 "content": "What transient rod worth did the eval-tr sweep give?", "type": "human"}}}]
    path.write_text("\n".join(json.dumps(l) for l in lines) + "\n", encoding="utf-8")


def _slow_run():
    from tools.openmc import runs
    ws = Path(automc_paths.WORKSPACE)
    name = f"sphere-slow-{int(time.time())}"
    _write_sphere(ws / name, batches=60)
    os.environ["AUTOMC_WORKER_TEST_DELAY_S"] = "10"
    try:
        job = runs.start(runs.plan_run(case=name, threads=1), approved_by="eval-setup")
    finally:
        os.environ.pop("AUTOMC_WORKER_TEST_DELAY_S", None)
    t = time.time()
    while time.time() - t < 120 and (runs.check(job_id=job["job_id"])["batches"]["done"] or 0) < 1:
        time.sleep(1.0)
    return job["job_id"]


def _answers(case):
    """approve as a list of True/False. YAML reads an unquoted yes or no as true or false, so both spellings count."""
    approve = case.get("approve", "no")
    return [{"yes": True, "no": False, True: True, False: False}.get(x) for x in (approve if isinstance(approve, list) else [approve])]


def _approval_answer(case, n):
    """The harness's answer to the case's n-th approval question (from 0): approve is yes, no, or a list answered in order, its last
    answer repeating (a build approved, then its run declined: [yes, no])."""
    answers = _answers(case)
    return answers[min(n, len(answers) - 1)]


def run_case(case, provider, facts):
    from automc_core import Session
    from tools.openmc import jobs, runs
    record = {"id": case["id"], "tools": [], "approvals": 0, "plan_kinds": [], "answers": [], "final": "", "started": False,
              "error": None}
    slow = _slow_run() if "slow_run" in case.get("setup", []) else None
    before = {j["job_id"] for j in jobs.list_jobs(jobs.home(automc_paths.WORKSPACE))}
    t0 = time.time()
    try:
        s = Session(provider=provider, surface="chat")
        for turn in case["turns"]:
            events = s.send(_fill(turn, facts))
            while events is not None:
                nxt = None
                for ev in events:
                    if ev[0] == "tool_call":
                        record["tools"].append(ev[1])
                    elif ev[0] == "approval":
                        record["approvals"] += 1
                        record["plan_kinds"].append(((ev[1] or {}).get("plan") or {}).get("kind"))
                        nxt = s.answer(_approval_answer(case, record["approvals"] - 1))
                    elif ev[0] == "error":                       # the model call failed (agent/errors.py)
                        record["error"] = ev[1]
                    elif ev[0] == "final":
                        record["final"] = ev[1]
                        record["answers"].append(ev[1])
                events = nxt
        record["tokens"] = s.spent()
    except Exception as e:                                       # a crash is a failed case, not a failed evaluation
        record["error"] = f"{type(e).__name__}: {e}"
    record["seconds"] = round(time.time() - t0, 1)
    new = [j for j in jobs.list_jobs(jobs.home(automc_paths.WORKSPACE)) if j["job_id"] not in before]
    record["started"] = any(j.get("approved_by") == "chat" for j in new)
    for j in jobs.list_jobs(jobs.home(automc_paths.WORKSPACE), active_only=True):          # leave nothing running for the next case
        try:
            runs.cancel(j["job_id"])
        except Exception:
            pass
    record["slow_job"] = slow
    return record


def child_env(environ, workspace, run_folder, cloud):
    """The environment of the child that runs the cases. For a cloud model, nothing restricted is reachable from it: no restricted
    facility pack (an empty pack folder), no folders declared in the environment, no user settings (they may declare folders)."""
    import automc_settings
    env = dict(environ, AUTOMC_WORKSPACE=str(workspace))
    env["PYTHONPATH"] = os.pathsep.join([str(automc_paths.SRC)] + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))
    if cloud:
        packs = Path(run_folder) / "no-facility-packs"
        packs.mkdir(parents=True, exist_ok=True)
        env["AUTOMC_FACILITY_PACKS"] = str(packs)
        for name in [n for n, _ in automc_settings.ROOT_ENVS] + [automc_settings.OLD_ROOTS_ENV]:
            env.pop(name, None)
        env[automc_settings.USER_ENV] = str(Path(run_folder) / "no-user-settings.yaml")
    return env


def _carry_check(choice, eval_workspace):
    """A cloud model is used only once its check has passed, and that record is kept per workspace (agent/discovery.py). The eval's
    workspace is new, so it gets the real workspace's record of this one model, and nothing else of it."""
    from agent import discovery
    record = discovery._checks().get(choice)
    if not record:
        return
    path = Path(eval_workspace) / automc_paths.STATE / discovery._checks_path().relative_to(automc_paths.state())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({choice: record}, indent=1, sort_keys=True), encoding="utf-8")


def _restricted_pack():
    """The NETL restricted pack, which the needs: restricted_pack cases use, or None when it is not installed. Not from tests/:
    importing the tests package drops AUTOMC_ENV_FILE (tests/__init__.py), and a cloud model's key is in that file."""
    from tools.reactors.triga import packs
    return next((p for p in packs.installed().packs if p.id == RESTRICTED_PACK), None)


def _pack_note(pack, answers):
    """The report's line on the phrases taken from the pack: how many cases and which file (its hash), never the phrases."""
    if not answers:
        return None
    from tools.reactors.triga import packs
    sha = packs.sha256_file(pack.path(pack.provides["eval_answers"]))[:12]
    return f"- Answer phrases of {len(answers)} case(s) from the {pack.id} pack (eval answers sha256 {sha})"


def child(run_folder, provider_name, only):
    import dataclasses
    import automc_settings
    from agent import discovery, prompt
    version, cases = load_cases()
    p = discovery.resolve(provider_name)
    if p.cloud:
        if automc_settings.facility_mode():                     # child_env hides it all; never send a case if it did not
            print("refused: something restricted is still declared, and a cloud model is being evaluated", file=sys.stderr)
            return 3
        p = dataclasses.replace(p, max_tokens_per_session=max(p.cap or 0, CLOUD_CASE_CAP))
    pack = _restricted_pack()
    answers = pack_answers(pack, cases)
    facts = fixtures()
    _, info = prompt.system_prompt(p)
    results = []
    with open(Path(run_folder) / "results.jsonl", "a", encoding="utf-8") as fh:
        for case in cases:
            if only and case["id"] not in only:
                continue
            if case.get("needs") == "restricted_pack" and pack is None:
                rec = {"id": case["id"], "skipped": "needs the restricted pack" + (", which a cloud model is never given; run it with "
                                                                                  "the local model" if p.cloud else "")}
            elif (case.get("expect") or {}).get("answer_from_pack") and case["id"] not in answers:
                rec = {"id": case["id"], "skipped": "the restricted pack gives no answer phrases for it (provides.eval_answers)"}
            else:
                case = with_pack_answers(case, answers)
                rec = run_case(case, p, facts)
                rec["rules"] = [{"rule": r, "passed": ok, "detail": d} for r, ok, d in score(case, rec, facts)]
                rec["passed"] = all(r["passed"] for r in rec["rules"])
            rec["title"] = case["title"]
            fh.write(json.dumps(rec, default=str) + "\n")
            fh.flush()
            results.append(rec)
            print(f"{case['id']:24s} {'skipped' if rec.get('skipped') else ('PASS' if rec['passed'] else 'FAIL')}  {rec.get('seconds', '')}",
                  flush=True)
    report(run_folder, results, p, version, info, pack_note=_pack_note(pack, answers))
    return 0


def _manifest_sha():
    import hashlib
    from agent import prompt
    try:
        return hashlib.sha256((prompt.SKILLS_DIR / prompt.MANIFEST).read_bytes()).hexdigest()[:12]
    except OSError:
        return "none"


def report(run_folder, results, provider, cases_version, info, pack_note=None):
    done = [r for r in results if not r.get("skipped")]
    passed = sum(1 for r in done if r["passed"])
    lines = [f"# AutoMC evaluation, {Path(run_folder).name}", "",
             f"- Model: {provider.describe()}", f"- {cases_version}; skills: {', '.join(info['skills'])}",
             f"- Skills MANIFEST.json sha256 {_manifest_sha()}"] + ([pack_note] if pack_note else [])
    if provider.cloud:
        lines.append(f"- Cloud model: the restricted facility packs, folders and user settings were hidden, so nothing restricted was "
                     f"reachable; cases that need the restricted pack were skipped. Each case could spend up to {provider.cap:,} tokens.")
    lines += [f"- **Passed {passed} of {len(done)}**" + (f" ({len(results) - len(done)} skipped)" if len(done) < len(results) else ""),
              f"- Time: {sum(r.get('seconds', 0) for r in done) / 60:.0f} min", "",
              "| Case | Result | Seconds | Tools called | Failed rules |", "|---|---|---|---|---|"]
    for r in results:
        if r.get("skipped"):
            lines.append(f"| {r['id']} | skipped | | | {r['skipped']} |")
            continue
        failed = "; ".join(f"{x['rule']}" + (f" ({x['detail']})" if x["detail"] and not x["passed"] else "") for x in r["rules"] if not x["passed"])
        lines.append(f"| {r['id']} | {'pass' if r['passed'] else '**FAIL**'} | {r['seconds']} | {', '.join(r['tools']) or 'none'} | {failed} |")
    lines += ["", "## Answers", ""]
    for r in done:
        lines += [f"### {r['id']}: {r['title']}", "", (r["final"] or "(no answer)").strip(), ""]
    (Path(run_folder) / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


# ----------------------------------------------------------------------------------------------------------------------
# the command line
# ----------------------------------------------------------------------------------------------------------------------
def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m automc eval", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)
    sub.add_parser("list")
    r = sub.add_parser("run")
    r.add_argument("--case", action="append", default=[], help="only these case ids (repeatable)")
    r.add_argument("--provider")
    c = sub.add_parser("child")                                 # internal: the process that runs the cases
    c.add_argument("--run-folder", required=True)
    c.add_argument("--provider")
    c.add_argument("--case", action="append", default=[])
    a = ap.parse_args(argv)
    if a.command == "list":
        version, cases = load_cases()
        print(f"{version}: {len(cases)} cases")
        for case in cases:
            print(f"  {case['id']:24s} {case['title']}" + ("   (needs the restricted pack)" if case.get("needs") else ""))
        return 0
    if a.command == "child":
        return child(a.run_folder, a.provider, set(a.case))
    from agent import discovery, llm
    try:
        p = discovery.resolve(a.provider)                       # a provider's name, PROVIDER/MODEL, or the label the screen shows
    except llm.LLMConfigError as e:
        print(f"cannot use {a.provider or 'the default provider'}: {e}", file=sys.stderr)
        return 2
    _, cases = load_cases()                                     # refuse a broken case file before anything starts
    if not p.cloud:                                             # and broken pack answers (a cloud run never reads them)
        pack_answers(_restricted_pack(), cases)
    stamp = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H%MZ")
    folder = Path(automc_paths.WORKSPACE) / "evals" / f"{stamp}_{re.sub(r'[^A-Za-z0-9._-]+', '-', llm.bare_model(p.model) or p.name)}"
    ws = folder / "workspace"
    ws.mkdir(parents=True)
    if p.cloud:
        _carry_check(p.choice, ws)
    env = child_env(os.environ, ws, folder, p.cloud)
    cmd = [sys.executable, "-m", "agent.evals", "child", "--run-folder", str(folder)] + (["--provider", a.provider] if a.provider else [])
    for cid in a.case:
        cmd += ["--case", cid]
    print(f"evaluation in {folder}")
    if p.cloud:
        print(f"cloud model {p.label}: nothing restricted is reachable from the cases (the restricted packs, folders and user settings "
              f"are hidden), the cases that need the restricted pack are skipped, and each case may spend up to "
              f"{max(p.cap or 0, CLOUD_CASE_CAP):,} tokens")
    rc = subprocess.call(cmd, cwd=str(automc_paths.SRC), env=env)
    print(f"report: {folder / 'report.md'}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
