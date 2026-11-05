# AutoMC skills

What the agent knows besides its tools, in the AutoSAM skill format: one folder per skill with a `SKILL.md` (front matter `name` and
`description`), loaded into the agent's context. Policy: `SKILL_POLICY.md`.

| Skill | Folder | What it covers |
|---|---|---|
| Core workflow | `core/` | how to work: tools first, never block on a run, the user approves every start, report numbers with their errors and assumptions, facility-data labels |
| OpenMC authoring | `openmc-authoring/` | writing a model of any system: the case spec, and one rule file per part of the model (materials, geometry, lattices, settings, sources, tallies, normalisation, dose, plots, variance reduction, multigroup, depletion, CAD and meshes, nuclear data, photons, openmc.lib, volumes, common mistakes), read on demand; a build-script template |
| OpenMC validation | `openmc-validation/` | the review gate around a run: what the model check sees and misses, overlaps and cuts, the builder's version; the fission source's convergence; relative errors, the figure of merit, biased maxima; comparing fairly with benchmarks and other codes (rule files on demand) |
| OpenMC execution | `openmc-execution/` | the job tools: start (a plan the user approves), check, pause, resume, stop, cancel; statuses; start-up time; sequences; troubleshooting on demand (crashes, lost particles and their replay, geometry-debug runs and their coverage, CAD file checks, depletion hangs) |
| OpenMC post-processing | `openmc-post-processing/` | reading a run: k-eff and its error, active batches, worths in pcm and dollars, plausibility checks, tallies; rule files on demand for statepoints and summaries, tally arrays and mesh order, spectra, derived numbers and their errors, depletion and multigroup results |
| Files, documents, web and Python | `general-tools/` | which general tool when; knowledge bases (make, then search, cite file and page); what leaves the machine through the web; Python runs the user approves; what each refuses |
| TRIGA Input Builder | `reactors/triga/` | the NETL TRIGA builder: presets, configurations and insert labels, rod units, run sizes, sweeps, what each build reports |
| Error knowledge base | `ERKB.yaml` | known OpenMC and builder errors: how to recognize each and what to do; matched in code against every failed tool result (`known_errors`), read whole on demand |

Facility packs can add skills of their own (`provides: skills` in their `pack.yaml`); a restricted pack's skills are loaded only for a
local model.
