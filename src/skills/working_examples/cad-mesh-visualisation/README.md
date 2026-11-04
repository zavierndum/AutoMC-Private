# A CAD model with its own mesh: flux in a steel manifold, viewed as VTK and a point cloud

Derived from: the OpenMC notebook "Unstructured Mesh: Tallies with CAD and Point Cloud Visualization" (openmc-notebooks, tag v0.15.0;
the manifold, its materials, the source, the mesh tally and its energy groups, the point cloud). Written for AutoMC.

## The problem

A steel piping manifold would be very hard to write as CSG. It comes as a CAD model (DAGMC), and the flux in it is wanted on a mesh that
follows its own shape: a tetrahedral mesh of the manifold, made alongside the CAD model.

The answer is a set of values on 84,822 elements. Two things follow:
- it has to be looked at, with a viewer or a point cloud;
- it has to be checked, here against what the physics says about how flux changes away from a source.

## The model

| Part | Value |
|---|---|
| geometry | `cad/manifold.h5m` (data cache): a steel manifold, one pipe branching into several, in air; DAGMC volumes tagged `steel` and `air` |
| materials | steel (Fe, Ni, Mo, Si, Mn, P, S) at 8.0 g/cm3; air at 0.001205 g/cm3 |
| source | 5 MeV neutrons from a point at the mouth of the single pipe, (0, 0, 0) |
| tally | flux on `mesh/manifold_mesh.h5m` (84,822 tetrahedra, 0.60 m3, MOAB) in two energy groups: below and above 1 MeV; track-length estimator |

Fetch the files once with `python -m automc data fetch cad-mesh-visualisation` (108 MB, most of it the CAD model).

## How it runs

- **`build.py`** builds the model and copies the CAD and mesh files into the case.
- **An ordinary fixed-source run** (`OpenMCRunStartTool`).
- **`post.py`**:
  - reads each element's volume and centroid back from the statepoint;
  - turns the tally into flux per cm3 in each group;
  - checks two trends as rank correlations over the elements that scored: the flux above 1 MeV falls with distance from the source,
    and the share below 1 MeV rises with it;
  - writes `manifold_flux.vtk` (legacy VTK, written by `post.py` because this environment has no `vtk` package);
  - draws `manifold_point_cloud.png`, the element centroids coloured by flux.

## Results (OpenMC 0.15.3, ENDF/B-VIII.0)

| Size | Histories | Elements scored | Leakage | Fast flux against distance | Share below 1 MeV against distance | Time here |
|---|---|---|---|---|---|---|
| quick | 2,000 x 20 | 41% | 0.9724 | -0.81 | +0.29 | 25 s |
| reference | 10,000 x 100 | 86% | 0.9734 | -0.87 | +0.44 | 50 s |

By distance from the source, at the reference size:

| Distance | Elements | Mean flux, per cm2 per source neutron | Below 1 MeV |
|---|---|---|---|
| 25 to 50 cm | 23,015 | 1.8e-4 | 33% |
| 50 to 100 cm | 31,680 | 3.6e-5 | 48% |
| 100 to 250 cm | 18,435 | 5.4e-6 | 55% |

- **The flux falls about thirtyfold from the first half metre to the far branches.** Geometry spreads it, and the steel scatters and
  absorbs it.
- **It softens on the way:** a third of it is below 1 MeV near the source, more than half far away. Neutrons lose energy in collisions
  with the steel's iron and nickel.
- **Almost all neutrons leave (97%):** a manifold in air holds few of them. The notebook had 0.97200.
- **Coverage:** even at the reference size, 14% of the elements score nothing. A fixed source far from most of a large mesh needs many
  histories, or variance reduction, before every element has a result.
- **The point cloud** shows the manifold's shape from its centroids alone: the bright single pipe at the source, fading along the
  branches.

## Running it with AutoMC

1. `python -m automc data fetch cad-mesh-visualisation`, once (the user runs it; it needs the network).
2. **Build:** `OpenMCModelBuildTool` with `build.py`.
3. **Plot and check:** `OpenMCGeometryPlotTool`, `OpenMCModelCheckTool`.
4. **Run:** `OpenMCRunStartTool`, approved by the user.
5. **Read:** `python post.py <run folder>` through `PythonRunTool`. Then open `manifold_flux.vtk` in ParaView or VisIt, and show the
   user `manifold_point_cloud.png`.

## What it teaches

- A CAD geometry with a tally on a mesh of the same object.
- An energy filter next to a mesh filter, and the tally's shape (elements by groups).
- Element volumes and centroids from the statepoint: flux per cm3, distance from the source.
- Checking a spatial result against physics trends with rank correlations.
- Seeing a mesh result: a VTK file for a viewer, and a point cloud.
