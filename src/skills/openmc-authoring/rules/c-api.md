# The C API (`openmc.lib`) and searches

Derived from: the OpenMC 0.15.3 Python API pages for `openmc.lib` and `openmc.search_for_keff` (MIT). Written for AutoMC; signatures
checked in the installed package (2026-11-04).

## When to use it

1. **`openmc.lib` runs OpenMC inside Python:** change a material or a temperature between batches or between runs, find cells, step
   batches by hand, or make weight windows. Anything that uses it runs transport, so it is a script run (`run.py`, decision A13),
   never part of `build.py` (`working_examples/c-api`).

## How

2. **Start and stop:** `openmc.lib.init()` reads `model.xml` in the current folder (threads from OMP_NUM_THREADS);
   `openmc.lib.finalize()` ends it. `openmc.lib.run_in_memory` does both around a `with` block.
3. **A whole run** is `openmc.lib.run()`; **by hand** it is `openmc.lib.simulation_init()`, `openmc.lib.next_batch()` as often as
   wanted, `openmc.lib.simulation_finalize()`. `openmc.lib.keff()` gives the mean and its standard deviation;
   `openmc.lib.reset()` sets tallies and k back to zero for the next run.
4. **Changing the model:** `openmc.lib.materials[id].set_density(value, units=...)` (the default units are atom/b-cm; give
   `"g/cm3"` for a mass density), `openmc.lib.materials[id].set_densities(nuclides, densities)`,
   `openmc.lib.cells[id].set_temperature(T)`. Read a value back (`get_density`, `get_temperature`) to be sure it took.
5. **Lookups:** `openmc.lib.find_cell(xyz)` gives the cell and its instance at a point; `openmc.lib.tallies[id]` gives a tally's
   current results; `openmc.lib.statepoint_write` saves a statepoint.

## One state per result

6. **Change the model only between runs.** A k or a tally gathered over batches before and after a change mixes two states and
   belongs to neither. The C API notebook printed such a k; `working_examples/c-api` finishes one state, resets, changes, and runs
   again.

## Criticality searches

7. **`openmc.search_for_keff(builder, bracket=[low, high], tol=..., bracketed_method="bisect")`** builds and runs a model for each
   guess of a parameter until k reaches the target (1.0 by default). It returns the critical value, the guesses and their k
   (`working_examples/criticality-search`: boron for a PWR pin).
8. **Bracket the answer** so that k is above the target at one end and below at the other, and set the tolerance no finer than k's
   statistical error allows; bisection is robust against that noise. Report the critical value with the k and error of its last run,
   and say that the tolerance limits it.
