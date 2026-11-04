"""
Check a TRISO packing: how many particles, the packing fraction reached, that every particle lies inside the box and that no two
overlap.

    python post.py CASE_FOLDER

Reads centers.json (written by build.py next to model.xml); no run is needed. Prints the checks and one last line `RESULTS {json}`.
"""
import argparse
import json
import math
import os

import numpy as np


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("case_folder")
    a = parser.parse_args()
    data = json.load(open(os.path.join(a.case_folder, "centers.json"), encoding="utf-8"))
    c = np.asarray(data["centers"])
    r, box = data["radius_cm"], data["box_cm"]
    n = len(c)
    packing = n * 4.0 / 3.0 * math.pi * r ** 3 / box ** 3
    inside = bool(np.all(np.abs(c) <= box / 2 - r + 1e-9))
    try:
        from scipy.spatial import cKDTree
        closest = float(cKDTree(c).query(c, k=2)[0][:, 1].min())
    except ImportError:                                   # without SciPy: the slow way
        d = np.sqrt(((c[:, None, :] - c[None, :, :]) ** 2).sum(-1))
        np.fill_diagonal(d, np.inf)
        closest = float(d.min())
    print(f"{n} particles of radius {r * 1e4:.0f} um in a {box} cm box: packing fraction {packing:.4f} (asked {data['packing_target']})")
    print(f"all inside the box: {inside}; closest centres {closest * 1e4:.1f} um apart (no overlap needs {2 * r * 1e4:.1f} um)")
    out = {"particles": n, "packing_fraction": packing, "all_inside": inside, "no_overlap": closest >= 2 * r - 1e-9,
           "closest_over_diameter": closest / (2 * r)}
    print("RESULTS " + json.dumps(out))


if __name__ == "__main__":
    main()
