# Tallies as pandas DataFrames: a pin map, distributed cells, a trigger

Derived from: the OpenMC notebook "Pandas Dataframes" (openmc-notebooks, tag v0.15.0; the assembly, its boundaries, tallies and
trigger). Written for AutoMC.

## The problem

A 17 x 17 assembly of the notebooks' 1.6% pins (see `post-processing`) sits at the corner of a core:

- its left and top sides are mirrored, because the core continues there;
- its right and bottom sides are open (vacuum), the edge of the core;
- it is mirrored in z.

What is the fission rate in each pin, how flat is it, and how many batches does a precision target need?

## The tallies

- **The pin map.** A 17 x 17 mesh tally with two energy groups (below and above 0.625 eV) scores fission and nu-fission. Its
  **trigger** asks for 2% relative error on fission (10% at the quick size): after the minimum batches OpenMC checks every 5 batches and
  stops once it is met, or at `trigger_max_batches`.
- **The distributed-cell tally.** `DistribcellFilter` scores absorption in the fuel cell, once for each of its 289 instances. In the
  DataFrame, each row's index columns name where that instance sits in the geometry.

## Reading them

`tally.get_pandas_dataframe()` returns one row per bin.

- **Mesh columns.** They form a MultiIndex (`("mesh 1", "x")`, ...). `post.py` flattens them.
- **Building the map:**
  1. keep the fission rows;
  2. sum the two groups with `groupby(["x", "y"])`;
  3. `unstack` to a 17 x 17 grid;
  4. normalize to the mean.
- **Errors.** Combine the groups' standard deviations in quadrature.

## Results (OpenMC 0.15.3, ENDF/B-VIII.0)

| | quick (4,000 per batch, 60 batches, 8 s) | reference (20,000 per batch, 200 batches, 54 s) |
|---|---|---|
| k-eff | 0.68353 +/- 0.00188 | 0.68687 +/- 0.00046 |
| hottest pin / mean | 2.09, at pin (1, 16) | 2.08, at pin (2, 17) |
| coldest pin / mean | 0.048, at pin (17, 1) | 0.042, at pin (17, 1) |
| worst mirror pair | 2.9 sigma | 3.4 sigma |
| thermal share of fissions | 0.813 | 0.813 |

- **The coldest pin** is in the corner between the two open faces.
- **The hottest pin** is beside the mirrored corner. Next to a mirror the flux has no slope, so the two or three pins there are equal
  within their noise.
- **Symmetry.** The map is symmetric across the diagonal from the mirrored corner to the open one: pin (x, y) matches
  (18 - y, 18 - x).
- **The trigger** ran to its maximum at both sizes. The coldest pins' fast group needs far more histories than the rest, which is
  typical of a target on every bin.
- **k.** 0.687, well below 1: two open faces leak a lot from one assembly.

## Running it with AutoMC

1. **Build:** `OpenMCModelBuildTool` with `build.py`.
2. **Plot:** `OpenMCGeometryPlotTool` (a 22 cm view).
3. **Run:** `OpenMCRunStartTool`, approved by the user. With a trigger the run may take more batches than `batches`.
4. **Read:** `python post.py <run folder> --plot pins.png`.

## What it teaches

- DataFrames from tallies: MultiIndex columns, groupby, unstack.
- Triggers.
- `DistribcellFilter`.
- Symmetry as a check.
- Peaking factors.
