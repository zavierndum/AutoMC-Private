# Tally arithmetic: the six-factor formula

Derived from: the OpenMC notebook "Tally Arithmetic" (openmc-notebooks, tag v0.15.0; its pin, energy cut, tallies and factors).
Written for AutoMC.

## The problem

Reactor physics splits k into factors that each tell a story:

k = ε · p · f · η · P_FNL · P_TNL

| Factor | Meaning |
|---|---|
| ε | fast fission: all neutrons made, over those made by thermal fission |
| p | resonance escape: the chance of slowing past the resonances |
| f | thermal utilization: the share of thermal absorptions that happen in the fuel |
| η | neutrons made per thermal absorption in the fuel |
| P_FNL | fast non-leakage |
| P_TNL | thermal non-leakage |

Each factor is a ratio of tallies. OpenMC's tally arithmetic (`+ - * /` on Tally objects) builds the ratios as derived tallies and
carries their uncertainties.

## The model and its tallies

- **The pin:** the notebooks' 1.6% pin (see `post-processing`). It is mirrored on its four sides, **100 cm tall, and open (vacuum) at
  both ends**, so neutrons really leak.
- **Why the ends are open.** The notebook used a box mirrored on all sides. With nothing leaking, its leakage factors reflected
  something else: it summed every mesh-surface current bin, the "in" ones as well as the "out" ones.
- **Leakage here** is the net current out of one mesh cell drawn around the pin: the `out` bins of a `MeshSurfaceFilter` minus its
  `in` bins. On the mirrored sides these cancel; at the open ends nothing comes back in.
- **Tallies:** production (nu-fission), absorption, the same below 0.625 eV, thermal absorption in the fuel, leakage and thermal
  leakage. A flux tally and the fuel's reaction rates by group give a cross section too.

## Results (OpenMC 0.15.3, ENDF/B-VIII.0)

| | quick (4,000 x 40 active, 10 s) | reference (40,000 x 150 active, 120 s) |
|---|---|---|
| k-eff, OpenMC's estimate | 0.98772 +/- 0.00219 | 0.98979 +/- 0.00035 |
| production / (absorption + leakage) | 0.98816 +/- 0.00442 | 0.98855 +/- 0.00063 |
| ε | 1.2063 | 1.20764 +/- 0.00092 |
| p | 0.6915 | 0.69141 +/- 0.00048 |
| f | 0.7502 | 0.74965 +/- 0.00060 |
| η | 1.6589 | 1.65890 +/- 0.00136 |
| P_FNL | 0.9581 | 0.95814 +/- 0.00055 |
| P_TNL | 0.9934 | 0.99360 +/- 0.00079 |
| leakage per absorption | 4.9% | 4.8% |
| U-235 fission cross section below 0.625 eV | 374 b | 374.3 b |

- **The product** of the six equals production / (absorption + leakage) to the last digit: the ratios telescope, which `post.py`
  checks.
- **The balance and OpenMC's k-eff** are two estimators of the same k. They agree within their errors (-1.7 sigma at the reference
  size); the derived tally's error is the larger, because it treats its parts as independent.
- **The cross section.** U-235's spectrum-averaged thermal cross section is 374 b, against 585 b at 0.0253 eV. Hardening and
  self-shielding lower it.

## Running it with AutoMC

1. **Build:** `OpenMCModelBuildTool` with `build.py` (OPTIONS: height_cm, size).
2. **Run:** `OpenMCRunStartTool`, approved by the user.
3. **Read:** `python post.py <run folder>` through `PythonRunTool`.

## What it teaches

- Tally arithmetic and derived tallies.
- `get_slice` and `summation` on a filter.
- Net leakage from mesh-surface currents.
- The six factors.
- A microscopic cross section from a rate and a flux.
