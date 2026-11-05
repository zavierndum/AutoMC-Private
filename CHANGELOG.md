# AutoMC changelog

One version for the whole package (`src/automc_version.py`): the code, the system prompt, the skills and the evaluation cases. Between
releases nothing is bumped by hand; a chat and an evaluation record the fingerprint of the prompt and skills (and cases) they saw
(`python -m automc --version`). Details of each step are in `devops/` (plans with their outcomes).

## 0.8.0 (2026-11-01, not released)

The first package version (WD-1, `devops/AutoMC_Workspace_Plan.md`). It continues the TRIGA builder's own 0.7.0; the prompt's and the
evaluation cases' own version lines are gone.

- **Skills (AU-3 and AU-4, 2026-11-04 and 11-05, `devops/AutoMC_Authoring_Plan.md`):** rule files read on demand with
  `SkillLookupTool`: 18 for authoring (one per part of a model) with build-script templates, 4 for validation (the review gate
  before and after a run), troubleshooting (crashes, lost particles and their replay, geometry-debug runs, CAD files, depletion
  hangs) and 5 for post-processing (statepoints, tally arrays and mesh order, spectra, derived numbers, depletion and multigroup
  results). Every skill file is compared with the OpenMC manual, examples and notebooks, and fails at 12 shared words or 5 shared
  code lines; facts the rules state about OpenMC 0.15.3 are tested against the installed package. `ERKB.yaml` left the prompt:
  `agent/erkb.py` matches it against every failed tool result and adds `known_errors`; 15 new entries (23 in all), each
  provoked from OpenMC in a test or saying why it cannot be. Corrected: a lost particle's file holds where its history started, not where it was lost (a replay finds
  that point), and OpenMC's overlap message names cell ids. The model check now reads each CAD file the model names and finds
  touching volumes that hold two copies of a face instead of sharing one: the cad-geometry tokamak's overlap error names its 25
  pairs and says the fix is to imprint and merge in the CAD export, not in `build.py`. The tokamak was found mirror-symmetric about
  x = 0, so the 2% between its mirrored half and its quarter comes with the reflecting plane at x = 0, not with its shape.

- **The system prompt (PR-0 to PR-3, 2026-11-02, `devops/AutoMC_Prompt_Plan.md`):** a closed schema (keyed sections with channels
  `local` and `cloud`, and examples with the phrases their answers must and must not hold), loaded with a loader that refuses unknown
  keys, wrong types and a key given twice. Sixteen sections written from the code (authority and approvals, data labels, OpenMC
  essentials, writing a model, runs, results and their errors, troubleshooting, the TRIGA, one routing line per tool, skills, how to
  answer, what is declined, a cloud-only section, a final checklist) and seven examples from real tool output; about 25 KB.
  `SkillLookupTool` reads the bundled files by path (`file`), the working examples above all. The model check's normalisation note
  says when OpenMC has already scaled fixed-source tallies by the source strengths. Seven evaluation cases for the examples' lessons
  (39 in all). `eval run` takes a cloud model too (`--provider PROVIDER/MODEL`), with the restricted packs, folders and user settings
  hidden from its cases, the restricted-pack cases skipped and 400,000 tokens per case. Choosing a model whose provider's list
  cannot be read now says so (with the network error) instead of saying the provider does not offer it.
- **Authoring (AU-0 to AU-2, 2026-11-01 and 11-02, `devops/AutoMC_Authoring_Plan.md`):** `OpenMCModelBuildTool` (a build script the
  user approves), `OpenMCGeometryPlotTool`, `OpenMCModelCheckTool`; script runs (decision A13: Python that runs transport itself,
  approved like any run, stop and cancel only); working examples in `src/skills/working_examples`, each tested. Batch 4 (2026-11-02):
  six multigroup examples, among them the C5G7 benchmark's 2-D core from the NEA tables, checked against its reference k and
  assembly powers. A file a build script names (an MG library) now stays relative to the case: the build runs with OpenMC's
  `resolve_paths` off, a path into the build folder left in `model.xml` is rewritten, and the check names a library that does not
  exist (before, the path pointed into the removed build folder and every run of the case failed). Batch 5 (2026-11-02 and 11-03):
  six examples (nuclear data, resonance covariances, the C API, CAD geometry, unstructured-mesh tallies, a CAD model with a mesh tally);
  analysis examples (`analysis.py`, run with PythonRunTool) for nuclear-data work that builds no model.
