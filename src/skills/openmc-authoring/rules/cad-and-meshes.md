# CAD geometry (DAGMC) and unstructured meshes

Derived from: the OpenMC 0.15.3 user's guide, geometry page (CAD-based geometry) and tallies page (unstructured meshes) (MIT). Written
for AutoMC from what the CAD and mesh examples met; arguments checked in the installed package (2026-11-04).

## The files

1. **CAD models are DAGMC `.h5m` files** (triangulated surfaces). AutoMC ships none; `python -m automc data fetch EXAMPLE` downloads
   an example's into the data cache, and the build script copies it into the case. Name files **relative to the case**: OpenMC stores
   absolute paths unless `openmc.config["resolve_paths"]` is False, and the build tool keeps them relative.
2. **A CAD model must be imprinted and merged:** where two volumes touch they share one surface. A file exported without it holds
   two copies of each touching face; DAGMC's implicit complement (the space outside every volume) then claims points inside the
   volumes, and the model check marks overlaps although no volumes cross. `check_watertight` does not show this; the model check
   does: it reads each CAD file and names the touching volumes that hold two copies of a face, with their area. The fix is in the
   CAD export, not in `build.py`: tell the user (`working_examples/cad-geometry`, "Using the tokamak").

## In a model

3. **`openmc.DAGMCUniverse(filename)`** is a universe like any other. Alone it is the whole geometry and needs a **graveyard** volume
   in the file to end particles; inside a CSG cell (with vacuum boundaries outside it) it needs none.
   `openmc.DAGMCUniverse.bounding_region` and `openmc.DAGMCUniverse.bounded_universe` make that cell.
4. **CAD inside CSG needs `auto_geom_ids = True`,** or the file's cell and surface ids clash with the CSG ones
   (`working_examples/cad-geometry`, the mirrored tokamak).
5. **Materials are matched by name:** the file's material tags must equal `openmc.Material` names (a numeric tag matches a
   material's id). `openmc.DAGMCUniverse.material_names` lists the tags; define one material for each (the CAD pin's water is
   tagged `41`).
6. **A sector needs reflecting planes** at its cuts, or it leaks through them (`rules/geometry.md`).
7. **Compare a CAD model with a CSG twin** when one is possible: the CAD pin agrees with its exact CSG version within 1.4 sigma
   (`working_examples/cad-geometry`).

## Unstructured meshes

8. **`openmc.UnstructuredMesh(filename, library="moab")`** for a tetrahedral mesh in a `.h5m` file. This OpenMC build has MOAB, not
   libMesh. Put it in an `openmc.MeshFilter`; results are per element and per source particle, so divide by
   `openmc.UnstructuredMesh.volumes` for densities (`working_examples/unstructured-mesh-tallies`: within 0.02% of an exact
   cylinder tally).
9. **Pictures of mesh results:** `write_data_to_vtk` needs the `vtk` package, which this environment lacks; the mesh examples write
   the legacy VTK text format themselves (`working_examples/cad-mesh-visualisation`).
