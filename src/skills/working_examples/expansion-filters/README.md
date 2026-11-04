# Functional expansions: flux shapes as Legendre and Zernike series

Derived from: the OpenMC notebook "Functional Expansions" (openmc-notebooks, tag v0.15.0; both geometries, their materials and the
expansion orders). Written for AutoMC.

## The idea

A mesh tally gives a flux shape as many small bins, each with its own noise. An expansion tally scores the moments of the flux
instead: the flux weighted by each polynomial of an orthogonal set, integrated over the region. A handful of moments describe a smooth
shape, which can then be evaluated anywhere.

- **`SpatialLegendreFilter(order, axis, min, max)`:** Legendre polynomials along one axis.
- **`ZernikeRadialFilter(order, x, y, r)`:** radial-only Zernike polynomials over a disc.
- **`ZernikeFilter`:** the full set over a disc, angle included.

## The two layouts

| Layout | Geometry | Tallies |
|---|---|---|
| `column` | 10 x 10 cm of 4.5% UO2, z from -10 to 10 cm, mirrored sides, vacuum ends, a 1 cm slab of boron carbide (2.5 g/cm3) across the middle | Legendre to order 8 along z; an 80-slice z mesh |
| `pellet` | a bare 5% UO2 pellet, r = 0.5 cm, z from -1 to 1 cm, vacuum all round | radial Zernike to order 10; full Zernike to order 10; a 25-ring cylindrical mesh |

## Rebuilding the shape

- **Legendre.** With moments a_n over [z_min, z_max] on a cross-section A, the flux per unit volume is
  φ(z) = Σ (2n + 1) a_n P_n(x) / (A · L), where x maps z onto [-1, 1] and L = z_max - z_min.
  `openmc.legendre_from_expcoef(a, domain)` returns the series per unit length; `post.py` divides by A.
- **Zernike.** `openmc.ZernikeRadial(a, radius)` and `openmc.Zernike(a, radius)` return the flux per unit area; dividing by the
  height gives per volume.
- **Use the same estimator.** Expansion filters are scored with the **collision** estimator. The mesh tallies here are set to
  collision too, so both use the same events. The zeroth moment is then exactly the sum of the bins, which `post.py` checks.

## Results (OpenMC 0.15.3, ENDF/B-VIII.0)

| | quick | reference |
|---|---|---|
| column: k-eff | 0.30274 +/- 0.00090 (6 s) | 0.30268 +/- 0.00014 (23 s) |
| column: zeroth moment / sum of slices | 1.000000000 | 1.000000000 |
| column: expansion against slices, RMS | 1.3% | 1.1% |
| column: flux in the absorber / fuel 1.5 cm away | 0.951 | 0.952 |
| pellet: k-eff | 0.01134 +/- 0.00003 (5 s) | 0.011321 +/- 0.000006 (9 s) |
| pellet: zeroth moment / sum of rings | 1.000000000 | 1.000000000 |
| pellet: radial expansion against rings, RMS | 4.5% | 0.7% |
| pellet: centre flux / edge flux | 1.7 | 2.08 |

- **The column** has no moderator, so its spectrum is fast and the boron carbide only dents it, by about 5%. The cosine shape set by
  the open ends dominates: the absorber is still 9% above the flux 5 cm away.
- **The pellet** is tiny and bare, so k is about 0.01 and the flux falls by half from the centre to the surface.
- **Rings at the quick size.** The thin outer rings are scored by few collisions, so the quick comparison there is noisy.

## Running it with AutoMC

1. **Build:** `OpenMCModelBuildTool` with `build.py` (OPTIONS: layout, size).
2. **Run:** `OpenMCRunStartTool`, approved by the user.
3. **Read:** `python post.py <run folder> --plot shape.png`.

## What it teaches

- Legendre and Zernike expansion filters.
- Reconstruction with OpenMC's helpers, and its normalization.
- Collision against track-length estimators.
- An exact identity used as a test.
