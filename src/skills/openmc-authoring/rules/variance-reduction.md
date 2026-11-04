# Variance reduction

Derived from: the OpenMC 0.15.3 user's guide, variance reduction page (MIT). Written for AutoMC; class arguments and method names
checked in the installed package (2026-11-04).

1. **Use it when an analog run cannot reach the answer:** a detector behind a thick shield, a dose far from the source, a deep
   penetration. It changes the run's statistics, not its physics: the mean must stay the same within its error.
2. **Weight windows on a mesh** are the main tool. A particle above its window splits, one below it plays roulette. Make the windows
   from a first, analog estimate of the flux:
   - **with openmc.lib** (a script run): `openmc.lib.WeightWindows.from_tally` on a mesh flux tally, an analog run, then
     `update_magic` and a second run with `openmc.lib.settings.weight_windows_on = True` (`working_examples/shielded-room-weight-windows`);
   - **inside one run:** an `openmc.WeightWindowGenerator(mesh, energy_bounds, particle_type, method)` in
     `openmc.Settings.weight_window_generators`, `method` `"magic"` or `"fw_cadis"` (the latter with the random-ray solver). Not yet
     run here; check its windows before trusting them.
   - **from a file:** an `openmc.WeightWindows` object (lower bounds per mesh element and energy group) in
     `openmc.Settings.weight_windows`, with `openmc.Settings.weight_windows_on`.
3. **The mesh** should cover the geometry with elements no larger than a few mean free paths of the shield, and the windows should
   be made from a run long enough that most elements have scores.
4. **Judge it fairly.** The figure of merit FOM = 1 / (R^2 T), with R the relative error and T the run time, compares runs of any
   length. Compare where the answer is wanted: windows aimed at a distant detector lower the FOM near the source, and that is not a
   failure (`working_examples/shielded-room-weight-windows` judges coverage and agreement, and says why).
5. **Check the means agree** between the analog and the weight-window runs within their combined error before quoting the second.
6. **Other tools:** `openmc.Settings.survival_biasing` (no absorption; weight is reduced instead) and `openmc.Settings.cutoff`
   (weight and energy cutoffs). Both are global; neither replaces weight windows for deep penetration.
