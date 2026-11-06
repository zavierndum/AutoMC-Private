# AutoMC Design

Status: **working draft, 2026-10-14.** Captures the decisions reached in the AutoMC design
session so far, and the questions still open. Everything marked *decided* was agreed between
Zavier and the assistant in conversation; everything marked *open* is not yet settled. This file
is the thing to argue against before code is written. `HANDOFF.md` is the briefing that preceded
it; `SETUP.md` is the environment; `TRIGA-Model/SCOPING.md` is the model-building path and the
validated runs. Section 8 (writing a model, and diagnosis), the milestones and the repository layout were brought up to the code on
2026-11-06; other sections carry their own dates where they changed.

## Table of Contents
- [1. What AutoMC is](#1-what-automc-is)
- [2. Scope boundaries](#2-scope-boundaries)
- [3. Decisions so far](#3-decisions-so-far)
- [4. Architecture: one backend, several surfaces](#4-architecture-one-backend-several-surfaces)
- [5. Execution layer: the OpenMC worker](#5-execution-layer-the-openmc-worker)
- [6. Workspace layout and job registry](#6-workspace-layout-and-job-registry)
- [7. The agent turn: interrupt, edit, resume](#7-the-agent-turn-interrupt-edit-resume)
- [8. Writing a model, and diagnosing failures](#8-writing-a-model-and-diagnosing-failures)
- [9. Delegation to peer agents](#9-delegation-to-peer-agents)
- [10. User interfaces](#10-user-interfaces)
- [11. Constraints that shape the design](#11-constraints-that-shape-the-design)
- [12. Open questions](#12-open-questions)
- [13. Milestones](#13-milestones)
- [14. What was verified, and where](#14-what-was-verified-and-where)
- [15. Repository layout](#15-repository-layout)

## 1. What AutoMC is

An agentic wrapper around OpenMC for Monte Carlo reactor-physics work, starting with the NETL
TRIGA model built through CoreForge and `progression_problems`. It covers model authoring, run
execution and management, failure diagnosis and repair, post-processing and reporting, and a
document-grounded assistant over OpenMC's own documentation plus the two NETL TRIGA source
documents CoreForge cites for every dimension and material: the NRC licensing document
ML23279A146 (the redacted NETL Safety Analysis Report supporting the Aug 2023 license renewal,
License R-129, 324 pages) and the Sandia report SAND2022-15938 by Redhouse et al., "Radiation
Characterization Summary: NETL Beam Port 1/5 Free-Field Environment at the 128-inch Core
Centerline Adjacent Location" (Nov 2022, DOI 10.2172/1898256, 84 pages). Note the second is a
Sandia report, not ORNL as the handoff said. Both PDFs are now in `AutoMC/references/` as
`ML23279A146.pdf` and `1898256.pdf` and are the seed corpus for the assistant.
It follows the same lineage as AutoFLUKA, AutoSAM and RADIANT-LLM and reuses their proven parts,
but is built new so that two requirements can be met from the first line rather than retrofitted:

1. A CLI and a web UI that call one shared backend.
2. A running simulation, and the agent's own reasoning turn, can both be interrupted, inspected,
   redirected, and resumed while the user keeps talking to the agent.

AutoMC will be used by people outside UT NETL. It must therefore run standalone (one Docker image,
one port, local WSL or Linux) without depending on NeutronOS, Open WebUI, Rascal or the NETL
Trame stack. Integration with those is done through explicit seams (section 4), followed when they
become stable, never as a dependency.

## 2. Scope boundaries

- **Not the TRIGA Control Room Agent.** That is a separate agent, separate environment, separate
  chat session, deployed near the control room around `triga_modsim_tools`' EKF/MPC code. The two
  talk only over A2A, and only if one ever needs the other. Nothing under
  `TRIGA-Control-Room-Agent/` is read from here except `known_issues.md` and `FIXES.md` as the
  documentation convention to copy.
- **OpenMC only for execution.** CoreForge and MPACTPy are used to build geometry. AutoMC does not
  run MPACT or VERA. If that ever changes, the `810` export-control tag on VERA makes TACC execution
  of that code tier uncertain and needs its own licensing review.
- **Local first.** Version 1 runs on the WSL `automc` environment. TACC/Slurm submission is a
  designed-for later step, not part of the first milestone (section 11 explains why).

## 3. Decisions so far

Dated so later readers can tell what was settled when.

| Date | Decision | Why |
|---|---|---|
| 2026-10-14 | **Execution engine follows AutoSAM's Start/Check/Cancel contract and job registry**, not AutoFLUKA's blocking executor. | Verified in code: AutoSAM already launches detached, tracks jobs in a two-tier JSON registry with a done sentinel and PID liveness, cancels via `scancel` or process-group kill, and reconciles on restart. AutoFLUKA's executor blocks. |
| 2026-10-14 | **Diagnosis follows AutoFLUKA's pattern**: deterministic fatal-signature detector, Markdown error knowledge base with fuzzy matching, fix notes logged per attempt, anti-loop rules. | It is the mature half of AutoFLUKA, and OpenMC's error set is small enough to seed the knowledge base from source. |
| 2026-10-14 | **Build pause-and-resume at batch granularity for OpenMC itself**, through the C API worker (section 5). | OpenMC exposes batch-level control and statepoint restart. SAM and FLUKA never offered this; it is a genuine AutoMC capability and the natural partner of the interruptible agent turn. |
| 2026-10-14 | **The agent turn is interruptible independently of the simulation** (section 7). | The older apps lock the chat while a turn runs, because the turn is a blocking call in an anonymous thread. Diagnosed in the 2026-09-23 "Stop button" entry of the AllApps updates log; deferred there because it meant reworking the executors. AutoMC is new, so it is designed in. |
| 2026-10-14 | **Peer-shaped delegation**, each agent carries the same handoff module and asks its user before sending anything to a peer. No central gateway. | Matches how the apps are deployed (standalone products). Degrades gracefully when a peer has no card or is down. Incremental, since only demo stubs have cards today. |
| 2026-10-14 | **UI: evolve the Auto-* React/Vite frontend into a shared component package**; add an OpenAI-compatible chat shim so AutoMC also appears in Open WebUI; treat Jonathan Salem's Trame DT UI as an integration seam, not a build target. | External users need standalone. Pause/resume, job panel, permission dialog need UI state that a chat shell does not model. Today's NETL DT weekly set Trame as the DT view direction. |
| 2026-10-14 | **One folder per case, one child folder per attempt, JSON registry at the workspace root.** Attempt folders are `run-001`, `run-002`, …; production folders, if the test/production split is kept, are `production-001`, …. | OpenMC filenames carry no case identity, so same-folder reruns silently overwrite results, break restart (statepoint must match its exact model) and collide with HDF5 file locks. Zero padding is cosmetic; code formats and parses through one helper and sorts by the parsed integer. |
| 2026-10-14 | **Every result is a schema'd artifact** (JSON summary, statepoint HDF5, PNG plots) so any view layer can render it. | Keeps the UI replaceable and gives the Trame/Open WebUI seams something concrete to consume. |
| 2026-10-14 | **MCP-compatible tool interface from day one**, in the shape NeutronOS's `model_corral` extension uses: handler takes an args dict, returns a JSON-serializable dict, one service layer behind CLI, UI, MCP. | Prometheus ConOps mandates MCP for agent-to-tool and A2A for agent-to-agent, with retrofit of older agents a budgeted cost. Cheaper to conform now. |

## 4. Architecture: one backend, several surfaces

```
                      +---------------------------------------------+
   CLI  ------------->|                                             |
   Web UI (React) --->|  AutoMC backend (FastAPI, async)            |
   Open WebUI ------->|  /v1/chat/completions shim                  |
   MCP clients ------>|  MCP tool server (same handlers)            |
   Peer agents ------>|  A2A agent card + task endpoint             |
                      |                                             |
                      |  agent core (turn manager, router, tools)   |
                      |  job service  <->  job registry (JSON)      |
                      |  event bus (batch events, turn events)      |
                      +------------------+--------------------------+
                                         |
                              OpenMC worker process(es)
                              (openmc.lib, one per running job)
```

Rules that hold the shape together:

- **The backend is the product; every surface is a thin client.** A feature that exists in the UI
  but not in the CLI, or the reverse, is a bug.
- **Tools are the unit of capability.** Each tool is a plain function with a typed args dict and a
  JSON result, registered once and exposed three ways: to the agent's LLM, over MCP, and as a CLI
  verb. This is the `model_corral` pattern (`mcp_tools.py` handlers reusing the CLI's service).
- **No tool blocks on a simulation.** Tools that touch runs are start, check, pause, resume,
  cancel. Each returns in under a few seconds. This is what keeps the agent turn interruptible
  and is inherited directly from AutoSAM's "Execution Timing Policy".
- **The job registry is the source of truth, not agent memory.** An interrupted or restarted
  agent loses nothing about running jobs.
- **Events, not polling, inside the backend.** The worker publishes batch events; the UI stream,
  the CLI progress display and the agent's own "has this stalled" check all subscribe. Copied
  from the EKF branch's ZMQ publish/subscribe fan-out, translated to an in-process bus plus
  WebSocket/SSE outward.

## 5. Execution layer: the OpenMC worker

Two execution modes behind one interface. Both record into the same registry and emit the same
events.

**In-process C API mode (local, default).** A dedicated worker process per job imports
`openmc.lib`, calls `init` on the attempt's `model.xml`, then `simulation_init`, then drives
`next_batch` in a loop. Verified present in 0.15.3 (`openmc/lib/core.py`): `init`,
`simulation_init`, `next_batch` returning 0 normal / 1 max batches / 2 tally triggers met,
`keff`, `current_batch`, `statepoint_write`, `simulation_finalize`, `finalize`. After each batch
the worker publishes an event with batch number, k-eff and sigma, Shannon entropy if an entropy
mesh is set, and wall time. Control messages arrive on a queue the worker checks between batches:

| Command | Worker action | Registry status |
|---|---|---|
| pause | finish current batch, write a statepoint, stop calling `next_batch`, keep the process alive | `paused` |
| resume | continue the loop from where it stopped | `running` |
| checkpoint | write a statepoint, continue | `running` |
| stop (graceful) | finish current batch, write statepoint, `simulation_finalize`, `finalize`, exit | `stopped` |
| kill | process-group kill, no statepoint beyond the last written | `cancelled` |

"Pause" therefore costs nothing but the process staying resident, and "resume" is immediate. If a
paused worker dies or the machine restarts, the last statepoint plus the frozen `model.xml` in the
attempt folder allow a cold resume with `openmc.run(restart_file=...)`, which only works because
the attempt folder is immutable once started (section 6).

**Subprocess mode (TACC/Slurm later, and a fallback locally).** Launch the `openmc` executable
detached, `setsid` plus PID file locally, `sbatch` remotely, exactly AutoSAM's two backends
(`sam_tools._build_detached_launch`, `sam_ssh_tools._submit_direct` / `_submit_slurm`). Progress
comes from tailing the captured log rather than from batch events. Pause is not available; stop
is kill, and resume is a restart from the last statepoint written by `Settings.statepoint`
batches. A done sentinel is appended to the log, AutoSAM's `SAM_JOB_DONE rc=<n>` pattern.

**Convergence as a first-class signal.** The worker's event stream carries k-eff, sigma and
entropy per batch, so the agent can reason about convergence while the run is in flight (for
example: "entropy has flattened, inactive batches look sufficient"; "sigma target met, stop
early"). Tally triggers in `Settings` give the deterministic version of the same idea.

## 6. Workspace layout and job registry

Since 0.8.0 (2026-11-01, `devops/AutoMC_Workspace_Plan.md`, WD-2) everything AutoMC keeps in a workspace is in one hidden folder,
`.automc/` (settings.yaml, format.json, chats/, jobs/, documents/, python/, web/, models/); the registry is `.automc/jobs/registry.json`.
A case may sit up to four folders deep (`study-8/c8`). `python -m automc workspace upgrade` moves an older workspace's items in.

```
<workspace>/
  .automc/jobs/registry.json         registry (see fields below)
  <case-name>/                       one case = one model identity
    case.json                        builder module+function, parameters, hash of model.xml,
                                     provenance (which run script / agent turn created it)
    run-001/                         one attempt; immutable once the worker starts
      model.xml                      frozen input for this attempt
      run.log                        captured stdout+stderr, ends with AUTOMC_JOB_DONE rc=<n>
      statepoint.<N>.h5 ...          checkpoints and the final statepoint
      summary.h5  tallies.out
      particle_*.h5                  only when particles are lost
      plots/                         geometry PNGs produced for this attempt
      result.json                    parsed k-eff, entropy history, timing, status, diagnosis
    run-002/                         a diagnose-and-fix produces a new model.xml here, never in run-001
```

Naming: one helper formats `run-{n:03d}` and one regex `^run-(\d+)$` parses it; all sorting is by
the parsed integer. Three digits because trials have never exceeded 15 in practice (AutoFLUKA's
record) and the margin is free. If a test/production split is kept, `production-{n:03d}` follows
the same helper. Changing AutoFLUKA's own `test-N` folders to padded names is a separate, later
patch in that codebase.

Registry record fields (superset of AutoSAM's `sam_job_registry.create_job`):
`job_id`, `case`, `attempt`, `backend` (`lib` | `local_subprocess` | `ssh_direct` | `ssh_slurm`),
`status`, `pid`, `pid_started_at`, `pid_namespace` (`wsl` | `native` | `remote`),
`remote` (scheduler, remote id, remote log), `created_at`, `updated_at`,
`cancel_requested`, `derived_from` (previous attempt and a one-line diff summary),
`last_checkpoint_batch`, `batches_total`, `keff_latest`, `error`.

Statuses: `queued`, `running`, `paused`, `stopped`, `success`, `failed`, `non_converged`,
`cancelled`, `interrupted`. The last one is assigned by startup reconciliation
(AutoSAM's `reconcile_jobs_on_startup`) when a job was active in the registry but its process
is gone and no sentinel was written.

Disk hygiene: keep-last-N checkpoints per attempt, configurable, because batch-level pausing with
mesh tallies is the one place disk use can run away. Final statepoint and `result.json` are
always kept.

## 7. The agent turn: interrupt, edit, resume

Three different things are kept as three different mechanisms:

1. **Control the simulation**: pause, resume, stop, kill (section 5). Exposed as agent tools and
   also as direct UI/CLI actions that do not go through the LLM.
2. **Interrupt the agent's turn without touching any simulation.** Every turn has a `turn_id`
   and runs as a cancellable task. The interrupt takes effect at the next step boundary or the
   next streamed LLM token, never mid-tool, which is safe because no tool blocks (section 4).
   Jobs the turn started stay in the registry; nothing is lost. If the framework is LangGraph this
   is its native `interrupt()` plus a checkpointer; if Axiom-native, a hand-rolled step loop that
   checks a stop flag between nodes. Either way the contract is the same.
3. **Edit the prompt and resume.** Two sub-cases. *Retract and re-issue*: cancel the turn, roll
   conversation state back to before it, submit the corrected prompt; the new turn's context says
   "job X from the previous attempt is still running" so the model can reuse or cancel it.
   *Inject mid-turn*: pause at a step boundary, append a user message to the turn state, resume.

User-facing consequence: the chat input is never locked. A message sent while a turn is running
offers three choices: queue it, interrupt and replace, or inject. The current Auto-* apps lock
the input because the SSE stream is tied to the turn thread; decoupling turn execution from the
stream is the whole fix.

Turn state machine:

```
idle -> running -> (completed | interrupted | failed)
          |  ^
          v  |
        paused  --(edit state)--> running
```

## 8. Writing a model, and diagnosing failures

Both parts were written from the code on 2026-11-06; the files are named where each claim is made.

### 8.1 Writing a model

The NETL TRIGA has its own builder (`TRIGAInputBuilderTool`, section 1). For any other model the agent writes a Python build script,
`build.py`, with the OpenMC Python API, the way OpenMC is meant to be used, and the tools below build, draw and check it
(`src/tools/openmc/authoring.py`, `_author_child.py`, `authoring_tools.py`).

1. **Build** (`OpenMCModelBuildTool`). The tool only plans: the user sees the script and approves it.
   - The script's text is refused when it would run transport or start a program: `.run(`, `run_in_memory`, `search_for_keff`,
     `.integrate(`, stepping `openmc.lib` through batches, `subprocess`, `os.system` (`authoring.py:53`). The refusal says that
     transport is started by `OpenMCRunStartTool` after the user's approval.
   - The build process also replaces those functions with ones that refuse, in case a script reaches them another way
     (`_author_child.py`, `disarm`).
   - The script runs in a staging folder beside the case, as `python -I` (no AutoMC modules, no user site), without keys, within a
     time limit (300 s by default, at most 1,800). Only a build that wrote `model.xml` becomes the case, so a failed build leaves
     nothing behind.
   - The case gets `model.xml`, `build.py`, `build.log` and `result.json` (the script's SHA-256, the versions, the approval, the data
     label). A case that has runs is never rebuilt (`authoring.py:114`).
2. **Draw** (`OpenMCGeometryPlotTool`): slices through OpenMC's plot mode (no transport), coloured by material or by cell, with the
   source marked and overlaps drawn red.
3. **Check** (`OpenMCModelCheckTool`, `_author_child.py:370`): the model read back from `model.xml`, before any run.
   - The files it names (CAD geometries, meshes): missing, outside the case, or named by an absolute path.
   - Nuclear data: nuclides and thermal scattering tables the library lacks, or a multigroup library that is not there.
   - Materials that mix atom and weight fractions.
   - Settings: too few inactive batches, an eigenvalue run with no fissionable material, a fixed-source run with no source, too few
     histories.
   - Boundaries: no vacuum surface.
   - The source: sampled points that are in no cell.
   - Overlaps, through OpenMC's plot mode on the default slices and through two source points. A slice lying exactly on a surface of
     the model is told apart from a real overlap by moving it a little each way.
   - CAD files: touching volumes that hold two copies of a face instead of sharing one (not merged). The implicit complement then
     claims the points beside each copied face, which reads as an overlap although no volume crosses another; the finding names the
     pairs and puts the fix in the CAD export.
   - Tallies: a mesh outside the geometry and, for a fixed source, how the tallies are normalised.

   The model passes only without errors, and each finding says where its fix goes (`build.py`, or the CAD program for a CAD file).
4. **Run** through the job service (sections 5 and 6), after the user's approval. Work that runs transport from inside Python (a
   criticality search, a depletion, a loop over `openmc.lib`) is a **script run**: `OpenMCRunStartTool` with the case and the text of
   `run.py`, approved like any run.
   - It runs in a new run folder holding a copy of the case's files, as its own process group, without keys, within a time limit (an
     hour by default, at most a day) (`src/tools/openmc/script_worker.py`, `runs.py:49`).
   - It can be stopped or cancelled, not paused, and it does not continue from a statepoint: it is planned again.
5. **Read** the result (`RunResultReaderTool`) and analyse it with `PythonRunTool`, as each example's `post.py` does.

**What the agent is told, and what it reads when it needs it** (`src/skills/SKILL_POLICY.md`, `src/agent/prompt.py`):
- **In the prompt:**
  - the system prompt (`src/prompts/system_prompt_automc.yml`). Its section "Writing a model" gives the order above. Its first step is
    to ask, in one message, for what the question leaves open, or to choose a standard value and call it an assumption;
  - `src/skills/index.md`, and every bundled skill's `SKILL.md`.

  The prompt is sized for a 128k-token context.
- **On demand,** with `SkillLookupTool` and a path under `src/skills` (`agent/skill_tool.py`):
  - the rule files: 18 for authoring (one per part of a model) with build-script templates, 4 for validation (the review before and
    after a run), the troubleshooting rules, and 5 for post-processing;
  - the error knowledge base (section 8.2);
  - the working examples.
- **The working examples** (`src/skills/working_examples/`, 29, listed in its `index.md`). Each has:
  - a README with its source;
  - `build.py`, with an `OPTIONS` line for its variants, or `analysis.py` for nuclear data processed without a model;
  - `expected.json`, the checks its results must pass;
  - `post.py`, when it has results.

  The agent starts from the nearest example and says which one it adapted. Five of them need files AutoMC does not ship (CAD
  geometries, meshes, ENDF and multipole files). `data.json` lists each file with its source and SHA-256, and `python -m automc data
  fetch` puts them in the data cache (`src/tools/openmc/example_data.py`; SETUP.md section 7k).

**How it is tested:**
- **`src/tests/skills/`:**
  - the skill files hold together (`test_skill_files.py`);
  - nothing in the skills or the prompt is copied from OpenMC's documentation, examples or notebooks (`test_originality.py`);
  - what the troubleshooting and post-processing rules say about OpenMC is checked against the installed package
    (`test_rule_facts.py`).
- **The slow tier** (`AUTOMC_SLOW_TESTS=1`, WSL with OpenMC; `src/tests/examples/`). Every variant of every example is built through
  the build tool, checked, run through the job service, and its results compared with its `expected.json`. On 2026-11-05: 42 tests,
  417 s.
- **The evaluation cases** (`src/evals/cases.yml`, 47).
  - Eight take this path from a description to its result: a dose question that leaves the source and the dose quantity open, a
    benchmark and a fixed-source run read back after they finish, a hexagonal lattice drawn, a build script that tries to run
    transport, a run that lost particles, and a CAD model that is not merged.
  - The harness waits for a run between turns, as a user would, while the agent never waits (`src/agent/evals.py`).
  - Not yet put to the local and cloud models.

### 8.2 Diagnosis

The design of 2026-10-14 ported AutoFLUKA's diagnosis: a detector of fatal signatures, a Markdown knowledge base with a TF-IDF and
fuzzy matcher, an attempt ladder and fix notes. What exists:

- **The error knowledge base** is `src/skills/ERKB.yaml`, in AutoSAM's ERKB format. It has 23 entries, each with:
  - `detection_hint`, a substring of a log or an error message;
  - `signature`, `background`, `suggestion` and `confidence`;
  - `origin`: 21 were met in this project, 2 come from OpenMC's source or documentation.

  There is no TF-IDF or fuzzy matching: an entry matches when its hint occurs in the text.
- **It is matched in code, against every failed result.** Every tool call passes through `agent/erkb.py` (`src/tools/registry.py:43`).
  - A result that reports a failure (ok false, a failed job or Python run, a failed build or check) gets `known_errors`: the signature,
    suggestion and confidence of each entry whose hint occurs in its text. The end of a long log is searched too.
  - A known error is thus recognised every time, not left to the model's memory.
  - The file itself is not in the prompt; the model reads it on demand.
  - The slow tier provokes each message it can and checks that its hint is still in what OpenMC, h5py or NumPy write
    (`src/tests/skills/test_erkb_messages.py`). An upgrade that changes a message fails a test, not a user's run.
- **The model check** (section 8.1) finds, before a run, what would make the run fail or give wrong numbers without failing.
- **The troubleshooting rules** (`src/skills/openmc-execution/rules/troubleshooting.md`, read on demand) cover what has no message to
  match:
  - crashes with no error line;
  - lost particles. A particle file holds where its history started, not where it was lost; a script run replays it to find that
    place;
  - geometry-debug runs and how far they reached;
  - checks of CAD files, and depletions that hang.

  Convergence is judged from k by batch and the Shannon entropy (`src/skills/openmc-validation/rules/convergence.md`).

Still plans, not built:
- **The attempt ladder.**
  - A fix is a new build by the agent, approved by the user like any other; since a case with runs is never rebuilt, a fixed model
    that has been run is a new case.
  - Nothing yet counts automatic attempts or compares the `model.xml` hashes of attempts.
  - A job's `derived_from` is written empty (`src/tools/openmc/runs.py:452`).
- **Fix notes** per case (`automc_run_fixes.md`), and the `known_issues.md` / `FIXES.md` record of bugs found in OpenMC, CoreForge,
  MPACTPy or `progression_problems`.
- **A `non_converged` status.** It is in the list of statuses (`src/tools/openmc/jobs.py:33`), but no code sets it.

## 9. Delegation to peer agents

Each agent, AutoMC included, carries the same handoff module. No central gateway.

1. **Capability catalog, built at runtime** from the A2A agent cards of known peers and the MCP
   tool lists of attached servers. No routing knowledge lives in code. The Prometheus Agent
   Registry becomes another feed when it exists.
2. **Classifier with structured output.** Input: user prompt, recent state, catalog. Output:
   `{target: self | agent_id, skill_id, confidence, params, rationale}`, with `target`
   constrained to the catalog. A cheap embedding pass over skill descriptions runs first; the LLM
   call happens only on ambiguity. Below a confidence threshold the agent asks instead of guessing.
3. **Permission gate before any network call.** The turn pauses (section 7) and shows: "This
   looks like a RADIANT-LLM question (regulatory dose limits). Send it there? yes / no / edit".
   The dialog shows which model and host the peer runs on, taken from its card, which is also
   where the data-governance check lives (section 11).
4. **Deterministic dispatcher**: A2A send, stream the peer's progress into the local chat, relay
   the result, own timeouts and fallback. A2A `cancel()` is wired to the interrupt mechanism.

Guardrails: a delegation depth limit; permission is always asked of the human at the originating
surface, so a second hop by a peer comes back as an A2A input-required request rather than being
approved by the peer's own logic.

### 9.1 Requests that arrive from peers: staged, never run on arrival (added 2026-10-23)

The other direction of the same rule. When a peer sends AutoMC a task over A2A (the main case: the Control Room Agent asks for simulations at
rod positions it believes match the real core today, since the SAR's values age with burnup and rod changes), AutoMC does not act. It turns
the request into a **staged prompt** and waits for its own user.

- **What is shown:** who asked, the request in plain words, and what AutoMC would do with it: the configuration, rods (console units, as the
  Control Room Agent uses), run size, the estimated run time, and the assumptions and warnings the spec layer already produces.
- **Web UI:** the staged prompt appears as a card in the chat (the pattern borrowed from AutoSAM's geometry builder). Buttons: *Move to input*
  puts the text into the message box, where the user edits it and sends it as an ordinary turn; *Discard* rejects it. Nothing runs until the
  user sends.
- **CLI:** the staged prompt is printed above the input line with the question `Send this request? [y = send / n = edit / d = discard]`.
  `y` submits it as written; `n` puts it into the input line, pre-filled and editable (`prompt_toolkit` supports a default text); `d` rejects it.
- **A2A states:** the peer's task stays `input-required` while the prompt is staged, becomes `working` when the user sends it, and ends
  `completed` with the result artifact, or `rejected` with the user's reason. The peer's own user approved sending it (section 9, step 3), and
  AutoMC's user approves running it: two gates, one on each side.
- **What goes back:** k-eff with its uncertainty, the spec hash and library profile, the assumptions in force, and the model's known bias (for
  example how far above critical configuration 0 is at the logged critical height, from the validation notes), so the peer
  never receives a bare number.
- **Runs are long.** A standard run is an hour or more, so a request becomes a job (section 6); the peer gets progress updates and the result
  when the job ends, and `cancel()` from the peer stops it.

Prerequisites and limits, so this is not promised too early:
1. The builder can already take real rod positions in console units for any of the ten configurations. It cannot yet represent the core's
   **current burnup**: every model uses the reference BOC505 fuel compositions. "Current reactor conditions" beyond rods and temperatures needs
   new fuel compositions (a later block: depletion, or composition sets from the facility).
2. **Data governance:** rod positions from the live system are real NETL data, so while such a request is in context AutoMC must run on a
   self-hosted model (section 11).
3. It needs the job tool and the A2A card (milestone M3) and the run tool's own permission (the builder never runs transport).
4. Not found yet: the AutoSAM prompt-staging code. `frontend/src/App.tsx` only opens the geometry builder in a new tab, and `Geometry_app.py`
   has a YAML editor and export; the hand-off may live elsewhere (to ask Zavier).

Bootstrapping cards: AutoMC's card is written as part of this design and becomes the reference.
Cards for Auto-* and RADIANT-LLM are generated from their system-prompt YAML and skill manifests,
three to five skills each, approved by Zavier. The Control Room Agent gets no card until it
exists; an absent peer is simply never offered.

A labeled routing evaluation set (fifty to a hundred prompts with correct destinations) is part
of the first delegation milestone, so routing accuracy is a measured number and the demo's
keyword list becomes the baseline. This ties to the NuclearBench methodology.

## 10. User interfaces

- **Native web UI**: the Auto-* React 18 + TypeScript + Vite frontend, split from its current
  single `App.tsx` into a shared component package all four apps import. Already provides SSE
  streaming, alerts panel, sessions, skills catalog, model selection, light/dark themes. AutoMC
  adds: jobs panel over the registry, run monitor (k-eff, sigma, entropy versus batch) on a
  drag-and-drop grid, interrupt and pause controls bound to `turn_id` / `job_id`, delegation
  permission modal, geometry and tally viewer over the artifact schema.
- **CLI**: same backend, same tools as verbs (`automc run start|check|pause|resume|stop`,
  `automc chat`, `automc jobs`, …). Neither Auto-* app has a CLI today, so this is new.
- **OpenAI-compatible shim** on the same FastAPI app so AutoMC is a selectable model in Open WebUI
  on Rascal. Chat only; fine for that audience. Floated in the Aug 2026 notes, about a day of
  work once the backend exists.
- **Trame seam**: the Control Room Agent embeds in Jonathan's Trame UI, not AutoMC. AutoMC's
  contribution is data: artifacts a Trame widget can render.

## 11. Constraints that shape the design

- **TACC export control.** No AI tool may read or operate on files living on Lonestar6 without
  Ben Booth's explicit sign-off. The SSH backend is therefore designed so the agent only ever sees
  what the backend copies back to the local machine (log tail, result files it requested), and no
  TACC path is exercised, even in tests, until that clearance exists.
- **Data governance.** Any LLM touching real NETL/Rascal data goes through UT Premium Claude
  (interactive) or Portkey (programmatic), never a consumer account. As built (2026-10-26): restricted facility data reach only a local
  model; a cloud model never receives a result labelled restricted or unknown (`src/agent/guard.py`); switching a conversation that
  holds restricted data to a cloud model is refused (a new conversation is offered), one holding data of unknown origin needs a typed
  confirmation; the only exception is a two-key policy decided in advance by the data owners (`src/config/llm.yaml`, never for a
  personal account). Models offered to the chat (`src/agent/discovery.py`): local Ollama models with tools, vision and thinking;
  cloud only OpenAI (gpt-4 on) and Gemini (gemini-3 on) with RADIANT-LLM's filters, each discovered cloud model only after one
  passed check. A discovered model keeps its provider's kind and account. Tracing (2026-10-31): LangChain's own switches are off, and
  LangSmith receives a model call only when that call could go to a cloud model as it is: a public conversation and a prompt without
  restricted pack skills (`src/agent/tracing.py`). Keys come from the shell or a keys file outside AutoMC, and those from the file never
  enter the environment of a child process (`src/agent/keys.py`), with one exception: a Visual-Parser parse of a public document whose
  page images go to the chat model's cloud service gets that one service's key. Every other parse gets every key variable set empty
  and uses the local vision model (2026-10-31, `src/tools/documents/parse.py`). Embedding is always local (Ollama's
  `nomic-embed-text`). What the model writes for the web (a query, a URL) leaves the machine only from a public conversation, from one
  holding data of unknown origin only after the user types 'yes, send', and never from one holding restricted data, with any model
  (2026-11-01, `src/tools/web/gate.py`). Python runs in a kernel without keys, each run approved by the user with the code shown; its
  output carries no label, so a cloud model reads it only without a restricted pack or after the user typed 'yes, send'
  (`src/tools/python/run.py`); otherwise the user sees it in the chat and the model gets a note (a ToolMessage's `artifact`, which
  LangChain never sends to a model, 2026-11-01). The screen names a model `KIND-SERVICE/MODEL` and never shows a Portkey workspace's
  `@slug/`, which names the account (`src/agent/llm.py`, `Provider.label`). Details: `devops/AutoMC_Agent_Models_Plan.md`, `devops/AutoMC_General_Tools_Plan.md`. The delegation permission
  dialog is where a prompt leaving one agent for another is checked.
- **Git.** The assistant never commits or pushes; it hands Zavier the exact commands. No AI
  co-author trailers. (This directory is not a git repository yet.)
- **External users.** Standalone deployment, Docker, no dependency on UT infrastructure.
- **Framework.** LangChain/LangGraph for the agent core, wrapped later as a thin AEOS extension,
  is the precedent-backed default; Axiom-native remains open (section 12). The tool contract and
  the registry are framework-independent either way.

## 12. Open questions

1. **Framework for the agent core**: LangGraph (matches the code being ported, native interrupt
   and checkpointing) versus Axiom's runtime from the start. Leaning LangGraph; not decided.
2. **Test/production split**: keep AutoFLUKA's capped-particles `test-N` then `production-N`
   ladder, or let OpenMC's cheap short runs plus tally triggers replace it? Leaning toward a
   simpler single `run-N` ladder with a "preflight" attempt flag.
3. **MPI.** Local runs use OpenMP threads only. Whether the C API worker should ever be an MPI
   launch (and thus lose in-process control) or whether MPI is subprocess-mode only.
4. **Where the classifier model runs** for routing in each agent, and which small model.
5. **AutoMC agent card contents**: exact skill list and wording. Draft belongs in the first
   delegation milestone.
6. **Artifact schema** version 1: which fields, HDF5 versus JSON for tally arrays, how plots are
   referenced.

## 13. Milestones

- **M0, slice** (built 2026-10-25 as Block 5 phase B5-2, for the TRIGA cases, and validated on them 2026-10-31: a standard-size job
  reproduces the hand run, a paused and resumed one a straight one in k and every tally; the progression-problem scripts are not yet run
  as managed jobs): job service + registry + C API worker, driven by a CLI only. Run problem 1
  through 4E from the existing scripts as managed jobs; pause, resume, stop, kill all working;
  startup reconciliation working. No LLM involved yet.
- **M1, agent** (built 2026-10-25 as Block 5 phase B5-3: LangGraph core, approval interrupt, data guard, CLI chat,
  evaluation set; the progression-problem builders are not done, and the knowledge base has been matched in code since 2026-11-04,
  section 8.2): turn manager with interrupt/edit/resume; start/check/pause/resume/cancel as
  tools; model-building tools over `progression_problems`; run problem 5 as the first
  agent-managed run; diagnosis detector and knowledge base seeded from source.
- **Authoring** (built 2026-11-01 to 11-06, section 8.1): any OpenMC model from a build script, built, drawn and checked before a run;
  script runs; 29 working examples, each tested in the slow tier, with the data five of them need; rule files read on demand; the
  error knowledge base matched against every failed result; 47 evaluation cases. Next: the cases put to the local and cloud models,
  and the prompt measured against a 128k context.
- **M2, surfaces**: React UI with jobs panel and run monitor; OpenAI shim; MCP tool server;
  artifact schema v1.
- **M3, delegation**: handoff module, catalog, classifier, permission gate, staged prompts for requests from peers (section 9.1), AutoMC agent card,
  routing evaluation set; first peer test against a RADIANT-LLM card.
- **Later**: SSH/Slurm subprocess backend for TACC, after export-control clearance.

## 14. What was verified, and where

Claims above were checked against code on 2026-10-14, not taken from docs or memory:

- AutoSAM async engine and registry: `ZavierNdum/Code/AutoSAM_Devops_VP_Updated/tools/sam_tools.py`
  (`_LIVE_POPEN`, `SAMStartExecutor`, `SAMCheckExecutor`, `SAMCancelExecutor`),
  `tools/sam_ssh_tools.py` (`SSHSAM*Executor`, `verify_remote_job`),
  `tools/sam_job_registry.py`; execution prompt policy in `system_prompt_autosam.yml`.
- AutoFLUKA diagnosis: `AutoFLUKA_NeWAPP/tools/fluka_tools.py` (`_detect_fatal_run_signatures`,
  `fluka_executor` is synchronous), `utils/fluka_error_resolver.py`,
  `utils/fluka_hybrid_error_resolver.py`, ladder rules in `system_prompt_autofluka.yml`.
- Stop-button deferral: `H:\My Drive\Temp_files-For-UTA-Research\App_Updates\AllApps_Updates_from_2026-09-02.md`,
  entry dated 2026-09-23.
- A2A demo and keyword routing: `H:\My Drive\Temp_files-For-UTA-Research\a2a_demo\code-dev\a2a_bidir_test.py`
  (`FLUKA_TRIGGER_KEYWORDS`, `cancel()` raises `NotImplementedError`), `a2a-sdk==1.1.5`.
- OpenMC C API batch control: `openmc-reference/openmc/lib/core.py`; output files and restart:
  `openmc-reference/docs/source/usersguide/basics.rst`, `settings.rst`, `troubleshoot.rst`;
  fatal error strings: `openmc-reference/src/*.cpp`.
- NeutronOS extension pattern: `NEUTRON-OS/neutron-os-core/src/neutron_os/extensions/builtins/model_corral/axiom-extension.toml`
  and `mcp_tools.py`.
- EKF widget layer: `TRIGA-Control-Room-Agent/triga_modsim_tools-dev/triga_modsim_tools/dt/view/gui_widgets.py`
  (PyQt5 + pyqtgraph), `web_dashboard.py` (aiohttp, ZMQ subscriber). Read for the pattern only.
- Validated runs: `TRIGA-Model/SCOPING.md`, status table dated 2026-10-14.

## 15. Repository layout

Decided 2026-10-15, revised 2026-10-25 (Block 5 plan, sections 5a and 5b), brought up to date 2026-11-06. Our code is organized by
what it does; other people's code sits in one separate folder; facility data that may not be published sits outside the repository.
Run outputs are kept apart from code.

```
AutoMC/
  README.md  DESIGN.md  TOOLS.md  SETUP.md  CHANGELOG.md  HANDOFF.md  TACC.md   docs
  src/        automc.py (python -m automc), automc_core.py (the agent), automc_paths.py, automc_settings.py,
              automc_workspace.py, automc_version.py, console.py,
              agent/ (models, prompt, guard, keys, the error knowledge base's matcher, evaluations),
              tools/ (openmc/, reactors/triga/, files/, documents/, web/, python/, chats/, memory/, registry.py),
              prompts/, skills/ (skills, rule files, working examples, ERKB.yaml), evals/ (the cases), config/,
              facility_packs/ (public packs only), tests/, frontend/ (empty) ; later api.py
  Dockerfile  docker-compose.yml  docker-compose.netl.yml  docker/   the container and the check every image passes
  library_snapshots/  pinned copies of CoreForge and progression_problems, their public patches, make_snapshots.py
  workspace/  all run outputs (case folders, run-001, registry); never beside source
  devops/     plans, validation notes and their outcomes; never published as is
  external/   material received from others, kept as received (branko/)
  references/ the two NETL PDFs
  TRIGA-Model/  progression-problem run scripts, results and RUNNING.md: the validation record so far
  Learning/ openmc-notebooks/ openmc-reference/ scripts/ downloads/   unchanged
```

Outside the repository: the nuclear data, the working examples' data cache (`AUTOMC_DATA_CACHE`, else `~/.cache/automc/data`), the
restricted facility packs and the keys file.

See `src/README.md` for what each source folder is for and its current state.

The detector patch question (how to apply it without touching the clone the progression problems use) is settled: each build runs
in a fresh worker process whose import path starts with the snapshots of its library profile, and the patched snapshot is one of them
(Block 1). Since 2026-10-25 the patch and its snapshot are restricted data and live in the restricted NETL facility pack, outside the
repository (`../AutoMC_restricted/`, found through `AUTOMC_FACILITY_PACKS`); `src/facility_packs/README.md` documents the format.
