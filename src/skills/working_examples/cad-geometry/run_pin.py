"""
The CAD pin and its CSG twin, one after the other (in cad/ and csg/): the same materials and the same run, the geometry once from the
CAD file and once from build.py's PIN_CSG. Each run's k and leakage go to comparison.json for post.py.

A script run (two transport runs), so the agent sends it to OpenMCRunStartTool with the case and this script, and the user approves
it. It runs in a copy of the case folder (with the CAD file) and reads the variant's OPTIONS from the copied build.py.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))      # python -I leaves the script's own folder off the path

import openmc                                                          # noqa: E402

import build                                                           # noqa: E402


def globals_of(sp):
    """{name: (mean, std)} of the run's global tallies (k estimators, leakage)."""
    out = {}
    for row in sp.global_tallies:
        name = row["name"].decode() if isinstance(row["name"], bytes) else str(row["name"])
        out[name] = (float(row["mean"]), float(row["std_dev"]))
    return out


out = {}
for geometry in ("cad", "csg"):
    model = build.build("pin", build.OPTIONS["size"], frame="half" if geometry == "cad" else "csg")
    with openmc.StatePoint(model.run(cwd=geometry, output=False)) as sp:
        g = globals_of(sp)
        out[geometry] = {"keff": sp.keff.nominal_value, "keff_sigma": sp.keff.std_dev, "leakage": g["leakage"][0],
                         "leakage_sigma": g["leakage"][1]}
    print(f"{geometry}: k {out[geometry]['keff']:.5f} +/- {out[geometry]['keff_sigma']:.5f}, leakage {out[geometry]['leakage']:.4f}")
with open("comparison.json", "w", encoding="utf-8") as fh:
    json.dump({"model": "pin", **out}, fh, indent=1)
