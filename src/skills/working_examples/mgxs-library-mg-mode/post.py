"""
Compare the continuous-energy and multigroup runs of the assembly (comparison.json, written by run.py): both eigenvalues, the MG bias
in pcm and in standard deviations, and what the MG file holds.

    python post.py RUN_FOLDER

Prints them and one last line `RESULTS {json}` for scripts and tests.
"""
import argparse
import json
import math
import os

import h5py


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("run_folder")
    a = parser.parse_args()
    c = json.load(open(os.path.join(a.run_folder, "comparison.json"), encoding="utf-8"))
    bias = 1e5 * (c["mg_keff"] - c["ce_keff"])
    sigma = 1e5 * math.hypot(c["ce_keff_sigma"], c["mg_keff_sigma"])
    with h5py.File(os.path.join(a.run_folder, "mgxs.h5"), "r") as f:
        groups = int(f.attrs["energy_groups"])
        data_sets = sorted(k for k in f.keys())
    out = {"ce_keff": c["ce_keff"], "ce_keff_sigma": c["ce_keff_sigma"], "mg_keff": c["mg_keff"], "mg_keff_sigma": c["mg_keff_sigma"],
           "bias_pcm": bias, "bias_sigma": bias / sigma, "energy_groups": groups, "data_sets": data_sets}
    print(f"CE k-infinity {c['ce_keff']:.5f} +/- {c['ce_keff_sigma']:.5f}; MG {c['mg_keff']:.5f} +/- {c['mg_keff_sigma']:.5f}")
    print(f"MG minus CE: {bias:+.0f} pcm ({bias / sigma:+.1f} combined standard deviations)")
    print(f"mgxs.h5: {groups} groups, data sets {', '.join(data_sets)}")
    print("RESULTS " + json.dumps(out))


if __name__ == "__main__":
    main()
