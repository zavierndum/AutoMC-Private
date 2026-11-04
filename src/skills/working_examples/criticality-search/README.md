# Criticality search: the boron that makes a pin critical

Derived from: the OpenMC notebook "Criticality Search" (openmc-notebooks, tag v0.15.0; the pin, its materials and the search bracket).
Written for AutoMC.

## The problem

A PWR controls its excess reactivity with boric acid dissolved in the coolant. For a 1.6%-enriched pin in an infinite lattice, which
boron concentration makes k-infinity exactly 1? OpenMC answers this with `openmc.search_for_keff`, which:

1. builds the model at a guess;
2. runs it;
3. moves the guess (here by bisection) until k reaches the target.

## The model

| Part | Value |
|---|---|
| fuel | UO2, 1.6 wt% U-235, 10.31341 g/cm3, r = 0.39218 cm (no gap) |
| cladding | zirconium, 6.55 g/cm3, to r = 0.45720 cm |
| coolant | water at 0.741 g/cm3 (about 300 C and 15.5 MPa), bound hydrogen, natural boron |
| cell | 1.26 cm square, mirrored sides (an infinite lattice) |

**Boron is given in ppm by weight**, the unit plant chemistry uses. The notebook passed `ppm * 1e-6` to `add_element` next to H 2 and
O 1, which OpenMC reads as atom fractions: boron atoms per million water molecules. Converting the notebook's answer (about 1,900 of
those) by 10.81 / 18.015 gives about 1,140 ppm by weight, close to the 1,148 found here. The pin also has bound hydrogen, which the
notebook left out.

## How it runs

- **`build.py`** defines the model as a function of the boron and writes `model.xml` at the OPTIONS boron, for plotting and checking.
- **`run.py`** is the search. It runs transport about ten times, which a build script may not do, so it is a **script run**:
  - OpenMCRunStartTool with `case` and `script`; the user approves it like any run;
  - it runs in a copy of the case folder, imports `build.py` from there (OPTIONS included) and writes `search.json`;
  - each guess runs in `search-runs/`, so the frozen `model.xml` stays as built.
- **`post.py`** reads `search.json`: every guess with its k, whether the bracket held the answer, and the boron worth from a weighted
  straight-line fit of k against boron.

## Results (OpenMC 0.15.3, ENDF/B-VIII.0)

| Size | Histories per guess | Critical boron | Guesses | Boron worth | Time here |
|---|---|---|---|---|---|
| quick | 1,000 x (80 - 20) | 1,148 ppm | 10 | -12.8 pcm/ppm | 21 s |
| reference | 10,000 x (220 - 20) | 1,148 ppm | 10 | -12.6 pcm/ppm | 286 s |

- **Why both sizes agree.** Bisection halves the bracket the same way while the sign of k - 1 at each midpoint is clear, so both runs
  land on the same node. The 1% tolerance (`tol`, relative, on the boron) makes the answer good to about 11 ppm.
- **The straight-line fit** crosses k = 1 at about 1,168 ppm. Across 500 to 2,500 ppm, k bends slightly, so the fit is a guide to the
  worth, not a better answer than the search.

## Running it with AutoMC

1. **Build:** `OpenMCModelBuildTool` with `build.py`.
2. **Plot and check:** `OpenMCGeometryPlotTool`, `OpenMCModelCheckTool`.
3. **Run:** `OpenMCRunStartTool` with `case` and `script` (the text of `run.py`), approved by the user. It can be stopped or cancelled,
   not paused.
4. **Read:** `python post.py <run folder> --plot k_vs_boron.png` through `PythonRunTool`.

Change `bracket_ppm` if k does not change sign across it: the search then fails at once and says so.

## What it teaches

- A model written as a function of a parameter.
- `search_for_keff` with a bracket and bisection.
- Boron by weight, declared with weight fractions.
- A script run, and why it is not a build.
- Reading a boron worth.
