"""
Deplete the pin: at each step OpenMC runs transport for the reaction rates, then solves the chain's equations over the step.

A script run (a transport run at every step), so the agent sends it to OpenMCRunStartTool with the case and this script, and the user
approves it. It runs in a copy of the case folder and reads the variant's OPTIONS from the copied build.py. The depletion chain is
OpenMC's configured one: openmc.config["chain_file"], from the OPENMC_CHAIN_FILE environment variable or a chain registered in
cross_sections.xml; a copy goes into the run folder. Writes depletion_results.h5 (and one statepoint per step) for post.py.
"""
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))      # python -I leaves the script's own folder off the path

import openmc                                                          # noqa: E402
import openmc.deplete                                                  # noqa: E402

import build                                                           # noqa: E402

opts = build.OPTIONS
if openmc.config.get("chain_file") is None:
    raise SystemExit("no depletion chain: set OPENMC_CHAIN_FILE to a chain file (this example ships chain_simple.xml)")
print(f"depletion chain: {openmc.config['chain_file']}")
shutil.copy(openmc.config["chain_file"], "chain_used.xml")             # the run keeps the chain it used (post.py reads its Q values)

model = openmc.Model.from_model_xml("model.xml")
operator = openmc.deplete.CoupledOperator(model)                       # the chain from openmc.config; fuel volume from the model
integrators = {"predictor": openmc.deplete.PredictorIntegrator, "cecm": openmc.deplete.CECMIntegrator}
integrator = integrators[opts["integrator"]](operator, opts["steps_days"], power=opts["power_W"], timestep_units="d")
integrator.integrate(write_rates=True)                                 # keep reaction rates: post.py checks the burnup with them
print(f"depleted over {sum(opts['steps_days'])} days in {len(opts['steps_days'])} steps at {opts['power_W']} W")
