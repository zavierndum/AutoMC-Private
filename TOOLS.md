# AutoMC tools and modules

Index created 2026-10-16. One row per tool or engineered module. Each module has its own specification, named after the module, so that
files opened side by side in an editor are never both called `SPEC.md`.

## Modules in development

| Module | Agent tool | Status | Specification | Code now | Code after promotion |
|---|---|---|---|---|---|
| TRIGA Input Builder (OpenMC input for the NETL TRIGA, MCNP later) | `TRIGAInputSpecTool`, `TRIGAInputBuilderTool`, `TRIGASweepBuilderTool`, `TRIGAInputPreviewTool`, `TRIGAAssumptionsTool`, `TRIGASweepResultsTool` (built 2026-10-25) | Blocks 1 to 3 and 5 done: all ten configurations build and run; promoted, facility packs, agent tools (2026-10-25) | `devops/TRIGA_Input_Builder_Spec.md` | `src/tools/reactors/triga/` (moved 2026-10-25) | done |
| OpenMC run readers (any model) | `RunResultReaderTool` (built 2026-10-25) | `results` and `model_signature` moved from `devops/validation/` | none (simple tool) | `src/tools/openmc/` | done |
| OpenMC job service (any model) | `OpenMCRunStartTool` (plans only; the user approves), `OpenMCRunCheckTool`, `OpenMCRunPauseTool`, `OpenMCRunResumeTool`, `OpenMCRunStopTool`, `OpenMCRunCancelTool`, `OpenMCJobListTool` (built 2026-10-25) | built: openmc.lib worker, registry, sequences, `python -m automc run` | `devops/TRIGA_Input_Builder_Block5_Plan.md` (B5-2), `DESIGN.md` sections 5 and 6 | `src/tools/openmc/` | done |
| File tools (any file the user allows) | `FileListTool`, `FileReadTool` (text, CSV, Excel, PDF text, HDF5 structure; paged), `FileWriteTool` (workspace only) (built 2026-10-31) | built: read roots, refused secrets, a label per place for the data guard | `devops/AutoMC_General_Tools_Plan.md` (GT-2) | `src/tools/files/` (`places.py`: roots, refusals, labels, which the document and Python tools reuse) | done |
| Documents: knowledge bases from folders of PDFs and images, through Visual-Parser 2.2.4 (as RADIANT-LLM, AutoSAM and AutoFLUKA) | `DocumentParseTool` (a folder; plans only, the user approves), `DocumentCheckTool`, `DocumentSearchTool` (by meaning, file and page cited), `DocumentCancelTool` (rebuilt 2026-11-01) | built: only new files parsed, changed files compared page by page, the run as a background job, the vision model by the folder's label, no keys for a folder that stays local, local embeddings (`nomic-embed-text`) in SQLite with `sqlite-vec`, `python -m automc docs` | `devops/AutoMC_General_Tools_Plan.md` (GT-3R), `devops/validation/GT3R_RETRIEVAL.md` | `src/tools/documents/` | done |
| The web (search and pages) | `WebSearchTool` (Tavily, as AutoSAM), `WebFetchTool` (one page or PDF as text, paged; public internet only) (built 2026-11-01) | built: the conversation's label decides what leaves the machine (public: sent; unknown: only after the user types 'yes, send'; restricted: never), searches counted per conversation and month | `devops/AutoMC_General_Tools_Plan.md` (GT-4) | `src/tools/web/` | done |
| Python (a Jupyter kernel per conversation) | `PythonRunTool` (plans only; the user approves every run, code shown) (built 2026-11-01) | built: no keys in the kernel, a time limit, the whole output saved and a long one shortened to a share of the model's window (start and end), figures as PNG, output unlabelled unless the user typed 'yes, send'; withheld output shown to the user; runs without asking only inside a container whose settings say python.approval: auto | `devops/AutoMC_General_Tools_Plan.md` (GT-5), `devops/AutoMC_Workspace_Plan.md` (WD-0, WD-5) | `src/tools/python/` | done |
| Any OpenMC model (authoring) | `OpenMCModelBuildTool` (plans; the user approves the script), `OpenMCGeometryPlotTool`, `OpenMCModelCheckTool` (built 2026-11-01) | built: build.py runs in its own isolated process without keys, transport refused on the text and disarmed at run time, a failed build leaves nothing, the case gets model.xml, build.py, build.log and result.json with its label; slices with the source marked and overlaps drawn red; checks of nuclear data, settings, boundaries, the source and overlaps (OpenMC's plot mode) | `devops/AutoMC_Authoring_Plan.md` (AU-1) | `src/tools/openmc/authoring.py`, `_author_child.py`, `authoring_tools.py` | done (skills and examples: AU-2 to AU-5) |
| Earlier conversations | `ChatSearchTool` (built 2026-11-01) | built: the saved conversations of this workspace and its linked ones, by their words, each hit with its conversation's label | `devops/AutoMC_Workspace_Plan.md` (WD-3) | `src/tools/chats/` | done |
| The workspace's memory | `MemoryWriteTool` (proposes a note; the user approves), `MemoryReadTool` (built 2026-11-01) | built: notes labelled with the conversation's label (never the model's), a cloud model sees public ones only; `AUTOMC.md` in every prompt | `devops/AutoMC_Workspace_Plan.md` (WD-4) | `src/tools/memory/` | done |
| The AutoMC agent (LangGraph, CLI chat) | uses every tool above through `src/tools/registry.py` | built 2026-10-25/26: approval interrupt for every start, data guard for cloud models, skills, 20-case evaluation | `devops/TRIGA_Input_Builder_Block5_Plan.md` (B5-3), `DESIGN.md` sections 4 and 7 | `src/automc_core.py`, `src/agent/` | done |

Plans and validation notes for a module live in `devops/` (never published as is) and carry the module name:
`devops/TRIGA_Input_Builder_Plan.md`, `..._Block2_Plan.md`, `..._Block3_Plan.md`, `devops/validation/`.

## Candidates mentioned, no design yet

Other OpenMC model builders (general examples, domain-specific cases). Planned later in `devops/AutoMC_General_Tools_Plan.md`: runs on a
cluster (RC, after the agent has been demonstrated) and Axiom's RAG as a second knowledge-base backend on a NeutronOS node (GT-3R-2).
A candidate gets a row above only when it is built or someone writes its specification.

## Conventions

- **Tool names** end in `Tool` and are PascalCase, like `SAMInputFileCreatorTool` and `WebSearchTool` in the other agents. Each tool is a
  plain function (a dict of arguments in, a JSON-serializable dict out, errors as `{"ok": false, "error": {...}}`), listed with its
  description and argument schema in `src/tools/registry.py`. The agent core wraps that list; `python -m automc tool NAME '{...}'` calls
  a tool exactly as the agent does.
- **Facility data never sits in code.** It comes from facility packs (`src/facility_packs/README.md`); a tool result that depends on it
  carries `facility_data` with its label.
- **One specification per engineered module**, named `<Module_Name>_Spec.md`. Keep the same section headings in every spec: purpose and
  decisions, API, the spec or inputs, presets, provenance, result and errors, naming and layout, blocks and acceptance tests, open
  items. The TRIGA Input Builder spec is the template.
- **Simple tools stay flat**: a single file in `tools/` with no module folder and no spec (web search, PDF reading, and similar).
- **Share code only when a second module needs it.** The worker process, atomic output, safety checks and result types in the TRIGA
  Input Builder are candidates, but nothing moves into a common folder before a second module actually duplicates them.
- **Promotion** follows the checklist in `devops/README.md`: tested in `devops/`, then moved to `src/tools/`, then an entry in
  `src/tools/registry.py` (the agent core wraps that list; there is no per-tool `@tool` wrapper).
- **No personal names** in code: presets, identifiers, file names, comments. Design documents may name people.
- **No AI tool touches files on Lonestar6** without the required sign-off (see `TACC.md`).
