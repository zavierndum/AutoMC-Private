# Resonance covariances: the uncertainty of Gd-157's resolved resonances

Derived from: the OpenMC notebook "Nuclear Data: Resonance Covariance" (openmc-notebooks, tag v0.15.0; the nuclide, the covariance
object, sampling and the subset by spin). Written for AutoMC.

## The problem

A resonance's energy and widths are measured quantities with uncertainties, and those uncertainties are correlated: two parameters
fitted from the same measurement move together. An evaluation gives them as a covariance matrix of the resonance parameters (ENDF File
32). To propagate data uncertainty into a result, for example the spread of k from sampled data, you need to:
- read that matrix;
- see what it says;
- draw parameter sets from it.

`openmc.data` does all three.

**An analysis, not a model.** It has `analysis.py` instead of `build.py`, and the agent runs it with `PythonRunTool`, approved by the
user. Fetch its file once with `python -m automc data fetch resonance-covariance`: `endf/n-064_Gd_157.endf`, ENDF/B-VIII.0 from NNDC.
The notebook's ENDF/B-VII.1 file came from a LANL server that no longer answers.

## What it does

1. **Reads the file with `covariance=True`.** Gd-157's resolved range, 1e-5 to 306.6 eV, is in the Reich-Moore form with 60
   resonances. Each resonance has three uncertain parameters, its energy and its neutron and capture widths, so the matrix is
   180 x 180.
2. **Makes the matrix whole.** The file stores only its upper triangle, so the full matrix is the upper triangle plus its transpose,
   less the diagonal counted twice. It then checks the result is a valid covariance: no eigenvalue below zero.
3. **Turns it into relative uncertainties** (the square root of each variance over the value) **and correlations** (each covariance
   over the product of the two standard deviations).
4. **Samples parameter sets with `sample()`,** 2,000 with a fixed seed, and checks that they reproduce the evaluated values and
   uncertainties.
5. **Takes the subset of one spin** (J = 2) with `subset()`, and checks that its matrix is exactly the matching block of the whole.

## Results (OpenMC 0.15.3, ENDF/B-VIII.0; 1.7 s)

Relative uncertainties of the first resonances:

| Resonance | J | Energy | Neutron width | Capture width |
|---|---|---|---|---|
| 0.0314 eV | 2 | 5.35% | 0.78% | 2.67% |
| 2.825 eV | 2 | 0.09% | 3.86% | 5.41% |
| 16.24 eV | 1 | 0.12% | 20.30% | 46.74% |
| 16.77 eV | 2 | 0.02% | 4.36% | 6.94% |
| 20.56 eV | 2 | 0.02% | 4.40% | 6.21% |

- **The matrix is a valid covariance:** its eigenvalues run from 1.5e-14 to 1.5e-2, none negative.
- **Most parameters are nearly independent.** Only 1.4% of the correlations exceed 0.1 in size, but a few pairs are almost fully
  correlated, from -0.996 to +0.950.
- **The samples reproduce the evaluation:**
  - the worst of the 180 sample means is 2.98 standard errors from its evaluated value, about what the largest of 180 should be;
  - the sample standard deviations are 0.958 to 1.041 times the evaluated ones.
- **0.18% of the sampled widths are negative.** The sampler draws from a normal distribution and does not keep widths positive. One
  resonance, at 139.3 eV, gives 88% of the negative values. A study that reconstructs cross sections from samples must reject or
  correct such sets.
- **The J = 2 subset** holds 36 of the 60 resonances. Its 108 x 108 matrix equals the matching block exactly.
- **Thermal capture is pinned to the 0.0314 eV resonance,** whose energy is the least certain of the five above (5.35%). It is the
  resonance that makes Gd-157 a strong thermal absorber.
- **Not shown:** reconstructing cross sections from the sampled parameters (`reconstruct`) needs a compiled extension this OpenMC
  build lacks.

## Running it with AutoMC

1. `python -m automc data fetch resonance-covariance`, once (the user runs it; it needs the network).
2. `PythonRunTool` with the text of `analysis.py`, approved by the user. It writes nothing it keeps.

`OPTIONS` sets the number of samples, the seed and the spin of the subset.

## What it teaches

- Resonance-parameter covariances (ENDF File 32) through `IncidentNeutron.from_endf(..., covariance=True)`.
- An upper-triangular matrix made whole, and checked as a covariance.
- Relative uncertainties and correlations.
- Sampling parameter sets, testing the samples against the evaluation, and the negative widths a normal sampler can produce.
- Subsets of resonances and their block of the matrix.
