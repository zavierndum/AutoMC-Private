# Tally arrays: shapes, filters and meshes

Derived from: the OpenMC 0.15.3 user's guide, processing and tallies pages (MIT), with the array orders read in `openmc/tallies.py`
(`filter_strides`, `get_reshaped_data`), `openmc/filter.py` (`MeshFilter`) and `openmc/mesh.py` (`volumes`, `write_data_to_vtk`).
Written for AutoMC.

## The arrays

1. **Three axes.** `tally.mean` and `tally.std_dev` have the shape (filter bins, nuclides, scores). With several filters their bins
   share the first axis, and the last filter's bins vary fastest: for a cell filter then an energy filter, the energies of the first
   cell come first.
2. **Pick parts by name:** `tally.get_values(scores=[...], filters=[...], filter_bins=[...], nuclides=[...], value=...)`, where value
   is `"mean"` (the default), `"std_dev"`, `"rel_err"`, `"sum"` or `"sum_sq"`. It keeps the three axes; reshape or ravel after.
3. **One axis per filter:** `tally.get_reshaped_data()` splits the first axis into one per filter; with `expand_dims=True` a mesh
   filter becomes one axis per mesh dimension.
4. **A zero is not a precise zero.** Where a bin's mean is 0, its standard deviation is set to 0: the bin was never scored, and
   0 +/- 0 means unsampled (`openmc-validation/rules/statistics.md` 2).

## Meshes

5. **A mesh filter's bins run x fastest, then y, then z.** So `get_values(...).reshape(nz, ny, nx)` (or `(ny, nx)` for a 2-D mesh)
   gives `[z, y, x]`: rows in y and columns in x, as `imshow(..., origin="lower")` draws them (`working_examples/post-processing`).
6. **`get_reshaped_data(expand_dims=True)` gives the other order,** `[x, y, z, nuclide, score]`. `mesh.volumes` is in that order too
   (`[x, y, z]`; `[r, phi, z]` for a cylindrical mesh). Divide each in its own order: an array reshaped to `[z, y, x]` needs
   `mesh.volumes.T`. With unequal cells the wrong order divides by the wrong volumes and raises no error when the dimensions happen
   to match.
7. **Unstructured meshes** get their element volumes and centroids from the statepoint (`mesh.volumes`, `mesh.centroids`), and the
   bins follow the element order (`working_examples/unstructured-mesh-tallies`, `working_examples/cad-mesh-visualisation`).
8. **VTK files:** `mesh.write_data_to_vtk(path, datasets={name: array})` divides every dataset by the element volumes unless
   `volume_normalization=False`. Give it raw tally values, or values already per cm3 with the flag off, never per cm3 with the flag
   on. It needs the `vtk` package; without it, the examples write the legacy VTK format themselves
   (`working_examples/unstructured-mesh-tallies`).

## DataFrames, slices and sums

9. **`tally.get_pandas_dataframe()`** gives one row per combination of bins, with `mean` and `std. dev.` columns. A mesh filter's
   columns are a MultiIndex, ("mesh 1", "x") and so on, numbered from 1; flatten them before grouping
   (`working_examples/pandas-dataframes`).
10. **Summing bins** adds the means, and the variances (the squares of the standard deviations), as the pandas example does;
    `tally.summation(filter_type=..., filter_bins=[...], remove_filter=True)` and `tally.get_slice(...)` do the same on the tally
    (`working_examples/tally-arithmetic`, the net current through a mesh cell's faces). Both treat the bins as independent:
    `derived-quantities.md` 2.
11. **Large tallies:** about 16 bytes per bin in the statepoint. Read the parts you need with `get_values` rather than whole
    DataFrames when the tally has millions of bins.
