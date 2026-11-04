# Volumes

Derived from: the OpenMC 0.15.3 user's guide, stochastic volume calculation page (MIT). Written for AutoMC; class and method names
checked in the installed package (2026-11-04). No working example yet.

1. **Volumes are needed** for depletion (`openmc.Material.volume`), for any per-volume result (flux per cm3, dose in a cell,
   `rules/normalization.md`) and for densities from masses.
2. **Compute them exactly when the shape is simple:** a cylinder is pi r^2 h, a lattice of pins is the number of pins times one. Write
   the formula in `build.py` next to the shape it measures, so that a change of dimension changes both.
3. **A stochastic estimate** for anything else: an `openmc.VolumeCalculation(domains, samples, lower_left, upper_right)` (cells,
   materials or universes, and a box that encloses them) in `openmc.Settings.volume_calculations`. Its error falls as one over the
   square root of the samples; `openmc.VolumeCalculation.set_trigger` stops it at a chosen error.
4. **It is an OpenMC run** (volume mode, `openmc.Model.calculate_volumes`), not a transport run, but it takes time and runs OpenMC:
   do it as Python the user approves, not inside `build.py`, and write the volumes it finds into the build script with their error.
5. **Check the box:** points outside every domain are wasted, and a domain that pokes out of the box is cut short without warning.
