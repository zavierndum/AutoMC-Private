# Depleting a fuel pin

Derived from: the OpenMC notebook "Pincell Depletion" (openmc-notebooks, tag v0.15.0; the pin, the power and the steps) and OpenMC's
script example `examples/pincell_depletion`, whose teaching chain `chain_simple.xml` ships here unchanged (MIT licence, copyright
MIT, UChicago Argonne LLC and OpenMC contributors). Written for AutoMC.

## The problem

A 4.25% UO2 pin in an infinite lattice burns at 174 W per cm of its length for 180 days. How do k, U-235 and Xe-135 change?

Depletion alternates two calculations:

1. **Transport:** OpenMC runs, giving every reaction rate in the fuel.
2. **Burnup:** the depletion solver evolves the nuclide inventory over a time step with those rates, using the chain's decays,
   reactions and fission yields.

The **predictor** integrator (forward Euler) holds each step's starting rates fixed over the step. Others, such as CE/CM, take a
midpoint and allow longer steps.

## The pieces

| Piece | Here |
|---|---|
| model | fuel r = 0.42 cm (10.4 g/cm3, 4.25 at% U-235), zirconium to 0.45 cm (6.0 g/cm3), water (1.0 g/cm3, bound hydrogen), 1.24 cm square, mirrored sides |
| depletable material | the fuel: `depletable = True` and `volume` = pi r^2 (for 1 cm of pin). Depletion needs the volume to turn rates per cm3 into atoms |
| chain | `chain_simple.xml`: nine nuclides (I-135, Xe-135, Xe-136, Cs-135, Gd-156, Gd-157, U-234, U-235, U-238). It is a teaching chain, too small for real work |
| operator | `openmc.deplete.CoupledOperator(model)`, with the chain from `openmc.config["chain_file"]`, which is OPENMC_CHAIN_FILE or a chain registered in `cross_sections.xml` |
| integrator | `PredictorIntegrator(operator, [30] * 6, power=174, timestep_units="d")` |

`run.py` does the depletion. It runs transport at every step, so it is a **script run**: OpenMCRunStartTool with `case` and `script`,
approved by the user.

- It copies the chain it used into the run folder (`chain_used.xml`).
- It calls `integrate(write_rates=True)`. Without that, OpenMC 0.15 does not save reaction rates in `depletion_results.h5`.

## Results (OpenMC 0.15.3, ENDF/B-VIII.0, chain_simple.xml)

| Day | k-infinity, quick (1,000 x 30 active, 13 s) | k-infinity, reference (10,000 x 100 active, 122 s) |
|---|---|---|
| 0 | 1.45980 +/- 0.00491 | 1.45899 +/- 0.00085 |
| 30 | 1.42632 | 1.43321 +/- 0.00083 |
| 90 | 1.42225 | 1.42508 +/- 0.00081 |
| 180 | 1.41269 +/- 0.00478 | 1.40815 +/- 0.00079 |

- **k falls by 0.051 over 180 days** (about 2,500 pcm of reactivity). Half of that comes in the first step, as Xe-135 and the iodine
  that feeds it reach equilibrium within days. The rest is U-235 being used up.
- **U-235** falls by 14.8%.
- **Xe-135** is at equilibrium after the first step. It drifts down 10% as the spectrum and flux change.
- **Checks in `post.py`:**
  - at every step the fission power (each nuclide's fission rate times the chain's Q) equals 174 W, to rounding;
  - the U-235 destroyed equals the U-235 fissioned (0.987, the rest being the predictor's fixed starting rates). This teaching chain
    gives U-235 no capture; with a full chain the ratio would be about 1 + alpha, near 1.2;
  - U-235 makes 95% of the fissions.

## Running it with AutoMC

1. **Build:** `OpenMCModelBuildTool` with `build.py`.
2. **Chain:** make sure a depletion chain is configured: OPENMC_CHAIN_FILE, or a chain registered in `cross_sections.xml`. For real
   work, use a full chain from openmc.org's data page, such as ENDF/B-VIII.0's PWR chain.
3. **Run:** `OpenMCRunStartTool` with `case` and `script` (the text of `run.py`), approved by the user.
4. **Read:** `python post.py <run folder> --plot depletion.png`; `openmc.deplete.Results` reads the file.

## What it teaches

- Depletable materials and their volume.
- Chains.
- `CoupledOperator`, integrators and time steps.
- `write_rates`.
- `Results.get_keff`, `get_atoms` and reaction rates.
- Checking a depletion against conservation.
