# AutoMC Environment Setup — Reproducibility Guide

How the OpenMC-based `automc` development environment was built on this machine, start to
finish. Follow this in order on a fresh PC to reproduce it exactly. Written 2026-10-04.

## Why WSL2 at all

OpenMC has **no native Windows build**: conda-forge's own package metadata for `openmc` lists
only `linux-64` and `osx-64` platforms, and there is no PyPI wheel for it either. WSL2 Ubuntu is
the supported path on Windows.

## 0. Prerequisites

- WSL2 already installed, running **Ubuntu 26.04 LTS** (`wsl -l -v` to check; `wsl --install -d Ubuntu` if not present).
- VS Code with the Jupyter extension. **You do not need the WSL remote extension** or to reopen
  VS Code inside WSL — see the "Connecting VS Code" section below for the approach actually used
  (keeps VS Code on Windows, connects out to a Jupyter server running inside WSL).

## 1. Install Miniforge inside WSL2 Ubuntu

```bash
wsl -d Ubuntu
wget -O ~/miniforge.sh "https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-Linux-x86_64.sh"
bash ~/miniforge.sh -b -p ~/miniforge3
source ~/miniforge3/etc/profile.d/conda.sh
```

A copy of the installer script used is kept at `scripts/miniforge.sh` (Miniforge3, built with
`constructor 3.16.1`).

**Important**: a fresh shell does not automatically pick up `conda` — don't assume `.bashrc`
sourcing happened. Always source `conda.sh` explicitly, especially in any non-interactive
invocation (e.g. `wsl -d Ubuntu -- bash -c "..."` from Windows), since non-interactive shells
typically skip `.bashrc` entirely.

## 2. Create the `automc` conda environment and install OpenMC

```bash
conda create -n automc python=3.11 -y
conda activate automc
conda install -c conda-forge openmc -y
```

Confirmed working version: **OpenMC 0.15.3**, Python 3.11.16.

## 3. Download and install ENDF/B-VIII.0 nuclear data

