# Statepoints and summaries

Derived from: the OpenMC 0.15.3 user's guide, processing page (MIT), with the behaviour read in `openmc/statepoint.py` and
`openmc/tallies.py`. Written for AutoMC.

RunResultReaderTool reads the usual results of a run (k-eff, k by batch, tallies, lost particles). This file is for a `post.py` or a
Python run that needs more.

## The file

1. **Take the last statepoint.** A run writes `statepoint.<batch>.h5` at the batches its settings name and at its end; the last holds
   the whole run. Sort the files by their batch number, not by name: as text, `statepoint.100.h5` comes before `statepoint.20.h5`
   (the `statepoint()` helper of `working_examples/post-processing`).
2. **Open it in a with block,** `with openmc.StatePoint(path) as sp:`, and read what you need inside it. An open statepoint holds its
   file and its summary, and the next run in that folder cannot write them.
3. **The summary comes with it.** `summary.h5`, written beside the statepoint, holds the geometry and materials of the run. A
   statepoint opened in its own folder links it, so `sp.summary.geometry` gives the cell names for the ids in a cell filter
   (`working_examples/flux-spectrum`). Copied alone to another folder, the statepoint has no summary.

## What it holds

4. **k:** `sp.keff` is the combined estimate over the active batches, an `uncertainties` value (`.nominal_value`, `.std_dev`).
   `sp.global_tallies` gives the collision, absorption and track-length estimates and the leakage fraction; `sp.k_generation` gives k
   of each generation.
5. **Batches:** `sp.n_batches`, `sp.n_inactive` and `sp.n_realizations` (the active batches the means are over). The error of a tally
   is the standard deviation of the mean over those realizations.
6. **Convergence:** `sp.entropy` holds the Shannon entropy of each generation when the settings had an entropy mesh
   (`working_examples/jezebel`; `openmc-validation/rules/convergence.md`).
7. **The source bank:** `sp.source`, when `sp.source_present`, is a NumPy structured array of sites with position `r`, direction
   `u`, energy `E` and weight `wgt` (`working_examples/post-processing` checks that every fission site lies in the fuel).
8. **Time:** `sp.runtime` holds the times the run spent, for a figure of merit (`openmc-validation/rules/statistics.md` 4).

## Finding a tally

9. **By name.** `sp.get_tally(name=...)` finds the one tally meant. Searching by scores, filters or nuclides returns the first tally
   that has them among its own, which is a different tally when two share them; with nothing found it raises LookupError. Give every
   tally a name in `build.py` for this reason.
10. **By its filter.** `tally.find_filter(openmc.MeshFilter)` returns the tally's filter of that type: the mesh, the energy edges
    (`.values`) or the cell ids (`.bins`) that the arrays are indexed by.

## Volumes

11. **A cell's volume is not in the statepoint.** Use the volume written in `build.py`, or a volume calculation
    (`openmc-authoring/rules/volume.md`); a mesh gives its own (`tally-arrays.md`). Flux per volume:
    `openmc-authoring/rules/normalization.md` 2.
