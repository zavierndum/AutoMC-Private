"""
The CAD half tokamak three ways (in half/, mirrored/ and quarter/): as published, inside a CSG box that mirrors it at its cut y = 0,
and inside a box that also cuts it at x = 0 and mirrors it there. The blanket's flux per source neutron and the leakage of each go to
comparison.json for post.py.

A script run (three transport runs), so the agent sends it to OpenMCRunStartTool with the case and this script, and the user
approves it. It runs in a copy of the case folder (with the CAD file) and reads the variant's OPTIONS from the copied build.py.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))      # python -I leaves the script's own folder off the path

import openmc                                                          # noqa: E402

import build                                                           # noqa: E402


def globals_of(sp):
    """{name: (mean, std)} of the run's global tallies (leakage among them)."""
    out = {}
    for row in sp.global_tallies:
        name = row["name"].decode() if isinstance(row["name"], bytes) else str(row["name"])
        out[name] = (float(row["mean"]), float(row["std_dev"]))
    return out


out = {}
for frame in ("half", "mirrored", "quarter"):
    model = build.build("tokamak", build.OPTIONS["size"], frame=frame)
    with openmc.StatePoint(model.run(cwd=frame, output=False)) as sp:
        t = sp.get_tally(name="blanket flux")
        g = globals_of(sp)
        out[frame] = {"blanket_flux": float(t.mean.ravel()[0]), "blanket_flux_sigma": float(t.std_dev.ravel()[0]),
                      "leakage": g["leakage"][0], "leakage_sigma": g["leakage"][1]}
    print(f"{frame}: blanket flux {out[frame]['blanket_flux']:.4g} +/- {out[frame]['blanket_flux_sigma']:.2g} cm per source neutron, "
          f"leakage {out[frame]['leakage']:.4f}")
with open("comparison.json", "w", encoding="utf-8") as fh:
    json.dump({"model": "tokamak", **out}, fh, indent=1)
