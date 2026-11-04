# Multigroup cross sections and multigroup mode

Derived from: the OpenMC 0.15.3 I/O format page for multigroup libraries and the Python API page for `openmc.mgxs` (MIT). Written for
AutoMC; methods and defaults checked in the installed package (2026-11-04).

## Making multigroup data with OpenMC (`openmc.mgxs`)

1. **One cross section at a time:** objects such as `openmc.mgxs.TotalXS`, `openmc.mgxs.AbsorptionXS`, `openmc.mgxs.ScatterXS` and
   `openmc.mgxs.FissionXS`, each with a domain and `openmc.mgxs.EnergyGroups` (edges in eV, or a structure's name such as
   `"CASMO-2"`); their tallies go in the model, and after the run each is loaded from the statepoint (`working_examples/mgxs-intro`).
   Totals obey their sum rules (total = absorption + scattering), a check worth printing.
2. **Many at once:** `openmc.mgxs.Library(geometry)` with `energy_groups`, `mgxs_types` (for example `"total"`, `"absorption"`,
   `"nu-fission"`, `"chi"`, `"nu-scatter matrix"`, `"transport"`), `domain_type` (`"material"`, `"cell"`, `"universe"`, `"mesh"`
   or `"distribcell"`) and `domains`; then `build_library()` and `add_to_tallies_file(tallies)`. After the run,
   `load_from_statepoint(statepoint)`, and `get_mgxs(domain, type)` for one (`working_examples/mgxs-library-mg-mode`).
3. **Scattering:** the library's defaults are `correction = "P0"` (a transport correction), `scatter_format = "legendre"` and
   `legendre_order = 0`. Raise the order or use angle-dependent data where anisotropy matters (`working_examples/mg-mode-advanced`:
   against continuous energy, an isotropic library was 1,335 pcm low and an angle-dependent one 233 pcm).
4. **By nuclide** (`by_nuclide = True`) gives microscopic data; condensing to fewer groups is exact only if done from the same
   tallies (`working_examples/mgxs-advanced`).
5. **Delayed-neutron data** (`working_examples/delayed-neutron-data`): a mixture's beta is its delayed nu-fission over its total
   nu-fission, not the sum of its nuclides' betas.

## Writing a library

6. **From OpenMC's tallies:** `create_mg_library(xs_type="macro", xsdata_names=[...])` then `export_to_hdf5("mgxs.h5")`
   (`working_examples/mgxs-library-mg-mode`).
7. **By hand, from a benchmark's tables:** an `openmc.MGXSLibrary(groups)` with one `openmc.XSdata(name, groups)` per material,
   filled with `set_total`, `set_absorption`, `set_scatter_matrix` (indexed incoming group, outgoing group, order), `set_nu_fission`
   or `set_fission`, `set_chi`; `order` must be set for the scattering. Check every row's balance (absorption + scattering =
   total) before using it: the C5G7 example's balances hold to 3.4e-6 (`working_examples/mg-mode-intro`).

## Running in multigroup mode

8. **Settings:** `openmc.Settings.energy_mode = "multi-group"`; the library is `openmc.Materials.cross_sections`, named by a path
   relative to the case (the build tool keeps it so); each material is `set_density("macro", 1.0)` with `add_macroscopic(name)`.
9. **A multigroup answer is only as good as its library:** compare it with continuous energy on the problem the library was made
   for, and say how far apart they are (`working_examples/mgxs-library-mg-mode`: +109 pcm, 2.0 sigma).
10. **`openmc.Model.convert_to_multigroup`** makes a multigroup model from a continuous-energy one in one step (used with the random-ray
    solver). Not yet run here.
