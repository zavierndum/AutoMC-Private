# MGXS introduction: two-group cross sections of an infinite mixture

Derived from: the OpenMC notebook "MGXS Part I: Introduction" (openmc-notebooks, tag v0.15.0; the mixture, the two groups and the four
cross section types). Written for AutoMC.

## The problem

Deterministic codes (diffusion, nodal, method of characteristics) work with a few energy groups and need cross sections averaged over
each group. A Monte Carlo run can make them: the reaction rate in a group divided by the flux in that group, both tallied on the same
tracks. `openmc.mgxs` does the bookkeeping. Each cross section object knows the tallies it needs, and once they are loaded from a
statepoint it returns the group values with their errors.

## The model

| Part | Value |
|---|---|
| medium | one homogeneous mixture of H-1, O-16, U-235, U-238 and Zr-90 at 5.0 g/cm3, bound hydrogen (`c_H_in_H2O`) |
| cell | a 1.26 cm square box with mirrored sides and no ends: an infinite medium |
| groups | two: thermal below 0.625 eV, fast above |
| cross sections | total, absorption, scattering, nu-fission |

## How it runs

- **`build.py`** makes the four MGXS objects for the cell (`cross_sections()`), adds the tallies each one brings, and writes `model.xml`.
- **`post.py`** makes the same objects again for the same cell, taken from the run's summary, loads their tallies with
  `load_from_statepoint`, reads them with `get_xs`, and checks two things:
  - **the sum rule:** in each group, total = absorption + scattering, because all three come from the same tracks;
  - **one-group k:** collapsed to one group with `get_condensed_xs`, nu-fission over absorption is k-infinity, since an infinite
    medium has no leakage. It must agree with OpenMC's own k.

## Results (OpenMC 0.15.3, ENDF/B-VIII.0)

| Size | Histories | k-infinity | One-group nu-fission / absorption | Time here |
|---|---|---|---|---|
| quick | 2,500 x (50 - 10) | 1.15362 ± 0.00283 | 1.15050 ± 0.00671 | 10 s |
| reference | 25,000 x (210 - 10) | 1.15646 ± 0.00040 | 1.15405 ± 0.00088 | 67 s |

The two-group cross sections at the reference size (1/cm):

| | Fast | Thermal |
|---|---|---|
| total | 0.68462 ± 0.00021 | 1.90420 ± 0.00156 |
| absorption | 0.01324 ± 0.00001 | 0.10227 ± 0.00009 |
| scattering | 0.67138 ± 0.00021 | 1.80193 ± 0.00147 |
| nu-fission | 0.00677 | 0.15397 ± 0.00013 |

- **The sum rule** holds to 3e-15 of the total, rounding only.
- **Thermal absorption** is 7.7 times the fast one: cross sections grow as neutrons slow down.
- **The one-group k** is within 2.5 of its own standard deviations of OpenMC's k. The two use the same tracks but different
  estimators, so they agree statistically, not exactly.
- **The notebook's k** was 1.15666 ± 0.00304 with older nuclear data, which agrees with the value here.

## Running it with AutoMC

1. **Build:** `OpenMCModelBuildTool` with `build.py`.
2. **Check:** `OpenMCModelCheckTool`.
3. **Run:** `OpenMCRunStartTool`, approved by the user.
4. **Read:** `python post.py <run folder>` through `PythonRunTool`.

Other groups go in `GROUP_EDGES` (eV, ascending), and other types in `TYPES` (for example `mgxs.ScatterMatrixXS` or `mgxs.Chi`).

## What it teaches

- What a multigroup cross section is, and how a Monte Carlo run makes one.
- MGXS objects: a domain, energy groups, a type, and the tallies they bring.
- Loading them back from a statepoint and reading them by group.
- Two checks: the sum rule, and k-infinity from one-group cross sections.
