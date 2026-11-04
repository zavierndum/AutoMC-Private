# Jezebel: a critical benchmark

Derived from: OpenMC's script example `examples/jezebel` (MIT licence) and the ICSBEP handbook's benchmark PU-MET-FAST-001. Written
for AutoMC.

## The problem

Jezebel was a bare sphere of delta-phase plutonium-gallium alloy assembled at Los Alamos in the 1950s and brought to delayed critical.
The ICSBEP benchmark reduces it to a homogeneous sphere of radius 6.3849 cm with given atom densities:

| Nuclide | atoms per barn-cm |
|---|---|
| Pu-239 | 3.7047e-2 |
| Pu-240 | 1.7512e-3 |
| Pu-241 | 1.1674e-4 |
| Ga (natural) | 1.3752e-3 |

The benchmark's k-eff is 1.0000 +/- 0.0020. A code and data library that reproduce it within the uncertainties pass a basic test of
fast-spectrum criticality.

## The model

- **Geometry:** one sphere with a vacuum boundary, filled with the alloy. The density is the sum of the atom densities
  (`set_density("sum")`).
- **Settings:** eigenvalue mode.
- **Initial source:** a box inside the sphere limited to fissionable material, so the first batches do not start from one point.
- **Shannon entropy:** an 8 x 8 x 8 entropy mesh lets `post.py` show that the fission source settled during the inactive batches.

## Results (OpenMC 0.15.3, ENDF/B-VIII.0)

| Size | Histories | k-eff | Against the benchmark | Time here |
|---|---|---|---|---|
| quick | 2,000 x (60 - 20) | 0.99874 +/- 0.00253 | -126 pcm, -0.39 combined sigma | 5 s |
| reference | 20,000 x (220 - 20) | 0.99982 +/- 0.00030 | -18 pcm | 7 s |

The test passes when k-eff lies within 3 combined standard deviations of 1.0000 (the run's and the benchmark's 0.0020 together), and
the source has converged.

## Running it with AutoMC

1. **Build:** `OpenMCModelBuildTool` with `build.py`.
2. **Plot and check:** `OpenMCGeometryPlotTool`, `OpenMCModelCheckTool`.
3. **Run:** `OpenMCRunStartTool`, approved by the user.
4. **Read:** `python post.py <run folder>` gives k-eff, the difference from the benchmark in pcm and in combined standard
   deviations, and the convergence verdict. `--benchmark` and `--benchmark-sigma` make the same reading work for any other
   critical benchmark.

## What it teaches

- Atom densities and `set_density("sum")`.
- An eigenvalue run with inactive batches.
- A fission source restricted to fissionable material.
- Shannon entropy as a convergence check.
- Judging a k-eff against a benchmark with both uncertainties.
