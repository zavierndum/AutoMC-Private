# Tallies

Derived from: the OpenMC 0.15.3 user's guide, tallies page (MIT). Written for AutoMC; API facts checked in the installed openmc
package and the tally rules in OpenMC's source (`src/tallies/tally.cpp`) (2026-11-04).

## A tally

1. **`openmc.Tally(name=...)` with `filters`, `scores` and, if wanted, `nuclides`,** collected in `openmc.Tallies`. Name every tally:
   results are read by name.
2. **Filters say where and what kind:** `openmc.CellFilter`, `openmc.MaterialFilter`, `openmc.UniverseFilter`,
   `openmc.DistribcellFilter` (each instance of a repeated cell), `openmc.MeshFilter`, `openmc.SurfaceFilter`,
   `openmc.MeshSurfaceFilter`, `openmc.EnergyFilter` (incoming energy, eV), `openmc.EnergyoutFilter`, `openmc.ParticleFilter`,
   `openmc.EnergyFunctionFilter` (a response such as dose, `rules/dose.md`), `openmc.LegendreFilter` and `openmc.ZernikeFilter`
   (expansions, `working_examples/expansion-filters`), `openmc.TimeFilter`, `openmc.DelayedGroupFilter`.
3. **Scores say what is counted:** `"flux"`, `"total"`, `"absorption"`, `"fission"`, `"nu-fission"`, `"scatter"`, `"(n,gamma)"`
   and other reactions by name, `"heating"`, `"kappa-fission"`, `"fission-q-recoverable"`, `"current"`, `"events"`,
   `"pulse-height"` (`rules/photons.md`).
4. **`nuclides`** splits a score by nuclide; without them a score is for the whole material. With nuclides a score is still a rate in
   that material (it includes the nuclide's density): a microscopic cross section is the rate divided by the flux and by the
   density (`working_examples/tally-arithmetic`).

## Energy groups and meshes

5. **Group structures by name:** `openmc.EnergyFilter.from_group_structure("CASMO-70")`; the names are the keys of
   `openmc.mgxs.GROUP_STRUCTURES` (CASMO-2 to -70, SHEM-361, VITAMIN-J-175, CCFE-709 and others). Equal-lethargy bins are
   `numpy.logspace` edges (`working_examples/flux-spectrum`).
6. **Meshes:** `openmc.RegularMesh` (`dimension`, `lower_left`, `upper_right` or `width`; `openmc.RegularMesh.from_domain` fits one
   to a cell or the geometry), `openmc.RectilinearMesh`, `openmc.CylindricalMesh` (`r_grid`, `z_grid`, `phi_grid`),
   `openmc.SphericalMesh` and `openmc.UnstructuredMesh` (`rules/cad-and-meshes.md`). A mesh tally is per element, not per cm3
   (`rules/normalization.md`).

## Rules OpenMC enforces

7. **`"current"` needs a surface filter** (`openmc.SurfaceFilter`, or `openmc.MeshSurfaceFilter` for a mesh's faces) and must be
   the tally's only score; surface and mesh-surface currents cannot share a tally. Net leakage out of a box is current out minus
   current in (`working_examples/tally-arithmetic`).
8. **Estimators:** `"tracklength"`, `"collision"` or `"analog"`; OpenMC chooses one per score unless told. An energy-out or Legendre
   filter forces analog; expansion filters score at collisions, so compare them with a collision-estimated tally
   (`working_examples/expansion-filters`).
9. **Triggers** on a tally (`openmc.Trigger`) stop the run when its error is small enough (`rules/settings.md`).

## Reading

10. Results are per source particle, with the mean and the standard deviation of the mean over active batches. Read them with
    `openmc.StatePoint`, `openmc.Tally.get_values`, `openmc.Tally.get_pandas_dataframe`, and combine them with tally arithmetic
    (`openmc-post-processing`; `working_examples/post-processing`, `working_examples/pandas-dataframes`).
