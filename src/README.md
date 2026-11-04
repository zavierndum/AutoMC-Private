# AutoMC source

The import root of AutoMC. Run everything from this folder: `python -m automc ...`. Design and decisions: `../DESIGN.md`. Index of the
tools: `../TOOLS.md`. Our code is organized by what it does; other people's code is in `../library_snapshots/`, run outputs in
`../workspace/`, never here. Facility data comes from facility packs ([`facility_packs/README.md`](facility_packs/README.md)): public
packs ship here, restricted ones live outside the repository (`AUTOMC_FACILITY_PACKS`, by default `../../AutoMC_restricted/`), and
every result says which data it was built from (`facility_data`, labelled public or restricted).

## Layout

| Path | Purpose | State |
|---|---|---|
| `automc.py` | The command line: `python -m automc chat`, `triga ...`, `run ...`, `read-run ...`, `tool ...`, `prompt`, `skills`, `eval`. | built |
| `automc_core.py` | The agent: a LangGraph loop over every tool (agent, tools, approve), a checkpointer, the user's approval for every start of transport (an interrupt), the data guard for cloud models. | built (B5-3) |
| `agent/` | `llm.py` (providers from `config/llm.yaml`: local Qwen, OpenAI directly or through Portkey, Gemini, Claude), `models.py` (`models list`, `discover` and `check`), `discovery.py` (the models offered: configured ones, plus local Ollama models with tools, vision and thinking, OpenAI from gpt-4 on and Gemini from gemini-3 on; the `/model` list), `context.py` (the context guard), `schemas.py` (the tool definitions as each service takes them, and text arguments parsed back), `errors.py` (a failed model call in one sentence), `keys.py` (keys from the file `AUTOMC_ENV_FILE` names, outside AutoMC: only the variables AutoMC uses, never put into the environment), `tracing.py` (LangSmith, only for what a cloud model may see), `skill_tool.py` (facility-pack skills on demand), `guard.py` (what a cloud model may see), `prompt.py` (system prompt and skills), `views.py` (what the model reads of a long tool result), `chat.py` (the command-line chat), `evals.py` (the evaluation runner). | built (B5-3) |
| `config/` | `llm.yaml` (the providers, their endpoints, which ones discover models, the switching policy), `ollama/` (the recipes of the 32k and 128k local models). | built |
| `prompts/`, `skills/`, `evals/` | The system prompt (YAML sections, no templating), the bundled skills in the AutoSAM format with `MANIFEST.json` and `ERKB.yaml`, the evaluation cases. | built (B5-3) |
| `automc_paths.py` | Where things are: the AutoMC root, the workspace (`AUTOMC_WORKSPACE` or `--workspace` overrides it) and its one hidden folder `.automc/`, the library snapshots, the pack folders. | built; `.automc/` 2026-11-01 |
| `automc_settings.py` | A workspace's settings (`.automc/settings.yaml`): the folders AutoMC may read with their labels (public, restricted, or unknown), the linked workspaces (read only), the policies; the variables `AUTOMC_PUBLIC_ROOTS`, `AUTOMC_RESTRICTED_ROOTS`, `AUTOMC_ROOTS`; open and facility modes. Plan: `../devops/AutoMC_Workspace_Plan.md`, WD-2. | built 2026-11-01 |
| `automc_workspace.py` | The workspace's layout: `workspace upgrade` (an older workspace's hidden items into `.automc/`, nothing deleted), `workspace usage`, `config show`, the check that refuses an older layout. WD-2. | built 2026-11-01 |
| `automc_version.py` | AutoMC's one version (0.8.0) and the fingerprint of the prompt, skills and evaluation cases (`python -m automc --version`); the history is `../CHANGELOG.md`. WD-1. | built 2026-11-01 |
| `tools/registry.py` | Every agent tool in one list (name, description with its limits, JSON schema, handler); the agent, the CLI and an MCP server read it. | built |
| `tools/reactors/triga/` | The TRIGA Input Builder, `build_input(code, facility, ...)`: spec layer (`spec`, `presets`, `safety`, `capabilities`, `inserts`, `facilities/netl`), `libraries`, `data`, `water`, `build`, `_worker`, `writers/openmc_writer`, `result`, `advice`, `tallies`, `energy_bins`, `sweep`, `sweep_results`, `preview`, `packs` (facility packs), `tools` (the six agent tools), CLI. Specification: `../devops/TRIGA_Input_Builder_Spec.md`. | Blocks 1 to 3 done; moved here and given facility packs and tools 2026-10-25 (0.7.0) |
| `tools/openmc/` | General OpenMC, for any model: the job service (`runs`: plan, start with the user's approval, check, pause, resume, checkpoint, stop, cancel; `jobs`: the registry `../workspace/.automc/jobs/registry.json`; `worker`: one openmc.lib process per run, controlled between batches; `sequence`: several cases in turn as one job; `run_cli`), `results` (read a finished run, compare two runs), `model_signature`, `tools` (the run tools and `RunResultReaderTool`), and the authoring of any model: `authoring` (build a case from the agent's `build.py` after the user's approval, plot it, check it), `_author_child` (the isolated process that does it, transport disarmed), `authoring_tools` (`OpenMCModelBuildTool`, `OpenMCGeometryPlotTool`, `OpenMCModelCheckTool`). | built (job service 2026-10-25) |
| `tools/files/` | The file tools: `places` (where they read and write, the keys and credentials they always refuse, the label of each place for the data guard) and `tools` (`FileListTool`, `FileReadTool` for text, CSV, Excel, PDF text and HDF5 structure, paged; `FileWriteTool`, workspace only). Plan: `../devops/AutoMC_General_Tools_Plan.md`, GT-2. | built 2026-10-31 |
| `tools/web/` | The web: `gate` (what may leave the machine, by the conversation's label: public sent, unknown only after 'yes, send', restricted never), `search` (Tavily, as AutoSAM; counted per conversation and month), `fetch` (one page or PDF as text, paged; public internet only, each redirect checked; a PDF kept for a knowledge base), `tools` (`WebSearchTool`, `WebFetchTool`). Plan: `../devops/AutoMC_General_Tools_Plan.md`, GT-4. | built 2026-11-01 |
| `tools/python/` | Python for the agent: `run` (one Jupyter kernel per conversation, without keys, in `workspace/.automc/python/<conversation>/`; the plan the user approves with the code shown; time limit; the whole output saved, a long one shortened to a share of the model's window; figures as PNG), `tools` (`PythonRunTool`). Plan: GT-5. | built 2026-11-01 |
| `tools/conversation.py`, `console.py` | What a tool may know of the conversation it runs in (the chat model, its label so far, its id, the model's window), set by the core around every call; how the command lines print (banners, fields, tables). | built 2026-11-01 |
| `tools/documents/` | Knowledge bases from folders of PDFs and images, through Visual-Parser (its command, never imported): `parse` (the plan: new, changed, re-saved, copied and twice-named files; the run as a background job with its own registry; one folder per knowledge base under `workspace/.automc/documents/`; the vision model and the keys the process gets by the folder's label), `child` (the run's process: Visual-Parser over a folder of links, then the index), `index` (the SQLite store with `sqlite-vec` and full text, Ollama's `nomic-embed-text`, search by meaning with each row once), `search` (over every knowledge base), `tools` (`DocumentParseTool`, which only plans, `DocumentCheckTool`, `DocumentSearchTool`, `DocumentCancelTool`), `cli` (`python -m automc docs`). Plan: `../devops/AutoMC_General_Tools_Plan.md`, GT-3R. | rebuilt 2026-11-01 |
| `tools/chats/` | Saved conversations: `store` (one JSONL file per conversation in `.automc/chats/`, also its audit; restricted content not saved unless the settings say so; list, copy from a linked workspace, delete, search; `egress.jsonl`, what left the machine), `tools` (`ChatSearchTool`). Plan: `../devops/AutoMC_Workspace_Plan.md`, WD-3. | built 2026-11-01 |
| `tools/memory/` | The workspace's memory: `store` (`AUTOMC.md`, labelled notes in `.automc/memory/` with an index, the prompt block each model may see, the size cap), `tools` (`MemoryWriteTool`, which only proposes a note for the user to approve, `MemoryReadTool`). WD-4. | built 2026-11-01 |
| `facility_packs/` | Public facility packs only: `netl_public` (library defaults and the SAR, fresh fuel). The format and the rules are in its README. | built |
| `tests/` | Mirrors `tools/`: `tests/test_tools.py` (every agent tool), `tests/reactors/triga/` (the builder, the packs). Standard `unittest`, about 7 to 20 minutes on WSL including real builds of all ten configurations. Tests that need OpenMC skip on Windows Python; tests of the reference preset skip without the restricted pack, and read their expected values from it. | passing |
| `frontend/` | The web UI. | empty (Block 6) |

## Commands (from this folder)

Any command takes `--workspace DIR` (before or after it) to work in another workspace. The workspace:

```
python -m automc --version                                              # AutoMC's version and the fingerprint of its prompt, skills and cases
python -m automc config show                                            # every setting in effect (folders, labels, links, mode) and where from
python -m automc workspace upgrade --dry-run                            # an older workspace: what would move into .automc/ (then without --dry-run)
python -m automc workspace usage                                        # what takes space
```

Anywhere (Windows Python works for these):

```
python -m unittest discover -s tests -t .           # all tests (-v for names)
python -m automc triga presets                       # list presets
python -m automc triga spec --config 8 --rod S1=600:console --views   # resolve a spec, show every rod unit and the hash
python -m automc triga assumptions --config 1        # assumptions that apply to a spec
python -m automc triga assumptions --all             # the full register
python -m automc triga spec --preset netl_public_fresh_fuel --views   # the public model: library defaults and the SAR, fresh fuel
python -m automc tool --list                         # the agent's tools and what each may do
python -m automc tool TRIGAInputSpecTool '{"config": 8, "rods": {"S1": {"value": 600, "unit": "console"}}}'   # call one as the agent does
AUTOMC_FACILITY_PACKS=/some/empty/folder python -m automc triga presets   # what a user without the restricted pack sees
```

Build, preview and read results (WSL, `automc` environment, `OPENMC_CROSS_SECTIONS` set):

```
python -m automc triga build --config 0                                 # the reference preset, configuration 0, smoke run size
python -m automc triga build --config 0 --rod S1=600:console --rod TR=0.3 --name my-case
python -m automc triga build --rods 0.5 --set conditions.water_temperature_K=320 --set 'run.size="standard"'
python -m automc triga build --set conditions.water_temperature_K=320 --set conditions.water_density_gcc=auto   # density follows temperature
python -m automc triga build --config 0 --force                         # rebuild and replace an existing folder
python -m automc triga build --config 0 --json                          # the full result as JSON (errors too), for scripts and agents
python -m automc triga build --config 8 --name c8                       # any of the ten configurations (PNT and 3-EL inserts)
python -m automc triga build --3L thermal --set inserts.three_el.liner=cadmium   # override one hardware field of an insert
python -m automc triga spec --config 5 --views                          # see the insert hardware a configuration resolves to
python -m automc triga preview my-case                                  # four geometry pictures in workspace/my-case/preview/
python -m automc triga build -h                                         # all options, with examples
python -m automc triga sweep --name rod-tr --sweep-rod TR --positions 0,0.2,0.35,0.5,0.65,0.8,1   # one model per transient-rod position
python -m automc triga sweep --name fuel-temp --sweep-field conditions.fuel_temperature_K --values '[294,600,900]'
python -m automc triga sweep -h                                         # all sweep options, with examples
python -m automc triga read-sweep ../workspace/rod-tr --beta-eff 0.0070 # k-eff and worths of a sweep after it has run
python -m automc read-run ../workspace/my-case/run-001                  # k-eff, timing, tallies, lost particles of one run
python -m automc read-run --compare RUN_A RUN_B                         # do two runs with the same seed agree? k by batch and every tally
python -m automc run start my-case --threads 16                         # shows the plan and asks y/n; returns at once, the run goes on
python -m automc run start --sweep rod-tr                               # every state of a sweep in turn, finished ones skipped
python -m automc run watch                                              # follow the newest job; Ctrl-C stops watching, not the run
python -m automc run pause JOB | resume JOB | checkpoint JOB | stop JOB | cancel JOB
python -m automc run jobs                                               # the registry: every job, its status and latest k-eff
python -m automc docs ingest /mnt/c/path/reports                       # a folder's new PDFs and images into a knowledge base; asks y/n, runs in the background
python -m automc docs search what is the zirconium hydride density      # the closest rows of the knowledge bases, with file and page
python -m automc data fetch [EXAMPLE ...]                               # the working examples' CAD, mesh and ENDF files into the data cache, SHA-256 checked
python -m automc tool WebSearchTool '{"query": "half-life of promethium-147"}'      # one web search (TAVILY_API_KEY in the keys file)
python -m automc tool WebFetchTool '{"url": "https://www.osti.gov/servlets/purl/1718987"}'   # a page or PDF as text
python -m automc models list                                            # the language models: local or cloud, whose account, ready or not; where each key comes from
python -m automc models discover [--refresh]                            # the models you can choose, numbered (as /model shows them)
python -m automc models check NAME [--tools]                            # one short message to a model, NAME as discover shows it (and one tool call)
python -m automc chat [--provider NAME]                                 # talk to the agent (local 32k model by default; /help for commands)
                                                                        # NAME as `models discover` shows it: cloud-portkey/gpt-5.4, local
                                                                        # in the chat: /model (list), /model NAME (switch), /new --model NAME,
                                                                        # /pause, /resume, /stop, /cancel JOB_ID (a run's controls, directly)
python -m automc prompt                                                 # what the model is told: size, skills, facility packs
python -m automc eval run                                               # score the agent on evals/cases.yml with the local model
python -m automc triga build --preset netl_boc505_tallies --name full-tallies   # detector and mesh tallies on (large: read the warnings)
python -m automc triga build --tallies beam_port_2_sphere_flux,flux_mesh       # pick tallies by name; --tallies none switches them off
python -m automc triga build --tallies flux_mesh --set tallies_options.mesh='{"dimension":[50,50,3],"lower_left_cm":[-60,-60,-20],"upper_right_cm":[60,60,20]}'
```

`build` writes `../workspace/<name>/` with `model.xml`, `result.json` (the full spec, library profile, cross-section checksum,
assumptions, warnings, summary, run-time estimate) and `build.log` (header, worker output, exit code). It never runs transport: it prints
the exact commands for you to run it by hand in a `run-001/` folder (or start it as a managed job: `python -m automc run start NAME`),
and the same estimate and warnings that `spec --views` shows beforehand.

A managed run (`run start`) shows its plan (histories, threads, an estimate, the run folder, which facility data the model uses) and
asks before it starts; no agent tool can start one. It runs in its own process, so the command returns at once and the run outlives the
terminal. Between batches it can be paused (it writes a statepoint and waits), resumed, checkpointed or stopped; a stopped or interrupted
run continues later from its last statepoint, again only after you approve. Its folder holds `run.log` (ending with
`AUTOMC_JOB_DONE rc=<n> status=<s> segment=<k>`), `events.jsonl` (k-eff after every batch), `status.json` and `control.json`. One run
at a time unless you ask for `--concurrent`. The batches are the same as a run by hand with the `openmc` executable and the same seed.

- An identical second request is reused instantly (`reused: true`). A different existing folder is refused. `--force` rebuilds and
  replaces it, and a failed forced rebuild leaves the old folder untouched. `--force` refuses a folder that holds transport runs (`run-*`),
  so results are never deleted by a rebuild.
- The writer turns off OpenMC's `tallies.out` text file (it was 2 GB with the detector tallies); every tally result is in the statepoint.
- Each build runs in its own worker process (`python -m tools.reactors.triga._worker`) with a 600 s timeout (`--timeout`); a hung build is
  killed with all its children. The worker's import path is this folder, then the library snapshots of the build's profile.
- Output stays inside the workspace unless `--allow-outside` is given.
- From Python, in this folder: `python -c "from tools.reactors import triga; r = triga.build_input('openmc', 'netl', config=0); print(r.outdir)"`.
- `sweep` builds one model per state (a rod at several positions in any unit, any spec field at several values, or a list of states) into
  `../workspace/<name>/state-NNN/`, all with the same random seed, and writes `run_sweep.sh` and `sweep.json`. Everything is checked
  before the first build. It runs nothing: you run `bash run_sweep.sh`, then `read-sweep` prints k-eff, the worth between neighbouring
  states in pcm (dollars with `--beta-eff`) and, for rod sweeps, a monotonic check.
- Tally presets are plain data: the general ones (`flux_mesh`) in `tools/reactors/triga/tallies.py`, the ones on a facility's own
  detector cells in its pack (`tally_presets.yaml` of the restricted NETL pack); a new detector is a new entry. They are unverified
  against any reference output (assumption A11). The size guard refuses more than 20,000,000 bins in total.
- Configurations 0 to 9 build from the labels (`--config`, `--pnt`, `--3L`). Each label resolves to insert hardware stored in the spec
  under `inserts` (PNT terminus and wrapper; 3-EL design, liner, liner thickness, modified geometry; what the 3-EL slots hold when empty).
  Which hardware a label means is a table in the preset; the assumptions register says which are confirmed (A7, A12 to A16).
- `preview` draws `core_xy`, `world_xy`, `core_xz` and `world_xz` with a legend (OpenMC's plotter, no transport, a few seconds).

Rod values accept units: `--rod TR=0.3`, `--rod S1=600:console`, `--rod S2=19.05:cm_withdrawn`, `--rods 0.5` for all four.
Exit codes: 0 ok, 1 other error, 2 invalid spec (the message names the field), 3 valid but not buildable yet, 4 missing library or
environment, 5 timeout. With `--json`, errors come out as `{"ok": false, "error": {type, field, message, exit_code}}` on stdout.
