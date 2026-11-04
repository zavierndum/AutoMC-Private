"""
Compare the CE run of the rodded assembly with its two multigroup runs (comparison.json, written by run.py): the bias of each library
in pcm and in standard deviations.

    python post.py RUN_FOLDER

Prints them and one last line `RESULTS {json}` for scripts and tests.
"""
import argparse
import json
import math
import os


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("run_folder")
    a = parser.parse_args()
    c = json.load(open(os.path.join(a.run_folder, "comparison.json"), encoding="utf-8"))
    ce = c["ce"]
    out = {"ce_keff": ce["keff"], "ce_keff_sigma": ce["sigma"]}
    print(f"CE k-infinity {ce['keff']:.5f} +/- {ce['sigma']:.5f}")
    for name in ("isotropic", "angle-dependent"):
        mg = c[name]
        bias = 1e5 * (mg["keff"] - ce["keff"])
        sigma = 1e5 * math.hypot(mg["sigma"], ce["sigma"])
        key = name.replace("-", "_")
        out.update({f"{key}_keff": mg["keff"], f"{key}_bias_pcm": bias, f"{key}_bias_sigma": bias / sigma})
        print(f"MG, {name} library: {mg['keff']:.5f} +/- {mg['sigma']:.5f}, {bias:+.0f} pcm ({bias / sigma:+.1f} sigma)")
    out["angle_reduces_bias"] = abs(out["angle_dependent_bias_pcm"]) < abs(out["isotropic_bias_pcm"])
    print("RESULTS " + json.dumps(out))


if __name__ == "__main__":
    main()
