"""
Two states of the pin in one OpenMC session (openmc.lib), the way a coupled code drives OpenMC: initialise once, run the batches one at
a time, read the heat tally from memory, change what the other code computed (cell temperatures, material densities), and run again.

1. As built: fuel 800 K, water 0.70 g/cm3.
2. After the feedback of build.py's FEEDBACK: every fuel cell at the new temperature, each water zone at its new density.

Each state is a simulation of its own (simulation_init ... simulation_finalize, tallies reset), so each has its own k and heat profile.
The values set are read back from OpenMC. Writes comparison.json for post.py.

A script run (it steps OpenMC through its C API), so the agent sends it to OpenMCRunStartTool with the case and this script, and the
user approves it. It runs in a copy of the case folder and reads the variant's OPTIONS from the copied build.py.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))      # python -I leaves the script's own folder off the path

import openmc                                                          # noqa: E402
import openmc.lib                                                      # noqa: E402

import build                                                           # noqa: E402

model = openmc.Model.from_model_xml("model.xml")
heat_id = next(t.id for t in model.tallies if t.name == "heat by zone")
fuel_cells = [c.id for c in sorted(model.geometry.get_all_cells().values(), key=lambda c: c.id) if c.name.startswith("fuel zone")]
water_mats = [m.id for m in sorted(model.materials, key=lambda m: m.id) if m.name.startswith("water zone")]
batches = model.settings.batches


def one_state():
    """One simulation: every batch stepped by hand; k, and the heat by zone with its standard deviation."""
    openmc.lib.reset()                                                 # tallies and k start from zero
    openmc.lib.simulation_init()
    for _ in range(batches):
        openmc.lib.next_batch()
    k, k_sd = openmc.lib.keff()
    tally = openmc.lib.tallies[heat_id]
    heat, heat_sd = tally.mean.ravel().tolist(), tally.std_dev.ravel().tolist()
    openmc.lib.simulation_finalize()
    return {"keff": k, "keff_sigma": k_sd, "heat": heat, "heat_sigma": heat_sd}


openmc.lib.init()                                                      # reads model.xml; threads from OMP_NUM_THREADS
nominal = one_state()

for cid in fuel_cells:                                                 # what a thermal-hydraulics code would hand back
    openmc.lib.cells[cid].set_temperature(build.FEEDBACK["fuel_K"])
for mid, rho in zip(water_mats, build.feedback_densities()):
    openmc.lib.materials[mid].set_density(rho, units="g/cm3")
readback = {"fuel_K": [openmc.lib.cells[cid].get_temperature() for cid in fuel_cells],
            "water_g_cm3": [openmc.lib.materials[mid].get_density("g/cm3") for mid in water_mats]}
fed_back = one_state()
openmc.lib.finalize()

with open("comparison.json", "w", encoding="utf-8") as fh:
    json.dump({"nominal": nominal, "feedback": fed_back, "readback": readback, "zones": build.zone_heights(),
               "feedback_asked": {"fuel_K": build.FEEDBACK["fuel_K"], "water_g_cm3": build.feedback_densities()}}, fh, indent=1)
print(f"as built k {nominal['keff']:.5f} +/- {nominal['keff_sigma']:.5f}; after the feedback {fed_back['keff']:.5f} +/- "
      f"{fed_back['keff_sigma']:.5f}")
