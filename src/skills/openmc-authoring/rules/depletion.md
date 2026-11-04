# Depletion

Derived from: the OpenMC 0.15.3 user's guide, depletion page, and the depletion chain and results format pages (MIT). Written for
AutoMC; arguments and defaults checked in the installed package (2026-11-04).

1. **A depletion is a script run** (`run.py`, planned with OpenMCRunStartTool with the case and the script): it runs transport many
   times. `build.py` builds the model; `run.py` imports it and integrates (`working_examples/depletion`).
2. **A chain** lists the nuclides, their decays and reactions, and fission yields. Give it as `chain_file` or through
   `openmc.config["chain_file"]` (the variable OPENMC_CHAIN_FILE). The ENDF/B-VIII.0 data here come without one; the example uses the
   small teaching chain of OpenMC's examples, which leaves out reactions a real burnup calculation needs (it gives U-235 no
   capture). Say so whenever a result comes from it.
3. **Depletable materials** need `openmc.Material.depletable = True` and a volume (`openmc.Material.volume`); the operator refuses a
   depletable material without one. A material repeated in many cells depletes as one unless
   `openmc.Model.differentiate_depletable_mats` splits it.
4. **The operator:** `openmc.deplete.CoupledOperator(model, chain_file=..., normalization_mode=...)`. `normalization_mode` is
   `"fission-q"` (the default: power from fission energy), `"energy-deposition"` or `"source-rate"`.
5. **The integrator** takes the operator, the time steps, the power (W, for the whole model) or `power_density` (W per gram of heavy
   metal), and `timestep_units` (`"s"` by default, `"min"`, `"h"`, `"d"`, `"a"`, or `"MWd/kg"` for burnup steps).
   `openmc.deplete.PredictorIntegrator` is first order and cheap; `openmc.deplete.CECMIntegrator` is second order and the usual
   choice for results to quote. A pin's power per cm times its height is the power to give.
6. **`integrate(write_rates=True)`** if reaction rates are wanted afterwards: in 0.15.3 they are not saved otherwise. The last time
   point holds atoms and k but no rates.
7. **Results:** `openmc.deplete.Results("depletion_results.h5")`, then `get_keff()`, `get_atoms(material, nuclide)`,
   `get_reaction_rate(...)`, `get_activity`, `get_decay_heat` and `export_to_materials`.
8. **Check the energy:** the fission power summed over the step should be the power given, to rounding, counting every fissioning
   nuclide (U-234 too): `working_examples/depletion` checks it to 1e-16.
