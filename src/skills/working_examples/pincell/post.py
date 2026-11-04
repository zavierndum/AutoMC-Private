"""
Read a pin-cell run: k-infinity with its error, and the U-235 reaction rates in the fuel per source neutron, with the capture-to-fission
ratio alpha.

    python post.py RUN_FOLDER

Prints them and one last line `RESULTS {json}` for scripts and tests.
"""
import argparse
import glob
import json
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
    with openmc.StatePoint(statepoint(a.run_folder)) as sp:
        k = sp.keff
        t = sp.get_tally(name="U-235 in the fuel")
        rates = {score: (float(t.get_values(scores=[score]).ravel()[0]), float(t.get_values(scores=[score], value="std_dev").ravel()[0]))
                 for score in t.scores}
    keff, sigma = float(k.nominal_value), float(k.std_dev)
    capture, fission = rates["(n,gamma)"][0], rates["fission"][0]
    alpha = capture / fission
    print(f"k-infinity = {keff:.5f} +/- {sigma:.5f}")
    for score, (v, s) in rates.items():
        print(f"U-235 {score:11s}: {v:.5e} +/- {s:.1e} per source neutron")
    print(f"capture-to-fission ratio of U-235 (alpha): {alpha:.4f}")
    out = {"keff": keff, "keff_sigma": sigma, "u235_fission": fission, "u235_fission_sigma": rates["fission"][1],
           "u235_alpha": alpha}
    print("RESULTS " + json.dumps(out))


if __name__ == "__main__":
    main()
