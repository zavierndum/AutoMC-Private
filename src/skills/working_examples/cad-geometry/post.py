"""
Read a run of the CAD examples (OPTIONS["model"]):

- pin (comparison.json from run_pin.py): k of the CAD pin against its CSG twin, in pcm and in combined standard deviations;
- tokamak (comparison.json from run_tokamak.py): the blanket's flux per source neutron of the three frames; the mirrored half against
  the quarter (both stand for the whole torus), and the half as published against them (its cut leaks);
- teapot (a statepoint): the flux in the iron and in the water, and the leakage.

    python post.py RUN_FOLDER

Prints them and one last line `RESULTS {json}` for scripts and tests.
"""
import argparse
import glob
import json
import math
import os

import openmc


def statepoint(folder):
    files = sorted(glob.glob(os.path.join(folder, "statepoint.*.h5")), key=lambda p: int(p.split(".")[-2]))
    if not files:
        raise SystemExit(f"no statepoint in {folder}")
    return files[-1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("run_folder")
    a = parser.parse_args()
    comparison = os.path.join(a.run_folder, "comparison.json")
    if os.path.exists(comparison):
        with open(comparison, encoding="utf-8") as fh:
            c = json.load(fh)
    else:
        c = {"model": "teapot"}
    if c["model"] == "pin":
        cad, csg = c["cad"], c["csg"]
        diff = cad["keff"] - csg["keff"]
        sigma = math.hypot(cad["keff_sigma"], csg["keff_sigma"])
        print(f"CAD pin: k {cad['keff']:.5f} +/- {cad['keff_sigma']:.5f}, leakage {cad['leakage']:.4f}")
        print(f"CSG twin: k {csg['keff']:.5f} +/- {csg['keff_sigma']:.5f}, leakage {csg['leakage']:.4f}")
        print(f"CAD - CSG: {diff * 1e5:+.0f} pcm ({diff / sigma:+.1f} combined standard deviations)")
        out = {"keff_cad": cad["keff"], "keff_csg": csg["keff"], "cad_minus_csg_pcm": diff * 1e5, "cad_minus_csg_sigma": diff / sigma,
               "leakage_cad": cad["leakage"]}
    elif c["model"] == "tokamak":
        print("blanket flux per source neutron (cm), and leakage:")
        for frame in ("half", "mirrored", "quarter"):
            f = c[frame]
            print(f"  {frame:9s} {f['blanket_flux']:.5g} +/- {f['blanket_flux_sigma']:.2g}   leakage {f['leakage']:.4f}")
        m, q, h = c["mirrored"], c["quarter"], c["half"]
        mq = (m["blanket_flux"] - q["blanket_flux"]) / math.hypot(m["blanket_flux_sigma"], q["blanket_flux_sigma"])
        hm = h["blanket_flux"] / m["blanket_flux"] - 1
        print(f"mirrored half against quarter: {mq:+.1f} combined standard deviations (both are the whole torus)")
        print(f"half as published against the mirrored half: {hm:+.1%} (its cut plane leaks)")
        out = {"mirrored_minus_quarter_sigma": mq, "half_over_mirrored_minus_1": hm, "leakage_half": h["leakage"],
               "leakage_mirrored": m["leakage"]}
    else:
        with openmc.StatePoint(statepoint(a.run_folder)) as sp:
            iron, water = (sp.get_tally(name=f"flux in the {n}") for n in ("iron", "water"))
            fi, fw = float(iron.mean.ravel()[0]), float(water.mean.ravel()[0])
            leak = next(float(r["mean"]) for r in sp.global_tallies
                        if (r["name"].decode() if isinstance(r["name"], bytes) else str(r["name"])) == "leakage")
        print(f"teapot: flux per source neutron in the water {fw:.4g} cm, in the iron {fi:.4g} cm; leakage {leak:.4f}")
        out = {"flux_water": fw, "flux_iron": fi, "water_over_iron": fw / fi, "leakage": leak}
    print("RESULTS " + json.dumps(out))


if __name__ == "__main__":
    main()
