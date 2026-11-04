"""
Read a CANDU bundle run: k-eff, and the flux in each pin's fuel grouped by ring, with how evenly the pins of one ring agree.

    python post.py RUN_FOLDER

A distribcell tally has one bin per place the fuel cell appears; the geometry's paths say which pin each bin is in. Prints the rings and
one last line `RESULTS {json}` for scripts and tests.
"""
import argparse
import glob
import json
import os
import re

import numpy as np
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
    model = openmc.Model.from_model_xml(os.path.join(a.run_folder, "model.xml"))
    model.geometry.determine_paths()
    cells = model.geometry.get_all_cells()
    fuel = next(c for c in cells.values() if c.name == "fuel")
    ring_of = {}
    for instance, path in enumerate(fuel.paths):
        pin_id = [int(m) for m in re.findall(r"c(\d+)", path)][-2]          # the pin cell just above the fuel cell
        ring_of[instance] = int(re.search(r"ring (\d+)", cells[pin_id].name).group(1))
    with openmc.StatePoint(statepoint(a.run_folder)) as sp:
        k = sp.keff
        t = sp.get_tally(name="flux in each pin's fuel")
        flux, sd = t.mean.ravel(), t.std_dev.ravel()
    rings = sorted(set(ring_of.values()))
    means, worst = [], 0.0
    print(f"k-eff = {k.nominal_value:.5f} +/- {k.std_dev:.5f}")
    for r in rings:
        idx = [i for i, ring in ring_of.items() if ring == r]
        f, s = flux[idx], sd[idx]
        m = f.mean()
        # how far each pin is from its ring's mean, in its own standard deviations: symmetric rings should stay within a few
        spread = float(np.max(np.abs(f - m) / np.where(s > 0, s, np.inf))) if len(idx) > 1 else 0.0
        worst = max(worst, spread)
        means.append(m)
        print(f"ring {r}: {len(idx):2d} pins, mean flux {m:.4e} per source neutron, pins within {spread:.1f} standard deviations of it")
    rel = [m / means[0] for m in means]
    print("ring means relative to the centre pin: " + ", ".join(f"{x:.3f}" for x in rel))
    out = {"keff": float(k.nominal_value), "keff_sigma": float(k.std_dev), "pins": len(ring_of), "worst_pin_spread_sigma": worst,
           "outer_over_centre": float(rel[-1]), "flux_rises_outward": bool(all(b > a for a, b in zip(means, means[1:])))}
    print("RESULTS " + json.dumps(out))


if __name__ == "__main__":
    main()
