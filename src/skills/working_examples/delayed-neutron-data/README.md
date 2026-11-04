# Delayed-neutron data: beta, decay constants and the two fission spectra

Derived from: the OpenMC notebook "MDGXS Part I: Introduction" (openmc-notebooks, tag v0.15.0; the mixture, the 100-group structure,
the six delayed groups and the cross section types). Written for AutoMC. The library form of these data (MDGXS Part II) is in
`mgxs-library-mg-mode`.

## The problem

A kinetics code (point kinetics or a time-dependent diffusion solver) needs what a static k leaves out: how many fission neutrons are
delayed (the delayed fraction beta), how fast their precursors decay (a decay constant per delayed group), and the energies they are born
at (the delayed fission spectrum, softer than the prompt one). `openmc.mgxs` tallies all of these. This example makes them for an
infinite mixture of U-235, U-238 and Pu-239.

## The model

| Part | Value |
|---|---|
| medium | a homogeneous mixture of H-1, O-16, U-235, U-238, Pu-239 and Zr-90 at 5.0 g/cm3, bound hydrogen |
| cell | a 1.26 cm square box with mirrored sides and no ends: an infinite medium |
| delayed groups | the six of the nuclear data |
| groups | one for beta and the decay constants; 100 equal-lethargy groups from 1 meV to 20 MeV for the spectra |
| quantities | beta and decay rate by nuclide; prompt and delayed chi; prompt, delayed and total nu-fission |

## How it runs

- **`build.py`** makes the objects and adds their tallies: `mgxs.Beta`, `mgxs.DecayRate`, `mgxs.Chi` with `prompt=True`,
  `mgxs.ChiDelayed`, `mgxs.FissionXS` with `nu=True` (with and without `prompt=True`), and `mgxs.DelayedNuFissionXS`.
- **`post.py`** reads them and checks:
  - **prompt + delayed nu-fission = total nu-fission;**
  - **each chi sums to 1;**
  - **the decay constants rise** from delayed group 1 (the longest-lived precursors) to group 6.

**The mixture's beta is not the sum of the nuclides' betas.** With `by_nuclide=True`, `Beta` gives each nuclide's own fraction:
delayed neutrons over all neutrons from that nuclide's fissions. Adding those up (what `nuclides="sum"` does) gives 0.024, four times
too much. The mixture's beta is its delayed nu-fission over its total nu-fission, which weights each nuclide by the neutrons its
fissions make. `post.py` computes it that way.

## Results (OpenMC 0.15.3, ENDF/B-VIII.0)

| Size | Histories | k-infinity | Mixture's beta | Time here |
|---|---|---|---|---|
| quick | 5,000 x (50 - 10) | 1.23081 ± 0.00197 | 0.00557 | 18 s |
| reference | 50,000 x (110 - 10) | 1.22899 ± 0.00041 | 0.00557 | 307 s |

At the reference size:

| Delayed group | 1 | 2 | 3 | 4 | 5 | 6 |
|---|---|---|---|---|---|---|
| mixture's beta | 1.73e-4 | 9.79e-4 | 9.21e-4 | 2.10e-3 | 9.96e-4 | 3.97e-4 |
| U-235 decay constant (1/s) | 0.01334 | 0.03274 | 0.1208 | 0.3028 | 0.8495 | 2.853 |

- **Each nuclide's own beta:** U-235 0.00652, U-238 0.0152, Pu-239 0.00225.
  - U-238's is the largest, but it fissions only with fast neutrons and contributes little.
  - Pu-239's is about a third of U-235's, so the same reactivity is worth more dollars in a plutonium-fuelled core.
- **Group 1 of U-235** decays at 0.01334/s, a half-life of 52 s and a mean life of 75 s. Its precursors are why power falls no faster
  than a period of about 80 s after a scram.
- **Mean energy at birth:** prompt neutrons 2.05 MeV, delayed neutrons 0.51 MeV.
- **The checks:**
  - prompt + delayed over total nu-fission is 0.999998;
  - the delayed chi sums to 1;
  - the prompt chi sums to 1 within 1e-5.
- **The notebook's k** was 1.23332 ± 0.00230 with older nuclear data.

## Running it with AutoMC

1. **Build:** `OpenMCModelBuildTool` with `build.py`.
2. **Check:** `OpenMCModelCheckTool`.
3. **Run:** `OpenMCRunStartTool`, approved by the user.
4. **Read:** `python post.py <run folder>` through `PythonRunTool`.

## What it teaches

- The delayed-neutron quantities a kinetics code needs, and the MGXS types that tally them.
- Delayed groups next to energy groups.
- Why a mixture's beta is a weighted average and not a sum, and how to get it right.
- The softer delayed fission spectrum.
