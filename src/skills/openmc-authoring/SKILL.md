---
name: OpenMC authoring
description: Writing an OpenMC model of any system as a build script; the case spec first, then the rule file for each part of the model, read on demand.
---

# openmc-authoring

## When to use
The user wants a model of a system that the TRIGA tools do not build. The prompt's "Writing a model" gives the order of work; this
skill says where the detail is. Read files with SkillLookupTool, `file` set to the path below (under the skills folder).

## Before writing build.py
Fill the case spec, `openmc-authoring/templates/case_spec.yaml`: the quantity wanted with its units, the geometry, every material,
the source, the tallies, the run mode and sizes. Every value the user did not give goes under assumptions; ask about what is
missing when it changes the answer, and state the rest with the result.

## Rule files: read the ones for the parts you are writing
| Part of the model | File (`openmc-authoring/rules/...`) |
|---|---|
| compositions, densities, S(a,b), temperatures | `materials.md` |
| surfaces, regions, cells, boundaries | `geometry.md` |
| universes, rectangular and hexagonal lattices, TRISO | `universes-and-lattices.md` |
| run mode, batches, triggers, limits | `settings.md` |
| source position, direction, energy, strength | `sources.md` |
| filters, scores, meshes, estimators | `tallies.md` |
| per source particle to per second, power | `normalization.md` |
| dose coefficients and dose rates | `dose.md` |
| geometry plots | `plots.md` |
| weight windows and the figure of merit | `variance-reduction.md` |
| multigroup data and multigroup mode | `multigroup.md` |
| depletion | `depletion.md` |
| CAD (DAGMC) and unstructured meshes | `cad-and-meshes.md` |
| reading and processing nuclear data | `nuclear-data.md` |
| photon transport and pulse-height spectra | `photons.md` |
| openmc.lib and criticality searches | `c-api.md` |
| volumes | `volume.md` |
| mistakes that give no error, and those that stop a run | `common-mistakes.md` |

## Writing it
Start from the nearest working example (`working_examples/index.md`); when none is near, from `openmc-authoring/templates/build.py`,
which has the structure every example follows. OpenMC's units: cm, eV, K, g/cm3; tallies are per source particle.
