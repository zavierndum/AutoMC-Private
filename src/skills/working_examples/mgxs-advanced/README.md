# MGXS advanced: eight-group data of a PWR pin, by nuclide, with triggers

Derived from: the OpenMC notebook "MGXS Part II: Advanced Features" (openmc-notebooks, tag v0.15.0; the pin, the 8- and 2-group
structures, the cross section types and the trigger). Written for AutoMC.

## The problem

A lattice code needs more than a mixture's total cross sections. It needs them in every region of a pin, the scattering between
groups as a matrix, the fission spectrum, and sometimes each nuclide's microscopic values so densities can change later. This example
makes all of these for a PWR pin cell in eight groups, and stops the run when the cross sections are precise enough.

## The model

| Part | Value |
|---|---|
| fuel | UO2, 1.6% enriched, 10.31341 g/cm3, r = 0.39218 cm |
| cladding | Zr-90 at 6.55 g/cm3, to r = 0.45720 cm |
| water | 0.740582 g/cm3, bound hydrogen |
| cell | a 1.26 cm cube, mirrored on every face |
| groups | eight (edges 0, 0.058, 0.14, 0.28, 0.625, 4.0, 5.53 keV, 821 keV, 20 MeV) and two (0.625 eV) |

In every cell, eight-group transport, fission and nu-fission cross sections and the nu-scattering matrix; in the fuel also chi, and
fission and nu-fission by nuclide. A two-group fission cross section is tallied directly as well, to test condensation against it.

## How it runs

- **`build.py`** makes the MGXS objects for the three cells (`cross_sections()`). It gives each a tally trigger, a relative standard
  deviation it must reach. OpenMC then runs past `batches`, checking every 5 batches, until every trigger is met or `max_batches` is reached.
- **`post.py`** loads them, prints U-235's microscopic nu-fission, chi and water's upscattering, and checks three rules:
  - **macroscopic = the sum over nuclides of atom density x microscopic**, in every group;
  - **condensation:** the eight-group fission cross section condensed to two groups (`get_condensed_xs`) equals the two-group one
    tallied directly, since they bin the same events;
  - **chi sums to 1.**

## Results (OpenMC 0.15.3, ENDF/B-VIII.0)

| Size | Particles | Batches run | k-infinity | Time here |
|---|---|---|---|---|
| quick | 5,000 | 60 (the limit) | 1.21940 ± 0.00190 | 12 s |
| reference | 10,000 | 200 (the limit) | 1.22111 ± 0.00067 | 64 s |

At the reference size:

- **U-235 microscopic nu-fission** (barns), groups 1 (fast) to 8: 3.3, 3.98, 55.5, 89.5, 292, 466, 700, 1,410. It rises by more than
  two orders of magnitude from fast to thermal.
- **Chi:** 0.751 in group 1, 0.249 in group 2, 0.0002 in group 3, nothing below: fission neutrons are born fast.
- **Water:** 5.8% of its scattering moves neutrons up in energy. That is thermal upscattering, which a lattice code must keep.
- **The rules** hold to rounding: macroscopic against microscopic exactly, condensation to 2e-15.
- **The triggers** were not all met before `max_batches` in either size, so both runs stopped at their limit. Raise `max_batches`, or
  relax `std_dev` in `SIZES`, to let the trigger decide.
- **The notebook's k** was 1.22204 ± 0.00135 with older nuclear data, 0.6 of its standard deviation from the value here.

## Running it with AutoMC

1. **Build:** `OpenMCModelBuildTool` with `build.py`.
2. **Check:** `OpenMCModelCheckTool`.
3. **Run:** `OpenMCRunStartTool`, approved by the user. The batch count shown in the plan is the minimum; the triggers can add batches.
4. **Read:** `python post.py <run folder>` through `PythonRunTool`.

## What it teaches

- MGXS objects in each cell of a heterogeneous pin.
- By-nuclide microscopic cross sections, and how the macroscopic ones follow from them.
- The scattering matrix and its upscattering, and the fission spectrum chi.
- Condensing fine groups to coarse ones, checked against a direct tally.
- Tally triggers: a run that stops when its answers are precise enough, and what it means when it stops at the limit instead.
