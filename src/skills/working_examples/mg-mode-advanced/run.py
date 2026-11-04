"""
Continuous energy, then multigroup twice: run the assembly in CE (in ce/), load both libraries from that statepoint, and for each
write an MG file and run the mesh-homogenized geometry it describes in multigroup mode (in mg-isotropic/ and mg-angle-dependent/).
Writes comparison.json for post.py.

A script run (three transport runs), so the agent sends it to OpenMCRunStartTool with the case and this script, and the user approves
it. It runs in a copy of the case folder and builds the model from the copied build.py with the variant's OPTIONS (a fresh build gives
the tallies the same IDs as the model.xml that was checked).
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))      # python -I leaves the script's own folder off the path

import openmc                                                          # noqa: E402

import build                                                           # noqa: E402

model, libs = build.build_with_libraries(build.OPTIONS["size"])
ce_path = model.run(cwd="ce", output=False)
result = {}
with openmc.StatePoint(ce_path) as sp:
    result["ce"] = (sp.keff.nominal_value, sp.keff.std_dev)
    for lib in libs.values():
        lib.load_from_statepoint(sp)

for name, lib in libs.items():
    # a mesh domain: the MG geometry is the mesh itself, each mesh cell a material with its own data set; mirrored all round
    mg_file, mg_materials, mg_geometry = lib.create_mg_mode()
    path = os.path.abspath(f"mgxs-{name}.h5")
    mg_file.export_to_hdf5(path)
    mg_materials.cross_sections = path
    mg = openmc.Model(geometry=mg_geometry, materials=mg_materials, settings=model.settings)
    mg.settings.energy_mode = "multi-group"
    with openmc.StatePoint(mg.run(cwd=f"mg-{name}", output=False)) as sp:
        result[name] = (sp.keff.nominal_value, sp.keff.std_dev)

with open("comparison.json", "w", encoding="utf-8") as fh:
    json.dump({k: {"keff": v[0], "sigma": v[1]} for k, v in result.items()}, fh, indent=1)
print(", ".join(f"{k}: {v[0]:.5f} +/- {v[1]:.5f}" for k, v in result.items()))
