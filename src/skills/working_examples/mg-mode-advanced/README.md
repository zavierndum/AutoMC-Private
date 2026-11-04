# Multigroup mode, advanced: angle-dependent data for a rodded BWR assembly

Derived from: the OpenMC notebook "Multigroup Mode Part III: Advanced Feature Showcase" (openmc-notebooks, tag v0.15.0; the assembly,
its materials and blade, the mesh, the histogram scattering and the angle-dependent library). Written for AutoMC.

## The problem

Group cross sections are averages over energy, and usually over the direction of travel too. That average is fine where the flux is
nearly isotropic. Next to a strong absorber it is not, because the neutrons streaming into the absorber and those coming out of it
see different averages. This example puts a cruciform control blade beside a BWR assembly, makes two-group libraries on a coarse mesh,
once averaged over direction and once in eight azimuthal bins, and measures each against the continuous-energy answer.

## The model

| Part | Value |
|---|---|
| assembly | 8 x 8 pins, 3.0% enriched UO2 with gadolinia-bearing pins, zircaloy cladding, boiling water at 0.6 g/cm3 |
| blade | boron carbide wings (70% of theoretical density) along the top and the left side, meeting at a steel centre |
| boundaries | mirrored on every side, so the blade is shared with the neighbouring assemblies |
| libraries | two groups (0.625 eV) on a 10 x 10 mesh: `isotropic` (one azimuthal bin) and `angle-dependent` (eight); scattering as 11-bin histograms in angle, no correction |

## How it runs

- **`build.py`** builds the CE model with the tallies of both libraries (`build_with_libraries()`), for plotting and checking.
- **`run.py`** is a **script run** of three transport runs:
  1. CE in `ce/`;
  2. both libraries loaded from it;
  3. each written as an MG file (`create_mg_mode`) and run on the mesh-homogenised geometry it describes, in `mg-isotropic/` and
     `mg-angle-dependent/`.
- **`post.py`** reads `comparison.json`: each library's eigenvalue against the CE one, in pcm and in standard deviations.

## Results (OpenMC 0.15.3, ENDF/B-VIII.0)

| Size | Histories per run | CE | Isotropic MG | Angle-dependent MG | Time here |
|---|---|---|---|---|---|
| quick | 2,000 x (80 - 20) | 0.83771 ± 0.00262 | 0.82449, -1,322 pcm (-3.8 σ) | 0.83455, -316 pcm (-0.9 σ) | 10 s |
| reference | 10,000 x (320 - 20) | 0.83769 ± 0.00048 | 0.82434, -1,335 pcm (-21.8 σ) | 0.83536, -233 pcm (-3.7 σ) | 117 s |

- **The isotropic library is 1,335 pcm low.** Near the blade the flux is far from isotropic, and group data averaged over direction
  miss that. Here they make the assembly 1,335 pcm less reactive than the continuous-energy run (differences in k, x 10⁵).
- **Eight azimuthal bins remove most of that error**, leaving -233 pcm. That is still 3.7 standard deviations at the reference size,
  so some bias remains with this mesh and these run sizes. The notebook, with longer runs, found -62 pcm; the source of the difference
  here has not been checked.
- **The notebook's values** (older nuclear data, longer runs): CE 0.83686, isotropic 0.82369 (-1,317 pcm), angle-dependent 0.83624.

## Running it with AutoMC

1. **Build:** `OpenMCModelBuildTool` with `build.py`.
2. **Plot and check:** `OpenMCGeometryPlotTool` (the blade and the gadolinia pins show in the xy plot), `OpenMCModelCheckTool`.
3. **Run:** `OpenMCRunStartTool` with `case` and `script` (the text of `run.py`), approved by the user. It can be stopped or
   cancelled, not paused.
4. **Read:** `python post.py <run folder>` through `PythonRunTool`.

## What it teaches

- When direction-averaged group data fail: strong absorbers and steep flux gradients.
- A library on a mesh domain, histogram scattering and angle-dependent (azimuthal) data.
- Measuring each library against the CE run it came from.
- A script run that chains three transport runs.
