"""
Two runs of the shielded room in one OpenMC session (openmc.lib): an analog run, then a run with weight windows that the MAGIC method
makes from the analog run's flux mesh. Each run's flux and its standard deviation, and its time, go to comparison.npz for post.py.

A script run (it steps OpenMC through its C API), so the agent sends it to OpenMCRunStartTool with the case and this script, and the
user approves it. It runs in a copy of the case folder and reads the variant's OPTIONS from the copied build.py.
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))      # python -I leaves the script's own folder off the path

import numpy as np                                                     # noqa: E402
import openmc                                                          # noqa: E402
import openmc.lib                                                      # noqa: E402

import build                                                           # noqa: E402

size = build.SIZES[build.OPTIONS["size"]]
tally_id = next(t.id for t in openmc.Model.from_model_xml("model.xml").tallies if t.name == "neutron flux")

openmc.lib.init()                                                      # reads model.xml; threads from OMP_NUM_THREADS
flux = openmc.lib.tallies[tally_id]
windows = openmc.lib.WeightWindows.from_tally(flux)                    # same mesh and particle as the tally, empty for now

openmc.lib.settings.particles = size["analog"]
t0 = time.time()
openmc.lib.run()
analog_s = time.time() - t0
analog = (flux.mean.ravel().copy(), flux.std_dev.ravel().copy())

windows.update_magic(flux)                                             # lower bounds from the analog flux (the MAGIC method)
openmc.lib.settings.weight_windows_on = True
openmc.lib.settings.particles = size["windows"]                        # fewer histories: each now splits and costs more
openmc.lib.reset()                                                     # the second run's tallies start from zero
t0 = time.time()
openmc.lib.run()
windows_s = time.time() - t0
with_windows = (flux.mean.ravel().copy(), flux.std_dev.ravel().copy())
openmc.lib.finalize()

np.savez("comparison.npz", analog_mean=analog[0], analog_sd=analog[1], windows_mean=with_windows[0], windows_sd=with_windows[1])
with open("comparison.json", "w", encoding="utf-8") as fh:
    json.dump({"analog_s": analog_s, "windows_s": windows_s, "analog_particles": size["analog"], "windows_particles": size["windows"],
               "batches": size["batches"], "mesh": build.OPTIONS["mesh"]}, fh, indent=1)
print(f"analog run {analog_s:.1f} s, weight-window run {windows_s:.1f} s")
