# An MGXS library and multigroup mode: a PWR assembly in CE, then in MG

Derived from: the OpenMC notebooks "MGXS Part III: Libraries" and "Multigroup Mode Part II: MGXS Library Generation with OpenMC"
(openmc-notebooks, tag v0.15.0; the assembly, the library's types, domains and Legendre order, the CE-against-MG comparison), with the
library form of "MDGXS Part II". Written for AutoMC.

## The problem

Multigroup mode runs much faster than continuous energy and is how deterministic codes are tested on the same geometry, but it needs
cross sections. OpenMC can make its own: run the problem once in continuous energy with an `openmc.mgxs.Library` collecting group
data for every material, write that library as a multigroup cross section file, and run the same geometry again in multigroup mode.
If the library is good, the two eigenvalues agree. Their difference is the error of the multigroup approximation, here with only two
energy groups.

## The model

| Part | Value |
|---|---|
| assembly | 17 x 17 pins at 1.26 cm, 264 fuel pins and 25 water-filled guide tubes, mirrored on every side |
| fuel | UO2, 1.6% enriched, 10.31341 g/cm3 |
| cladding | Zr-90 at 6.55 g/cm3 |
| water | 0.740582 g/cm3 with a little boron-10 |
| library | two groups (0.625 eV), one data set per material, types total, absorption, fission, nu-fission, chi, the nu-scattering matrix and the multiplicity matrix, scattering to Legendre order 3 with no transport correction |

## How it runs

- **`build.py`** builds the CE model with the library's tallies (`build_with_library()`), for plotting and checking.
- **`run.py`** is a **script run**, because it runs transport twice with a step between:
  1. it runs the CE model in `ce/`;
  2. it loads the library from that statepoint (`load_from_statepoint`);
  3. it makes the MG file, the materials that point into it and the same geometry with them (`create_mg_mode`), and writes
     `mgxs.h5`;
  4. it runs the geometry in multigroup mode in `mg/` and writes `comparison.json`.
- **`post.py`** reads `comparison.json`: both k values, the MG bias in pcm and in combined standard deviations, and what `mgxs.h5`
  holds.

## Results (OpenMC 0.15.3, ENDF/B-VIII.0)

| Size | Histories per run | CE k-infinity | MG k-infinity | MG - CE | Time here |
|---|---|---|---|---|---|
| quick | 3,000 x (60 - 20) | 1.01898 ± 0.00265 | 1.01862 ± 0.00180 | -36 pcm (-0.1 σ) | 8 s |
| reference | 20,000 x (220 - 20) | 1.02068 ± 0.00043 | 1.02177 ± 0.00034 | +109 pcm (+2.0 σ) | 128 s |

- **Two groups are enough here, to about 100 pcm.** The library comes from the same assembly it is used in, so its group averages carry
  the right spectrum. A library used in another problem would not.
- **The +109 pcm at the reference size** is 2.0 combined standard deviations: part statistics, part the multigroup approximation (two
  groups, scattering to P3). A longer run, more groups or a higher Legendre order would separate the two. Differences here are in
  k, x 10⁵.
- **The notebook's CE k** (MGXS Part III) was 1.02362 ± 0.00128 with older nuclear data.

## Running it with AutoMC

1. **Build:** `OpenMCModelBuildTool` with `build.py`.
2. **Plot and check:** `OpenMCGeometryPlotTool`, `OpenMCModelCheckTool`.
3. **Run:** `OpenMCRunStartTool` with `case` and `script` (the text of `run.py`), approved by the user. It can be stopped or
   cancelled, not paused.
4. **Read:** `python post.py <run folder>` through `PythonRunTool`.

`legendre_order` in `OPTIONS` changes the scattering order of the library (0 to 3; run.py reads it from `build.py`).

## What it teaches

- An `openmc.mgxs.Library`: its energy groups, types, domain type, domains and Legendre order.
- From a CE statepoint to an MG cross section file (`create_mg_mode`, `export_to_hdf5`), and an MG run of the same geometry.
- Measuring a multigroup library by its eigenvalue against the CE run it came from.
- A script run that chains two transport runs.
