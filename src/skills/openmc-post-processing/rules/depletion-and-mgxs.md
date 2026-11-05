# Depletion results and multigroup cross sections

Derived from: the OpenMC 0.15.3 user's guide, depletion and multigroup cross section pages (MIT), with the signatures read in
`openmc/deplete/results.py`, `openmc/deplete/abc.py`, `openmc/deplete/stepresult.py` and `openmc/mgxs/mgxs.py`. Written for AutoMC.

## Depletion

1. **Read the results file:** `openmc.deplete.Results("depletion_results.h5")` in the run folder (`working_examples/depletion`).
   - `get_keff(time_units="d")` gives the times and an array of rows [k, its error], one per time point;
   - `get_atoms(mat, nuclide, nuc_units=...)` gives "atoms" (the default), "atom/b-cm" or "atom/cm3"; `get_mass` gives grams;
   - `get_activity` and `get_decay_heat` give activity and decay heat; `get_reaction_rate` gives reactions per second in the
     material (the rate per atom times the atoms);
   - `export_to_materials(index)` writes the compositions at one time point for a later model.

   `mat` is a `openmc.Material` or its id as a string.
2. **Give the time units every time.** `get_keff`, `get_atoms` and `get_mass` return seconds unless told; `get_times` returns days.
3. **Reaction rates are saved only on request.** Unless the integration ran with `integrate(write_rates=True)`, the file holds no
   rates, and `get_reaction_rate` returns zeros without an error. Check the run's `run.py` before quoting a rate.
4. **Each time point's k is its own transport run,** with its own error. A change of k between points smaller than about twice their
   combined error is not a trend.
5. **Check the run against its own input:** with a power given, the fission power from the saved rates equals it at every step, and a
   nuclide destroyed only by fission falls by what fissioned (`working_examples/depletion`). A failed check points at the chain, the
   power or the volumes.

## Multigroup cross sections

6. **Rebuild, then load:** make the same MGXS objects as in `build.py` (same domains, groups, types), then
   `xs.load_from_statepoint(sp)` with the run's statepoint, then `xs.get_xs(...)` (`working_examples/mgxs-intro`,
   `working_examples/mgxs-advanced`); a whole `openmc.mgxs.Library` loads the same way (the `run.py` of
   `working_examples/mgxs-library-mg-mode`).
7. **Group order:** `get_xs` returns groups in increasing group number by default, which is decreasing energy: group 1 is the fastest.
   `order_groups="decreasing"` reverses it.
8. **Their errors are statistical only.** Cross sections from a short run carry its noise into every multigroup calculation that
   uses them; quote the relative errors with the library, and say what the spectrum was (`openmc-authoring/rules/multigroup.md`).

## Other results

9. **A criticality search** returns its answer and every guess with its k (`working_examples/criticality-search`;
   `openmc-authoring/rules/c-api.md`). **Volume calculations:** `openmc-authoring/rules/volume.md`.
