"""
Continuous energy, then multigroup: run the assembly in CE (in ce/), load the two-group library from that statepoint and write it as an
MG cross section file (mgxs.h5), then run the same geometry in multigroup mode with materials that point at the file (in mg/).
Writes comparison.json for post.py.

A script run (two transport runs and the library step between them), so the agent sends it to OpenMCRunStartTool with the case and
this script, and the user approves it. It runs in a copy of the case folder and builds the model from the copied build.py with the
variant's OPTIONS (a fresh build gives the tallies the same IDs as the model.xml that was checked).
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))      # python -I leaves the script's own folder off the path

import openmc                                                          # noqa: E402

import build                                                           # noqa: E402

opts = build.OPTIONS
model, lib = build.build_with_library(opts["legendre_order"], opts["size"])
ce_path = model.run(cwd="ce", output=False)
with openmc.StatePoint(ce_path) as sp:
    ce_k = sp.keff
    lib.load_from_statepoint(sp)

names = [d.name for d in lib.domains]                                  # in the order of the library's domains
# the MG file, materials that each point at one of its data sets, and the same geometry filled with them
mg_file, mg_materials, mg_geometry = lib.create_mg_mode(xsdata_names=names)
mg_file.export_to_hdf5("mgxs.h5")
mg_materials.cross_sections = os.path.abspath("mgxs.h5")
model.materials, model.geometry = mg_materials, mg_geometry
model.settings.energy_mode = "multi-group"
model.tallies = openmc.Tallies()
mg_path = model.run(cwd="mg", output=False)
with openmc.StatePoint(mg_path) as sp:
    mg_k = sp.keff

with open("comparison.json", "w", encoding="utf-8") as fh:
    json.dump({"ce_keff": ce_k.nominal_value, "ce_keff_sigma": ce_k.std_dev, "mg_keff": mg_k.nominal_value,
               "mg_keff_sigma": mg_k.std_dev, "legendre_order": opts["legendre_order"], "materials": names}, fh, indent=1)
print(f"CE k-inf {ce_k.nominal_value:.5f} +/- {ce_k.std_dev:.5f}; MG k-inf {mg_k.nominal_value:.5f} +/- {mg_k.std_dev:.5f}")