Get the full HDF5 library from **openmc.org/data/** (Box-hosted, ~3.2 GB,
`endfb80_hdf5.tar.xz`) — **not** the Zenodo "low-temp" alternative, which only ships 3
temperature points (20K/294K/600K). TRIGA-relevant work needs higher temperatures, and the full
openmc.org release has 6 points (250K–2500K).

```bash
tar -xJf endfb80_hdf5.tar.xz -C ~/
# produces ~/endfb-viii.0-hdf5/cross_sections.xml plus neutron/photon/thermal data folders
```

Point OpenMC at it via the `OPENMC_CROSS_SECTIONS` environment variable:

```bash
export OPENMC_CROSS_SECTIONS=~/endfb-viii.0-hdf5/cross_sections.xml
```

**Gotcha**: putting this only in `~/.bashrc` is not enough — Ubuntu's default `.bashrc` typically
short-circuits for non-interactive shells, so any `wsl -d Ubuntu -- bash -c "..."` command run
from Windows won't see it unless you export it inline in that same command, every time.

## 4. Register the Jupyter kernel

```bash
conda activate automc
python -m ipykernel install --user --name automc --display-name "automc"
```

Confirmed registered at `~/.local/share/jupyter/kernels/automc` (`jupyter kernelspec list` to
verify).

## 5. Connecting VS Code to the WSL kernel (without reopening VS Code in WSL)

VS Code stays on Windows. A Jupyter server is started manually inside WSL and VS Code connects to
it as a **remote/existing server**, same mechanism as connecting to a server on another machine.

**Automated, secure launcher** (what's actually used day to day) — `scripts/start_automc_jupyter.sh`:
- Binds to `127.0.0.1` only (loopback — not reachable from the local network; WSL2's built-in
  localhost forwarding still lets Windows/VS Code reach it as `http://localhost:8891`).
- Uses a cryptographically random 256-bit token, generated once and stored in
  `~/.automc_jupyter_token` (`chmod 600`, not hardcoded in any script).
- Idempotent — safe to run even if the server's already up, it just no-ops and reports the URL.

Deployed copy lives at `~/start_automc_jupyter.sh` in WSL (the editable source is
`scripts/start_automc_jupyter.sh` here — after editing it, redeploy with
`wsl -d Ubuntu -- bash -c "cp /mnt/c/.../scripts/start_automc_jupyter.sh ~/ && chmod +x ~/start_automc_jupyter.sh"`).

**To start it:**
- Double-click `scripts/start_jupyter.bat`, **or**
- `wsl -d Ubuntu -- bash -c "~/start_automc_jupyter.sh"`

It prints a URL like `http://localhost:8891/?token=<random>`.

**In VS Code**, open any notebook → **Select Kernel** (top-right of the notebook) →
**"Existing Jupyter Server..."** → paste that URL → pick the **automc** kernel from the list that
appears. VS Code remembers this server afterward (as long as the token file isn't deleted, the
URL never changes) — you won't need to re-paste it on future sessions, just pick it from history.

## 6. UT-Computational-NE repos cloned (for CoreForge/MPACTPy-driven model generation)

All live under `C:\UTA-Research\Projects\UT-Research-Repos\`. Each was deliberately cloned fresh
(not a stale ZIP-download snapshot) and has **push disabled** as a safety default, since these are
UT-Computational-NE org repos, not Zavier's own:

```bash
git clone <url> <TargetFolder>
cd <TargetFolder>
git remote set-url --push origin DISABLED-no-accidental-push
```

(`fetch`/`pull` still work normally against the real remote; `git push` fails immediately.)

| Repo | Remote |
|---|---|
| `CoreForge` | `https://github.com/UT-Computational-NE/CoreForge.git` |
| `MPACTPy` | `https://github.com/UT-Computational-NE/MPACTPy.git` |
| `FlowForge` | `https://github.com/UT-Computational-NE/FlowForge.git` |
| `PROGRESSION-PROPS` | `https://github.com/UT-Computational-NE/progression_problems.git` |
| `AXIOM-OS/axiom-os` | `https://github.com/b-tree-labs/axiom-os.git` |
| `AXIOM-OS/axiom-os-private` | `https://github.com/b-tree-labs/axiom-os-private.git` |
| `TRIGA_MOD_SIM/triga_modsim_tools` | `https://ndum9496@rsicc-gitlab.tacc.utexas.edu/ut-computational-ne/triga_digital_twin/triga_modsim_tools.git` |
| `TRIGA_WEBSITE/triga_dt_website` | `https://ndum9496@rsicc-gitlab.tacc.utexas.edu/ut-computational-ne/triga_digital_twin/triga_dt_website.git` |
| `LOCAL-LLMS/RASCAL/rascal-configs` | `https://ndum9496@rsicc-gitlab.tacc.utexas.edu/ut-computational-ne/rascal-configs.git` |

**Not yet real git clones** (still ZIP-download-style snapshots, no freshness check done against
upstream yet): `netl_triga_dt-master`, `tunek3-main`. Treat anything read from these with caution
until they get the same fresh-clone treatment as CoreForge/MPACTPy/FlowForge did.

### Editable pip installs (inside the `automc` conda env)

```bash
conda activate automc
pip install -e /mnt/c/UTA-Research/Projects/UT-Research-Repos/CoreForge
pip install -e /mnt/c/UTA-Research/Projects/UT-Research-Repos/MPACTPy
```

Confirmed importable as `coreforge` 0.0.1 and `mpactpy` 0.0.1. **`FlowForge` is cloned but not
yet `pip install -e`'d into `automc`** — do that the same way if/when it's actually needed:
```bash
pip install -e /mnt/c/UTA-Research/Projects/UT-Research-Repos/FlowForge
```

## 7. OpenMC reference documentation + example notebooks

Scoped inside `AutoMC` itself (not `UT-Research-Repos`, since OpenMC is a general third-party
open-source project, not UT research output):

```bash
cd AutoMC
git clone --branch v0.15.3 --depth 1 https://github.com/openmc-dev/openmc.git openmc-reference
git clone https://github.com/openmc-dev/openmc-notebooks.git
```

Both push-disabled the same way as above. `openmc-reference` is pinned to the exact commit
(`27e38e8`) matching the installed OpenMC 0.15.3. It contains the full docs source
(`docs/source/usersguide`, `methods`, `devguide`, API reference) and 10 script-based examples.
`openmc-notebooks` has the 28 real example Jupyter notebooks (pincell, depletion, CAD geometry,
CANDU, TRISO, multigroup cross sections, etc.) — these are the actual official example gallery;
the `examples/` folder inside the main `openmc` repo only has script-based examples, not notebooks.

**Both clones are kept pristine/read-only on purpose.** The notebooks actually run are *copies*,
at `Learning/openmc-examples/` (along with their supporting data: `c5g7.h5`, `chain_simple.xml`,
`images/`), so that running them (which modifies cell outputs, writes `statepoint.h5`/etc.) never
dirties the upstream clone or complicates a future `git pull`.

## 7b. The agent: LangGraph, LangChain and the local model (Block 5, B5-3, installed 2026-10-25)

Pinned in the `automc` environment (decision D8). The dry run showed every package new and nothing already installed upgraded, so
OpenMC, numpy and h5py were not touched; `pip check` reported no broken requirements.

```bash
conda activate automc
python -m pip install "langgraph==1.2.17" "langchain-core==1.7.0" "langchain-ollama==1.1.0" "langchain-openai==1.7.2"
```

They bring, among others: langgraph-checkpoint 4.3.0, langgraph-prebuilt 1.1.1, ollama 0.6.3, openai 3.37.0, pydantic 2.14.1.
`prompt_toolkit` (the chat's input line) and PyYAML were already there.

The local model runs in the WSL Ollama (0.35.0, its own server on 127.0.0.1:11434, separate from the Windows one). AutoMC's default
since 2026-10-26 is `qwen3.6:35b-32k-t0` (32k context, temperature 0); `qwen3.6:35b-128k-t0` is the `local-128k` choice. Both are
built once from AutoMC's own recipes:

```bash
cd /mnt/c/UTA-Research/Projects/ZavierNdum/Code/AutoMC/src
ollama create qwen3.6:35b-32k-t0 -f config/ollama/qwen3.6-35b-32k-t0.Modelfile
ollama create qwen3.6:35b-128k-t0 -f config/ollama/qwen3.6-35b-128k-t0.Modelfile
```

Speed on this laptop (measured 2026-10-25, RTX PRO 3000 12 GB, 71% of the model on the CPU): about 1 token per second while writing.
One tool-calling step took 75 s with thinking off (including loading the model) and 918 s with thinking on, so `config/llm.yaml`
switches thinking off. The model choice and its endpoint are in `src/config/llm.yaml`.

## 7c. Cloud models for public data (2026-10-26)

`src/config/llm.yaml` lists every provider; `python -m automc models list` shows which are ready and what each still needs.

| Provider | Service | Key variable | Whose account |
|---|---|---|---|
| `local` | Qwen 32k in the WSL Ollama (section 7b), and the other Ollama models it discovers; `local-128k` is the 128k variant | none | self-hosted: may see restricted data |
| `openai` | the OpenAI API directly; no Portkey needed | `OPENAI_API_KEY` | the user's own |
| `openai-portkey` | OpenAI through UT's Portkey gateway; model written `@<provider-slug>/<model>` | `PORTKEY_API_KEY` | UT (enterprise) |
| `gemini` | Gemini through Google's own API and `langchain-google-genai` (installed 2026-10-27, below) | `GEMINI_API_KEY` | the user's own |
| `claude` | the Anthropic API; needs `python -m pip install langchain-anthropic` first | `ANTHROPIC_API_KEY` | the user's own |

To make one ready, put its key in a keys file outside AutoMC (section 7d) or export it in the shell that runs AutoMC (for example
`export GEMINI_API_KEY=...` in WSL); the shell wins over the file. Never write a key into `llm.yaml` or any other file in AutoMC (the
loader refuses key headers in `llm.yaml`, and a keys file inside AutoMC is refused). Then list the models you can choose (`discover`,
below) and check the one you want once:

```bash
python -m automc models discover                              # numbered, as /model shows them in the chat
python -m automc models check gemini/gemini-3-flash --tools   # a hello, one tool call, the hello with all of AutoMC's tools (~5,000 tokens)
python -m automc chat --provider gemini/gemini-3-flash
```

Gemini needs one more package (2026-10-27, MS-4), the pair RADIANT-LLM runs; the dry run showed nothing installed upgraded
(`langchain-core` stays 1.7.0), only new packages (cryptography 50.0.2, google-auth 2.62.0, tenacity 9.1.4, websockets 16.1.1 and others):

```bash
conda activate automc
python -m pip install "langchain-google-genai==4.4.0" "google-genai==2.23.0"
```

Gemini runs through Google's own API (not its OpenAI-compatible endpoint) because that is the path RADIANT-LLM proved with tool calls,
and Gemini 3 needs its thought signatures carried between tool calls, which this package does. GPT models that take tools only
through OpenAI's Responses API (gpt-5.4 and later 5.x, gpt-6) are sent there automatically (`src/agent/llm.py`).

**Tokens and the cap.** After each turn the chat shows its tokens and the conversation's total on that provider. A cloud provider
allows 200,000 tokens per conversation (`max_tokens_per_session` in `llm.yaml`, about 20 agent steps: one step with AutoMC's prompt
and tools is about 10,000 tokens); a step that would pass it is not sent. A new conversation starts at zero. **Errors** (a wrong key,
a rate limit, spent quota, a timeout) come back in one sentence naming the provider; the conversation is kept, so `/resume` tries the
step again and `/model` switches.

**Which models are offered (2026-10-26).** Each provider's own `model` from `llm.yaml`, plus the models it discovers (`discover:` in
`llm.yaml`, `src/agent/discovery.py`):

- local (Ollama): only models whose capabilities include tools, vision and thinking (`ollama show NAME` lists them), each at the
  context its Modelfile sets (`num_ctx`), else the provider's 32,768. On 2026-10-26 that is the qwen3.6 and qwen3.5 models; the qwen2.5
  models are left out (no vision, no thinking).
- cloud: only OpenAI (directly or through Portkey) and Gemini, with RADIANT-LLM's filters: GPT models from gpt-4 on (no gpt-3.5, no
  o-series, codex, -pro, realtime, audio, image or dated snapshots), Gemini models from gemini-3 on that support generateContent (no
  gemini-2, no omni or live variants). Through Portkey only the `@<slug>/gpt-...` models, never another provider's. Claude is not
  discovered: set its `model` by hand. A listed cloud model can still refuse every call, so a discovered one is used only after
  `models check NAME --tools` has passed once (the chat offers to run it); the result is kept in
  `workspace/.automc/models/checks.json`. Listing models costs nothing; a check costs about 6,000 tokens.

A model is shown, and typed, as `KIND-SERVICE/MODEL`: `local-ollama/qwen3.6:35b-32k-t0`, `cloud-portkey/gpt-5.4`, `cloud-openai/gpt-5.4`,
`cloud-gemini/gemini-3-flash`. A Portkey model's id carries the workspace's `@<slug>/`, which names the account; the screen never shows
it, and it need not be typed (`openai-portkey/gpt-5.4` also works). The full id is kept only in the chat audit. A provider's own name
(`local`, `local-128k`) and the old `PROVIDER/MODEL` form still work.

Cloud models are for public data only. The agent withholds every result labelled restricted or unknown from them (`src/agent/guard.py`).
In the chat, `/model` lists the models and `/model N` (or `/model NAME`) switches; a conversation that holds restricted data
cannot switch to a cloud model (`/new --model N` starts a fresh one). The default local model is the 32k variant
(`qwen3.6:35b-32k-t0`, built from `src/config/ollama/qwen3.6-35b-32k-t0.Modelfile` like the 128k one); `local-128k` is the other choice.

## 7d. The keys file and LangSmith (2026-10-31, GT-1)

One file outside AutoMC holds the keys; `AUTOMC_ENV_FILE` names it. AutoMC takes from it only the variables it uses (the `api_key_env`
names in `llm.yaml`, `TAVILY_API_KEY`, the LangSmith settings) and keeps them inside its own process: they never go into the
environment, so no run, worker or later Python kernel inherits them. Other variables in the file are dropped as it is read. A
variable set in the shell wins over the file. A file inside the AutoMC folder is refused, because that folder is published.

```bash
conda activate automc
python -m pip install "python-dotenv==1.2.3"                 # installed 2026-10-31 (AutoSAM's pin; the dry run added only it)
mkdir -p ~/.config/automc
cp src/config/keys.env.example ~/.config/automc/keys.env     # from AutoMC/; names only, the values empty
chmod 600 ~/.config/automc/keys.env                          # then fill in the values with an editor
echo 'export AUTOMC_ENV_FILE=~/.config/automc/keys.env' >> ~/.bashrc
python -m automc models list                                 # from AutoMC/src: where each key comes from, never its value
```

**Choosing the route and the starting model.** Both OpenAI routes can be set at once: `OPENAI_API_KEY` (OpenAI directly, your own
account) and `PORTKEY_API_KEY` (UT's account); `/model` shows each with its account. Comment a key out (`#` in front) to hide its
route. `AUTOMC_LLM_PROVIDER` in the same file sets the model `python -m automc chat` starts with, for example
`AUTOMC_LLM_PROVIDER=openai/gpt-5.4`; the template lists the usual lines, of which one stays uncommented. Without it the chat starts
with the local model.

The file is AutoMC's own, not the AutoSAM executable's `.env`: AutoSAM uses `OPENAI_API_KEY` ahead of Portkey whenever it is set, and
its docker-compose passes its whole file to the container, so adding AutoMC's OpenAI key there would switch AutoSAM to it.

**LangSmith** traces only what a cloud model may see (`src/agent/tracing.py`). LangChain's own switches (`LANGCHAIN_TRACING_V2`,
`LANGSMITH_TRACING`) are set to false in AutoMC's process, whatever the shell says, so nothing is traced by accident. With
`AUTOMC_TRACING=langsmith` and a key in `LANGSMITH_API_KEY` or `LANGCHAIN_API_KEY` (the same LangSmith token under its older name; a
placeholder too short to be a key is passed over), in the shell or the keys file, the agent sends one
model call at a time to LangSmith, project `automc` (`AUTOMC_LANGSMITH_PROJECT` changes it), and only when:

- the conversation is public (no restricted or unknown result has entered it), and
- the system prompt holds nothing restricted: the model is a cloud one, or no restricted facility pack is installed.

So with the NETL pack installed, conversations with the local model are not traced; they stay in AutoMC's own audit file
(`workspace/.automc/chats/`). The chat says when tracing starts and when it stops. In LangSmith each call is a run with the
conversation as `thread_id`, showing the messages, the tool calls, the answer and the tokens; tool results appear as the inputs of the
next call. If LangSmith refuses the uploads (a wrong key: HTTP 401 or 403), the chat says so once; the conversation itself goes on.

## 7e. The file tools (2026-10-31, GT-2)

`FileListTool`, `FileReadTool` and `FileWriteTool` (`src/tools/files/`). Reading Excel and PDF text needs two packages (AutoSAM's pins;
the dry run added only them and `et_xmlfile` 2.0.0):

```bash
conda activate automc
python -m pip install "openpyxl==3.1.5" "pypdf==5.5.0"
```

The agent reads the workspace, the AutoMC folder, the facility packs, the folders the workspace's settings declare, and the linked
workspaces (read only). Since 2026-11-01 (WD-2) a folder is declared once, with its label, in `<workspace>/.automc/settings.yaml`
(`src/automc_settings.py` explains every key):

```yaml
folders:
  - {name: vp_test, path: /mnt/c/UTA-Research/KBs/vp_test, label: public}
  - {name: netl, path: /mnt/c/NETL/reports, label: restricted}
  - {name: papers, path: /mnt/c/UTA-Research/Projects/Papers}      # no label: unknown origin
links:
  - {name: projectA, path: /mnt/c/projects/projectA}               # another workspace: its cases, runs and knowledge bases, read only
```

For one session, three variables do the same without editing the file (each a list separated by `:`): `AUTOMC_PUBLIC_ROOTS`,
`AUTOMC_RESTRICTED_ROOTS`, and `AUTOMC_ROOTS` for folders of unknown origin. `AUTOMC_FILE_ROOTS`, the old name of `AUTOMC_ROOTS`, is still
read until the next release, with a warning. `python -m automc config show` prints every setting in effect and where it came from.

Whatever is declared, it never reads or lists keys and credentials: the keys file, `.env` files, `*.pem`, `*.key`, `id_rsa` and the like,
`.ssh/`, `.aws/`, `.netrc`, `credentials*`. It writes only inside the workspace, never into a case or a run folder, never into
`.automc/`, never over an existing file unless told to, and never under the name of one of AutoMC's records (`result.json` and the like).

**Two modes, set by what is declared, never by a switch.** Open: nothing restricted is declared (no restricted facility pack, no folder
labelled restricted, no `mode: facility`); every place counts as public and a cloud model is refused only what is labelled restricted.
This is what a pip or Docker user outside NETL gets. Facility: something restricted is declared; then each result carries the label of
the place it comes from, which decides what a cloud model may see (`src/agent/guard.py`):

| Place | Label | A cloud model |
|---|---|---|
| a restricted facility pack's folder, or a folder declared restricted | restricted | never |
| a case (of the workspace or a linked one) | the label its builder recorded (unknown when none) | only if public |
| `src/` (AutoMC's code, skills, public pack), or a folder declared public | public | yes |
| anywhere else (the rest of the workspace and AutoMC, folders of unknown origin) | none | no |

A workspace that has held restricted data keeps `mode: facility` in its settings (`workspace upgrade` writes it), so removing the pack
later never opens it. The local model reads everything that is declared.

## 7f. Knowledge bases from folders of PDFs and images (2026-11-01, GT-3R)

`DocumentParseTool`, `DocumentCheckTool`, `DocumentSearchTool` and `DocumentCancelTool` (`src/tools/documents/`) work as RADIANT-LLM,
AutoSAM and AutoFLUKA read documents: point at a folder; Visual-Parser parses its new PDFs and images into its files; the rows are
embedded on this machine and searched by meaning. Visual-Parser is the package AutoSAM and RADIANT-LLM use, at AutoSAM's pin. It brings
torch with CUDA, transformers (for Nougat) and LangChain's community packages, about 70 packages and several GB. The dry run on
2026-10-31 changed none of the packages already in `automc`, so it is installed there:

```bash
conda activate automc
python -m pip install --dry-run "visual-parser==2.2.4"     # check first: nothing already installed may change
python -m pip install "visual-parser==2.2.4"
visual-parser --version                                    # visual-parser 2.2.4
```

If a later install would change one of AutoMC's packages, put Visual-Parser in its own environment instead and name its command:
`export AUTOMC_VISUAL_PARSER=~/miniforge3/envs/vp/bin/visual-parser`. AutoMC only runs the command; it never imports the package.

Nougat downloads its model (`facebook/nougat-small`, from Hugging Face) on the first parse. The local vision model is one of Ollama's;
Visual-Parser picks the largest vision-capable one that fits in the free GPU memory (on this laptop `qwen3.5-9b-16k` when nothing else
is loaded). After a parse, unload it if the GPU is needed: `curl -s localhost:11434/api/generate -d '{"model": "NAME", "keep_alive": 0}'`.
On the first real parse (2026-10-31) that model gave empty answers for the figure pages, about 6 minutes each; `docs check` shows
such pages as a warning (the text is not affected). Until that is solved, figures are described only for a public folder with a
cloud vision model (`--vision`, or a cloud chat model), and a local parse takes minutes per page.

**Embedding** is always on this machine: Ollama's `nomic-embed-text` (274 MB), the model Axiom and vpx use, chosen by the retrieval
experiment (`devops/validation/GT3R_RETRIEVAL.md`). The store is SQLite with `sqlite-vec`, one file per knowledge base:

```bash
ollama pull nomic-embed-text                                # in the Ollama AutoMC uses (WSL has its own)
python -m pip install --dry-run "sqlite-vec==0.1.9"         # added only itself on 2026-11-01
python -m pip install "sqlite-vec==0.1.9"
```

Without the model (or without `sqlite-vec`) a run still parses, and search is by full text until the folder is ingested again with the
model there. Each knowledge base keeps the model it was built with; when Ollama's build of it changes, the next ingest embeds every row
again rather than mix the two.

Where the page images go follows the folder's label, not the chat model:

| Folder | Vision model | Keys the run's process gets |
|---|---|---|
| public (`src/` or a folder declared public), chat model OpenAI, Portkey or Gemini | the chat model, with your key | only that one |
| public, any other chat model | local (Ollama) | none |
| restricted, or not known to be public | local (Ollama) | none: every key variable is set empty |

Every run asks first, from the chat or the command line, and goes on in the background:

```bash
python -m automc docs ingest /mnt/c/path/reports [--name NAME] [--text-mode lightweight] [--vision openai/gpt-5.4]
python -m automc docs watch JOB_ID
python -m automc docs search what is the zirconium hydride density
python -m automc docs list                      # every knowledge base
python -m automc docs check --kb reports        # its files, documents, what is embedded, its last run
python -m automc docs ingest /mnt/c/path/reports --redo report_a.pdf     # parse a changed file again
```

Run `ingest` again after adding files: only the new ones are parsed. The plan says what it found:

| In the folder | What happens |
|---|---|
| a file not parsed before | parsed |
| a file parsed before, bytes the same | kept |
| bytes changed, same text, drawing and pictures (a saved highlight or note, a new title, Save As) | kept |
| text or pictures changed on some pages | listed with the pages; parsed again only with `--redo NAME` (the chat: "parse it again") |
| the same content as a parsed file, under another name | skipped |
| a second file with a name already used (in another subfolder) | skipped: Visual-Parser knows files by name |
| a parsed file gone from the folder | its rows stay |

Each knowledge base is `workspace/.automc/documents/<name>/` (the name defaults to the folder's): Visual-Parser's files
(`01_chunks_kb.jsonl`, `02_visuals_kb.jsonl`, `03_metadata_kb.jsonl`, `image_descriptions_kb.jsonl`, its lists of parsed files, its
log), `index.sqlite` (about 25 KB per page) and `kb.json` with the label, the files and the runs. The document folder is only read: a
run gives Visual-Parser a folder of links to the files it parses.

## 7g. The web and Python (2026-11-01, GT-4 and GT-5)

Nothing new to install: `requests`, `beautifulsoup4`, `pypdf`, `jupyter_client` and `ipykernel` are already in `automc`.

**The web** (`WebSearchTool`, `WebFetchTool`) searches with Tavily, as AutoSAM does. Put `TAVILY_API_KEY` in the keys file (section 7d);
the free tier is 1,000 searches a month, and every result says how many this conversation and this month have used. Check it once:

```bash
python -m automc tool WebSearchTool '{"query": "half-life of promethium-147"}'
```

What the model writes for the web leaves this machine, so the conversation's label decides, with any model:

| The conversation holds | A search or a page request |
|---|---|
| only public results | sent |
| results of unknown origin | the chat asks; sent only if you type `yes, send` |
| restricted facility data | never sent: ask in a new conversation |

`WebFetchTool` reads only the public internet: an address on this machine or a private network is refused, and so is a redirect to one.
A PDF it fetches is kept in `workspace/.automc/web/downloads/<site>/`, where `docs ingest` can make a knowledge base of it.

**Python** (`PythonRunTool`) runs the model's code in a Jupyter kernel of its own, one per conversation, in
`workspace/.automc/python/<conversation>/`. Every run is shown to you with its code, numbered, and runs only after you say yes; the
plan warns when the code would run OpenMC itself, start other programs, reach the network or delete files. The kernel's environment has
no key (every variable whose name looks like a key, token, secret or password is left out) and draws figures to PNG files. A run stops
at its time limit (120 s unless the model asks for more, at most 30 min); Ctrl-C in the chat stops it too. In WSL the kernel and its
variables survive the stop; on Windows a kernel that does not take the interrupt is restarted, and the result says its variables are gone. The whole output is kept
(`runs/run-NNN.txt`, the code beside it); the model reads about 10,000 characters of it with the local 32k model, more with a larger
window, the start and the end. It is not a sandbox: approved code can do what you can.

With a cloud model and the restricted pack installed, the chat asks `Run it? [y/N, or 'yes, send' ...]`: `y` runs it, shows you the
output in the chat (its start and end, and the file with the whole of it) and withholds it from the cloud model; `yes, send` lets the
model read it too. Code that waits for typed input (`input()`) cannot run (nothing can type into the kernel); the plan warns about it.

## 7h. The workspace, its settings and its version (2026-11-01, WD-0 to WD-2)

A workspace is the folder AutoMC works in: `AutoMC/workspace` unless `AUTOMC_WORKSPACE` names another, or `--workspace DIR` on any
command (before or after it: `python -m automc chat --workspace /mnt/c/projects/projectA`). Your cases and runs are in it, in any
subfolder up to four deep (`study-8/c8`). Everything AutoMC keeps there is in one hidden folder, `.automc/`:

```
<workspace>/
  my-case/  study-8/c8/      your cases and runs
  .automc/
    settings.yaml            folders, labels, links, policies (section 7e)
    format.json              the layout's format and the AutoMC version that wrote it
    chats/  documents/  jobs/  python/  web/  models/
```

**A workspace made before 0.8.0** kept up to eight hidden items at its root (`.automc_chats/`, `.automc_documents/`, `.automc_jobs.json`
and so on). The commands that use them (`chat`, `run`, `docs`, `eval`, `tool`, `models`) refuse it until it is upgraded; building and
reading runs go on. Close every AutoMC chat of the workspace, then:

```bash
python -m automc workspace upgrade --dry-run    # what moves and changes; nothing is touched
python -m automc workspace upgrade              # asks, then does it; nothing is deleted
python -m automc workspace usage                # what takes space
```

The upgrade moves each item into `.automc/`, points the run registry and each knowledge base at the new places (a knowledge base's
source folder becomes a reference that survives moving the workspace), declares those source folders in `settings.yaml` with the label
they had, and writes `mode: facility` when the workspace holds anything restricted or of unknown origin. It refuses while a run or a
parse is active, and can run twice. Old chat audits go to `.automc/chats/legacy/`.

**One version.** `python -m automc --version` prints AutoMC's version (`src/automc_version.py`, 0.8.0) and a fingerprint of the system
prompt, the skills and the evaluation cases, computed from their files: two chats or evaluations with the same fingerprint saw the
same prompt and skills. The prompt and the cases have no version of their own any more; the history is in `CHANGELOG.md`.

## 7i. Saved conversations and memory (2026-11-01, WD-3 and WD-4)

Every conversation is saved as it goes, in `.automc/chats/<id>.jsonl` (one file per conversation, named by a random UUID; it is also
the conversation's audit). In the chat:

| Command | Does |
|---|---|
| `/sessions` | the saved conversations of this workspace, then those of the linked workspaces, numbered |
| `/show N`, `/load N`, `/delete N` | read one; continue one (a linked one is copied here first; the original stays); delete one of this workspace's |
| `python -m automc chat --load N` | start the chat with a saved conversation |

A conversation's data label comes with it: one holding restricted data is not loaded with a cloud model (use the local model), one of
unknown origin only after you type `yes, send`. **A conversation that holds restricted data is not saved from that point on** unless the
workspace's settings say `conversations: {save_restricted: true}` (decision W3: NETL's data owners decide; the default is no). What came
before stays; the file says from where it stopped. Whatever happens to the chats, `.automc/egress.jsonl` records what left the machine:
each cloud model call, LangSmith trace, web request and cloud page-image parse, with its time, service and label, never its content.

The agent can search earlier conversations (ChatSearchTool) when you refer to one; it searches this workspace and the linked ones only.

**Memory.** Write `AUTOMC.md` at the workspace root for what every conversation should know (what the project is, how you want things
done). It goes into every conversation's system prompt; in facility mode a cloud model gets it only if its first line is `label: public`.
Notes are kept in `.automc/memory/`, one fact each, with an index the prompt holds:

| | |
|---|---|
| the agent proposes a note | the chat shows it and asks `Save it? [y/N]`; it is saved with the conversation's label |
| `/remember TEXT` (`/remember --public TEXT`) | you save a note yourself |
| `/memory`, `/memory show NAME`, `/memory forget NAME`, `/memory public NAME` | list, read, forget, mark public (only you lower a label) |

A cloud model in facility mode sees only public notes. `AUTOMC.md` and the index are capped at about 2,000 tokens together (the 32k local
model has about 21,000 left after the prompt).

## 7j. Docker (2026-11-01, WD-5)

The image (`Dockerfile`) holds AutoMC's code, its public pack and the library snapshots, OpenMC 0.15.3 and the packages pinned in
`docker/requirements.txt`, and (by default) Visual-Parser with torch. It never holds the restricted packs, the nuclear data, keys or
your projects: those are mounted when it runs (`docker-compose.yml`).

```bash
docker build -t automc:0.8.0-lite --build-arg VISUAL_PARSER=no .     # from AutoMC/; without Visual-Parser (several GB smaller)
docker build -t automc:0.8.0 .                                         # with it
python docker/check_image.py automc:0.8.0                              # before any image is shared: no restricted data, keys, names
docker compose run --rm automc chat                                    # a chat in /host/workspace
AUTOMC_PROJECT=projectA docker compose run --rm automc chat            # another project folder under /host
```

| Mount | From (variable, default) | In the container |
|---|---|---|
| your projects | `AUTOMC_HOST`, `./host` | `/host` (the workspace is `/host/<project>`) |
| keys file and user settings | `AUTOMC_CONFIG`, `~/.config/automc` | `/config`, read only |
| nuclear data | `AUTOMC_NUCLEAR_DATA`, `./downloads/endfb-viii.0-hdf5` | `/data/nuclear`, read only |
| the restricted packs (NETL only) | `AUTOMC_RESTRICTED_PACKS`, with `-f docker-compose.netl.yml` | `/packs`, read only |

`AUTOMC_IMAGE=automc:0.8.0-lite` makes compose use the lite image. On Windows the keys file in WSL is reached with
`AUTOMC_CONFIG=//wsl.localhost/Ubuntu/home/<you>/.config/automc`. The local model is the host's Ollama (`host.docker.internal:11434`). Inside the container `python: {approval: auto}` in a workspace's
settings lets Python run without asking (the container is the sandbox; the code still shows in the chat and is saved); outside a
container every run asks, whatever the settings say. The container runs as an ordinary user and is limited to `AUTOMC_CPUS` cores
(default 8), so OpenMC runs leave room for the model.

## 7k. Data for the working examples (2026-11-02, AU-2 batch 5)

Five working examples need files AutoMC does not ship: CAD geometries (DAGMC `.h5m`), tetrahedral meshes, two ENDF/B-VIII.0 files and a
windowed multipole file. `src/skills/working_examples/data.json` lists each with its source and SHA-256. Fetch them once (the network
is needed; 184 MB on disk, 103 MB of it one CAD model):

```bash
cd ~/AutoMC/src                                   # in the automc environment
python -m automc data list                        # what each example needs, and what the cache has
python -m automc data fetch                       # all of them; or name examples: data fetch cad-geometry nuclear-data
python -m automc data verify                      # check the cache against the SHA-256 sums
```

They go to the data cache: `AUTOMC_DATA_CACHE`, else `~/.cache/automc/data`. A file is kept only when its SHA-256 matches; the two
ENDF files are read out of NNDC's 310 MB archive by range requests, about 0.8 MB. An example copies what it needs into its case, so
every run folder carries it; one whose file is missing says which `data fetch` to run. The Docker image does not hold them: mount the
cache and set `AUTOMC_DATA_CACHE` to the mount.

**The slow tests.** The ordinary suite checks that every example is complete. The slow tier also builds every variant of every
example through the build tool, checks it, runs it through the job service and compares its results with its `expected.json`, and it
provokes the OpenMC messages the error knowledge base knows, so a hint an upgrade changed fails here (WSL, with OpenMC):

```bash
cd ~/AutoMC/src
python -m automc data fetch                       # first: an example whose files are not in the cache fails, it is not skipped
AUTOMC_SLOW_TESTS=1 python -m unittest tests.examples.test_examples tests.skills.test_erkb_messages
```

On 2026-11-05 it was 42 tests in 417 s, with 4 threads per run (`AUTOMC_EXAMPLE_THREADS`, default 4). Run it after a change to an
example, a rule it tests or OpenMC; `AUTOMC_SLOW_TESTS=1` with `discover` runs it with the rest of the suite.

## 8. Directory layout reference

```
ZavierNdum/Code/AutoMC/
├── SETUP.md                      <- this file
├── scripts/
│   ├── miniforge.sh               (installer, kept for reference)
│   ├── start_automc_jupyter.sh    (editable source; deployed copy lives at ~/start_automc_jupyter.sh in WSL)
│   └── start_jupyter.bat          (double-click launcher)
├── downloads/
│   └── endfb-viii.0-hdf5/         (mirrors ~/endfb-viii.0-hdf5 in WSL — check before assuming either is authoritative)
├── openmc-reference/              (pristine clone, v0.15.3, docs + script examples)
├── openmc-notebooks/              (pristine clone, 28 official example notebooks)
└── Learning/
    ├── 01_toy_examples.ipynb      (hand-built: point-source dose in a sphere; fluence through 2 slabs)
    └── openmc-examples/           (working copies of the 28 official notebooks + their data, run here)
```

## Known rough edges

- `netl_triga_dt-master` and `tunek3-main` haven't had the freshness check (diff against a fresh
  clone) that CoreForge/MPACTPy/FlowForge got — their git history/drift status is unknown.
- The Jupyter server launcher binds the token to `~/.automc_jupyter_token` — if that file is ever
  deleted, a new random token is generated on next start, and you'll need to re-paste the URL once
  in VS Code's "Existing Jupyter Server..." picker.
