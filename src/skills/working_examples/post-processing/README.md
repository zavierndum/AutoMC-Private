# Reading a statepoint: tallies as arrays and the source bank

Derived from: the OpenMC notebook "Post Processing" (openmc-notebooks, tag v0.15.0; its pin, compositions and mesh). Written for
AutoMC.

## The problem

Most analysis starts by opening the statepoint a run wrote. This example makes a model whose statepoint has something to show: the
1.6%-enriched pin that several notebooks use, mirrored on all six faces, with a 100 x 100 mesh tally of flux and fission across it.
`post.py` then goes through the usual steps.

## The model

| Part | Value |
|---|---|
| fuel | UO2 with U-235 3.7503e-4, U-238 2.2625e-2, O-16 4.6007e-2 (relative amounts), 10.31341 g/cm3, r = 0.39218 cm |
| cladding | Zr-90 at 6.55 g/cm3 to r = 0.45720 cm (the notebook's simplification of zircaloy) |
| water | H-1 4.9457e-2, O-16 2.4732e-2, B-10 8.0042e-6, 0.740582 g/cm3, bound hydrogen (the notebook had none) |
| cell | a 1.26 cm cube, mirrored on all six faces |
| tally | a 2-D `RegularMesh` of 100 x 100 over the cell; flux and fission |

## The reading, step by step

1. **Open:** `openmc.StatePoint(path)`, and read `sp.keff`, which carries its error.
2. **Find the tally:** `sp.get_tally(name=...)`.
3. **Read the values:** `tally.get_values(scores=["flux"])` returns an array shaped (filter bins, nuclides, scores). A mesh filter's
   bins run x fastest, so `.reshape(ny, nx)` gives the map. `value="std_dev"` gives the standard deviations.
4. **Check the errors:** look at the relative error, `std_dev / mean`, before trusting a map.
5. **Look at the source bank:** `sp.source` holds the last batch's fission sites, a structured array with fields `r` (x, y, z), `u`,
   `E` and `wgt`. Plot the positions and histogram the energies.

## Results (OpenMC 0.15.3, ENDF/B-VIII.0)

| Quantity | quick (2,000 x 30 active, 9 s) | reference (20,000 x 100 active, 104 s) |
|---|---|---|
| k-infinity | 1.04109 +/- 0.00344 | 1.03773 +/- 0.00069 |
| median relative error of the flux map | 0.84% | 0.15% |
| mesh cells with fission / cells overlapping the pellet | 3,172 / 3,172 | 3,172 / 3,172 |
| source sites inside the pellet | all | all |
| mean birth energy | 1.95 MeV | 1.955 MeV |

The fission map is non-zero in exactly the mesh cells the pellet overlaps, counted geometrically in `post.py`. The birth energies
average close to 2 MeV, the mean of U-235's fission spectrum.

## Running it with AutoMC

1. **Build:** `OpenMCModelBuildTool` with `build.py` (OPTIONS: mesh, size).
2. **Run:** `OpenMCRunStartTool`, approved by the user.
3. **Read:** `RunResultReaderTool` for k-eff and the tallies' health; then `python post.py <run folder> --plot maps.png` through
   `PythonRunTool`.

## What it teaches

- StatePoint, `get_tally` and `get_values`.
- How a mesh tally's bins are ordered.
- Relative errors.
- The source bank's fields.
