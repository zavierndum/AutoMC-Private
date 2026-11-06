# AutoMC

An AI agent for OpenMC that builds, checks and runs Monte Carlo models with your approval, then explains the results and errors,
using local or cloud language models. It writes a model as a Python build script from your description, starting from the nearest of
29 working examples, draws and checks it before anything runs, and starts a run only when you approve it.

Version 0.8.0, not released. This is the development repository.

## What is here

| Path | What |
|---|---|
| `src/` | The package: the agent, its tools, the system prompt, the skills (working examples and the error knowledge base), the evaluation cases, the public facility pack and the tests. Layout: [src/README.md](src/README.md). |
| `library_snapshots/` | Pinned copies of other people's code AutoMC builds on, each with its own license. |
| `Dockerfile`, `docker-compose.yml`, `docker-compose.netl.yml`, `docker/` | The container and the check every image passes before it is shared. |
| [SETUP.md](SETUP.md) | Installing the environment, the nuclear data, the models and keys, Docker. |
| [DESIGN.md](DESIGN.md), [TOOLS.md](TOOLS.md), [CHANGELOG.md](CHANGELOG.md) | The design and its decisions, the tools, the history. |

## What is not here

- **Nuclear data.** OpenMC needs the ENDF/B-VIII.0 HDF5 library (about 13 GB), installed once as SETUP.md section 3 says. The
  container mounts it read only.
- **Data for some working examples** (CAD geometries, meshes, ENDF files): `python -m automc data fetch` downloads them and checks
  them against their SHA-256.
- **Restricted facility packs.** They live outside AutoMC and are mounted only where they may be used.
- **Workspaces, runs and keys.** Keys come from a file outside AutoMC (SETUP.md section 7d).

## Start

```bash
cd src
python -m automc chat                      # the chat; /help lists its commands
python -m unittest discover -s tests -t .  # the tests
```
