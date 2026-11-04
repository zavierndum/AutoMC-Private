# Settings

Derived from: the OpenMC 0.15.3 user's guide, settings page (MIT). Written for AutoMC; defaults below were read from the installed
package and OpenMC's source (`src/settings.cpp`) (2026-11-04).

## The mode and the size

1. **`openmc.Settings.run_mode`:** `"eigenvalue"` (k and the fission source) or `"fixed source"` (a given source, as in shielding
   and detectors). The others (`"plot"`, `"volume"`, `"particle restart"`) are for the tools.
2. **Size = `particles` per batch x `batches`.** In eigenvalue mode the first `inactive` batches only converge the fission source
   and are not tallied; a fixed-source run needs none.
3. **Two sizes in every build script,** as the working examples have them: `quick` (seconds to a few minutes: it shows the model runs
   and gives plausible numbers) and `reference` (the numbers to quote). The user approves each run.
4. **Inactive batches** must outlast the source's convergence. Put an entropy mesh over the fissile region
   (`openmc.Settings.entropy_mesh`, a `openmc.RegularMesh`) and look at the Shannon entropy before trusting tallies
   (`working_examples/jezebel`; `openmc-validation`).
5. **`openmc.Settings.seed`** makes a run repeatable; the examples set one.

## Stopping on precision

6. **Triggers** stop a run when its errors are small enough: `openmc.Settings.keff_trigger` (for example `{"type": "std_dev",
   "threshold": 0.0005}`), and an `openmc.Trigger` on a tally (`"rel_err"`, `"std_dev"` or `"variance"`), with
   `openmc.Settings.trigger_active` and `openmc.Settings.trigger_max_batches` (`working_examples/pandas-dataframes`,
   `working_examples/mgxs-advanced`).

## Physics switches

7. **Temperature:** `openmc.Settings.temperature` takes `default`, `method` (`"nearest"` or `"interpolation"`), `tolerance` (10 K
   by default), `range` and `multipole`.
8. **Photons:** `openmc.Settings.photon_transport = True` (`rules/photons.md`).
9. **Multigroup:** `openmc.Settings.energy_mode = "multi-group"` (`rules/multigroup.md`).
10. **Variance reduction:** weight windows, survival biasing and cutoffs (`rules/variance-reduction.md`).

## Limits that stop a run

11. **Lost particles:** the run stops when both limits are reached: `openmc.Settings.max_lost_particles` (10) lost, and
    `openmc.Settings.rel_max_lost_particles` (1e-6) of the particles run. Do not raise the limits to make a run finish: a lost
    particle is a geometry error.
12. **Source rejection:** a source with constraints stops when fewer than `openmc.Settings.source_rejection_fraction` (0.05) of the
    sites it samples are accepted (`rules/sources.md`).

## Output

13. **Statepoints** are written at the end, and at chosen batches through `openmc.Settings.statepoint`; AutoMC's job tools also write
    them to pause and checkpoint runs. `openmc.Settings.output` can turn off `tallies.out`, which can be large.
