"""
Two-group cross sections from the run: make the same MGXS objects as build.py (for the same cell, from the run's summary), load their
tallies from the statepoint, print them, and check them: total = absorption + scattering in each group, and k-infinity rebuilt from
one-group cross sections (nu-fission over absorption) against OpenMC's k.

    python post.py RUN_FOLDER

Prints them and one last line `RESULTS {json}` for scripts and tests.
"""
import argparse
import glob
import importlib.util
import json
import math
import os

import openmc
import openmc.mgxs as mgxs


def statepoint(folder):
    files = sorted(glob.glob(os.path.join(folder, "statepoint.*.h5")), key=lambda p: int(p.split(".")[-2]))
    if not files:
        raise SystemExit(f"no statepoint in {folder}")
    return files[-1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("run_folder")
    a = parser.parse_args()
    # build.py defines the cross sections; a run folder holds only the frozen model.xml, so it is in the case folder above it
    here = os.path.join(a.run_folder, "build.py")
    spec = importlib.util.spec_from_file_location("build", here if os.path.exists(here) else os.path.join(a.run_folder, "..", "build.py"))
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    with openmc.StatePoint(statepoint(a.run_folder)) as sp:
        k = sp.keff
        cell = next(c for c in sp.summary.geometry.get_all_cells().values() if c.name == "medium")
        xs = build.cross_sections(cell)
        for x in xs.values():
            x.load_from_statepoint(sp)              # finds its tallies in the statepoint and computes the cross section
    get = lambda name: (xs[name].get_xs(), xs[name].get_xs(value="std_dev"))
    out = {"keff": float(k.nominal_value), "keff_sigma": float(k.std_dev)}
    total, absorption, scatter = get("total")[0], get("absorption")[0], get("scatter")[0]
    for name in xs:
        print(f"{name:11s} " + "  ".join(f"group {g + 1}: {m:.5f} +/- {s:.5f} /cm" for g, (m, s) in enumerate(zip(*get(name)))))
    # groups count from the highest energy: group 1 is fast, group 2 thermal
    out["sum_rule_worst"] = float(max(abs(t - a_ - s) / t for t, a_, s in zip(total, absorption, scatter)))
    out["thermal_over_fast_absorption"] = float(absorption[1] / absorption[0])
    one = {name: x.get_condensed_xs(mgxs.EnergyGroups([0.0, 20.0e6])) for name, x in xs.items()}
    nf, ab = one["nu-fission"].get_xs()[0], one["absorption"].get_xs()[0]
    nfs, abs_ = one["nu-fission"].get_xs(value="std_dev")[0], one["absorption"].get_xs(value="std_dev")[0]
    k_one = nf / ab
    k_one_sigma = k_one * math.hypot(nfs / nf, abs_ / ab)         # the two are correlated, so this overstates the error
    out.update(k_one_group=float(k_one), k_one_group_sigma=float(k_one_sigma),
               k_one_group_minus_keff_sigma=float((k_one - k.nominal_value) / math.hypot(k_one_sigma, k.std_dev)))
    print(f"total - absorption - scattering, worst group: {out['sum_rule_worst']:.2e} of the total")
    print(f"k-infinity: OpenMC {k.nominal_value:.5f} +/- {k.std_dev:.5f}; one-group nu-fission / absorption {k_one:.5f} +/- "
          f"{k_one_sigma:.5f}")
    print("RESULTS " + json.dumps(out))


if __name__ == "__main__":
    main()