- **Example data (A3):** `python -m automc data list | fetch | verify` downloads the files some examples need (CAD, meshes, ENDF,
  multipole) into the data cache (AUTOMC_DATA_CACHE, else ~/.cache/automc/data), listed with their sources and SHA-256 in
  `skills/working_examples/data.json`; a member of a zip archive is read by range requests (two ENDF files: 0.8 MB of NNDC's 310 MB).
  Run folders now carry every file `model.xml` names by a relative path (a multigroup library, a CAD geometry, a mesh; linked, or
  copied), and the model check names one that is missing, outside the case or absolute. The check no longer reports an overlap for a
  slice lying exactly on a surface (it tries the slice a little to either side; a CAD model's flat face at z = 0 had read as an
  overlap).
- **Correction (2026-11-03):** the CAD tokamak of `cad-geometry` was described as having overlapping volumes. Its volumes do not
  overlap. They touch through duplicate faces and are not merged, so DAGMC's implicit complement claims points inside them; the
  file passes `check_watertight`. The example's README now says what the file holds, what to trust from it and how to check a CAD
  model (section "Using the tokamak"), and `ERKB.yaml`'s overlap entry covers the CAD case. A repair is planned
  (`devops/DAGMC_Merge_Fix_Plan.md`).
- **Repository (2026-11-03):** a `.gitignore` that is an allow-list like `.dockerignore` (the package, the library snapshots, the
  container files and the public docs), `.gitattributes` (LF; shell scripts and the Dockerfile LF in every checkout), a top-level
  `README.md`. Facility values out of the public files: two evaluation cases (`reference-bias`, `config-8`) had the restricted pack's
  values in their expected answers, and three tests had them as literals. Cases now say `answer_from_pack` and take those phrases
  from the pack (`provides.eval_answers`); the loader refuses a number in the phrases of a case that needs the restricted pack. The
  tests read the values from the pack (`provides.canaries`), and a new test searches every public file for them. Two lines of
  `DESIGN.md` reworded (a facility value, a personal detail).

- **Docker (WD-5):** `Dockerfile` (OpenMC from conda-forge, the pinned packages, Visual-Parser unless `VISUAL_PARSER=no`, an ordinary
  user), `docker-compose.yml` (`/host` projects, read-only keys file, settings and nuclear data, the host's Ollama, a CPU limit),
  `docker-compose.netl.yml` (the restricted packs, mounted at NETL only), `docker/check_image.py` (refuses an image holding
  restricted data, devops/, a workspace, keys or personal identifiers). Python may run without asking inside a container whose
  settings say so; `OLLAMA_HOST` points every local provider at another Ollama.
- **Memory (WD-4):** `AUTOMC.md` and labelled notes in `.automc/memory/`; `MemoryWriteTool` proposes, the user approves; `/remember`,
  `/memory`; a cloud model in facility mode sees only what is public.
- **Saved conversations (WD-3):** one JSONL file per conversation (`.automc/chats/<uuid>.jsonl`, also the audit); `/sessions`, `/show`,
  `/load`, `/delete`, `chat --load`; restricted content not saved unless the settings say so (W3); `egress.jsonl` records what left
  the machine; `ChatSearchTool`.
- **The workspace (WD-2):** one hidden folder `.automc/`; `settings.yaml` with folders, labels, links and policies;
  `AUTOMC_PUBLIC_ROOTS`, `AUTOMC_RESTRICTED_ROOTS`, `AUTOMC_ROOTS` (`AUTOMC_FILE_ROOTS` still read, with a warning); open and facility
  modes (an undeclared knowledge base is no longer withheld from a cloud model in open mode); places recorded as references;
  read-only linked workspaces; cases up to four folders deep; `--workspace`; `workspace upgrade`, `workspace usage`, `config show`.
- **Models on screen (WD-0):** a model is shown and typed as `KIND-SERVICE/MODEL` (`cloud-portkey/gpt-5.4`); a Portkey workspace's
  `@slug/` is never shown and need not be typed. Python output withheld from a cloud model is shown to the user; the model is told so.
  The skill tells the model to call `PythonRunTool` itself and never to use `input()`; the plan warns about typed input. The chat has
  `/pause`, `/resume`, `/checkpoint`, `/stop` and `/cancel` for runs. Two evaluation cases (30 in all).
- **General tools (GT-1 to GT-6, 2026-10-31 and 11-01):** keys from a file outside AutoMC, LangSmith for public conversations only;
  file tools; knowledge bases from folders of PDFs and images (Visual-Parser, local `nomic-embed-text` embeddings, SQLite with
  `sqlite-vec`); the web (Tavily search, page and PDF fetch); Python in a Jupyter kernel per conversation, each run approved. System
  prompt 1.1.0 and evaluation cases 1.1.0 (28 cases) under the old numbering.
- **The agent (Block 5, models plan MS-1 to MS-5, 2026-10-26 to 10-31):** the LangGraph agent and the CLI chat, approval for every
  run, the data guard for cloud models, OpenAI (directly or through Portkey), Gemini, Claude and local models, switching during a
  conversation, model discovery. System prompt 1.0.0; evaluation cases 1.0.0 to 1.0.3.
- **The TRIGA Input Builder (Blocks 1 to 3, to 0.7.0):** the spec, presets and assumptions; OpenMC models of the ten experimental
  configurations; sweeps; previews; the job service for runs (pause, resume, checkpoint, stop, cancel, restart).
