# Unstructured mesh tallies: flux and heating on a tetrahedral mesh

Derived from: the OpenMC notebook "Unstructured Mesh: Introduction" (openmc-notebooks, tag v0.15.0; the assembly, its 20 cm cut, the
vacuum sides, the pin mesh and the scores). Written for AutoMC. The cylinder tallies that check the mesh are added here.

## The problem

A regular mesh is a grid of boxes. To tally on curved or irregular regions, such as a pin's fuel, a component's own shape, or the
cells another code computes on, OpenMC can score on an unstructured mesh: a mesh of tetrahedra from a mesh generator, here read
through MOAB.

Each element gets its own score, and the results go to a file a viewer such as ParaView or VisIt can show.

This example tallies flux and heating on a tet mesh of four pin channels of a PWR assembly. It checks the mesh's answer against exact
cylinder tallies of the same channels.

## The model

| Part | Value |
|---|---|
| assembly | `openmc.examples.pwr_assembly()`, a 17 x 17 PWR assembly, cut to z from -10 to +10 cm; vacuum on every side, as the notebook does for illustration |
| tet mesh | `mesh/pin_mesh_moab.h5m` (data cache): 340,144 tetrahedra, 38.33 cm3, filling the inner cylinder (r = 0.39218 cm) of four positions along a diagonal: the central guide tube, two fuel pins and another guide tube |
| tallies | flux and heating on every tet (`MeshFilter` of an `UnstructuredMesh`, track-length estimator); the same scores on an exact cylinder over each channel (`CylindricalMesh` with its `origin` at the channel) |

Fetch the mesh once with `python -m automc data fetch unstructured-mesh-tallies`. The notebook also offers it for libMesh (an Exodus
file); this OpenMC build has no libMesh, so only MOAB is used.

## How it runs

- **`build.py`** builds the cut assembly, copies the mesh into the case, and adds the tet tally and the four cylinder tallies.
- **An ordinary run** (`OpenMCRunStartTool`).
- **`post.py`**:
  - reads the mesh back from the statepoint: its elements' volumes and centroids;
  - sorts the elements into the channels by their centroids;
  - compares each channel's flux and heating per cm3 with its cylinder;
  - writes the results to `pin_channels.vtk`.
- **VTK.** OpenMC's `write_data_to_vtk` needs the `vtk` package, which this environment lacks. `post.py` then writes the legacy VTK
  format itself: points, tetrahedra and cell data, which ParaView and VisIt read.

## Results (OpenMC 0.15.3, ENDF/B-VIII.0)

| Size | Histories | k-eff | Elements scored | Worst flux difference, tets against cylinder | Time here |
|---|---|---|---|---|---|
| quick | 2,000 x (50 - 20) | 0.22834 ± 0.00185 | 52% | 0.05% | 37 s |
| reference | 20,000 x (150 - 20) | 0.23198 ± 0.00033 | 100% | 0.02% | 91 s |

At the reference size:

| Channel | Tetrahedra | Volume against the exact cylinder | Flux per cm3, tets / cylinder | Heating, tets / cylinder |
|---|---|---|---|---|
| guide tube (centre) | 85,480 | -0.84% | 1.0000 | 1.0000 |
| fuel pin | 84,672 | -0.84% | 1.0002 | 0.9996 |
| fuel pin | 84,520 | -0.84% | 1.0002 | 0.9997 |
| guide tube | 85,472 | -0.84% | 1.0000 | 1.0000 |

- **The tets give the same flux and heating per cm3 as the exact cylinders,** to 0.02% and 0.04%.
  - The faceted mesh is 0.84% smaller than the circles it approximates, but per unit volume that hardly matters.
  - The two tallies score the same tracks, so their difference is much smaller than either one's statistical error.
- **The fuel heats about 250 times more than the guide tubes' water.** Fission deposits its energy in the fuel; the water gets only
  what neutrons and photons bring it.
- **Coverage grows with histories:** 52% of the tets scored at the quick size, all of them at the reference size. A fine mesh needs
  many histories before every element has a result.
- **k is low (0.23) on purpose.** The assembly is alone in vacuum and only 20 cm tall, so most neutrons leak. The notebook had 0.21117
  ± 0.00443 with older nuclear data, and with its z planes left without a boundary condition.

## Running it with AutoMC

1. `python -m automc data fetch unstructured-mesh-tallies`, once (the user runs it; it needs the network).
2. **Build:** `OpenMCModelBuildTool` with `build.py`.
3. **Check:** `OpenMCModelCheckTool` (it confirms the mesh file is in the case).
4. **Run:** `OpenMCRunStartTool`, approved by the user. A large unstructured mesh makes the run slower than the same model without it.
5. **Read:** `python post.py <run folder>` through `PythonRunTool`. Open `pin_channels.vtk` in ParaView or VisIt.

## What it teaches

- `openmc.UnstructuredMesh` (MOAB) in a `MeshFilter`; the track-length estimator on tetrahedra.
- The mesh's elements read back from the statepoint: volumes, centroids, vertices and connectivity.
- Checking a mesh tally against an independent tally of the same region.
- Writing mesh data for a viewer, with or without the `vtk` package.
