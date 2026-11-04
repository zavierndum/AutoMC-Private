"""
Read a criticality search (search.json, written by run.py): the critical boron concentration, every guess with its k, whether the
bracket held the answer, and the boron worth from a straight-line fit of k against the concentration.

    python post.py RUN_FOLDER [--plot k_vs_boron.png]

Prints them and one last line `RESULTS {json}` for scripts and tests.
"""
import argparse
import json
import os

import numpy as np


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("run_folder")
    parser.add_argument("--plot")
    a = parser.parse_args()
    s = json.load(open(os.path.join(a.run_folder, "search.json"), encoding="utf-8"))
    ppm, k, sig = np.array(s["guesses_ppm"]), np.array(s["keff"]), np.array(s["keff_sigma"])
    for i, (x, kk, ss) in enumerate(zip(ppm, k, sig), 1):
        print(f"run {i}: {x:7.1f} ppm  k = {kk:.5f} +/- {ss:.5f}")
    # k is close to linear in boron at these concentrations (a 1/v absorber, thinly spread): a weighted straight line
    slope, intercept = np.polyfit(ppm, k, 1, w=1.0 / sig)
    worth = slope * 1e5                                               # pcm per ppm
    fit_critical = (1.0 - intercept) / slope
    near = int(np.argmin(np.abs(ppm - s["critical_ppm"])))
    lo, hi = int(np.argmin(ppm)), int(np.argmax(ppm))
    bracketed = bool(k[lo] > 1.0 > k[hi])
    print(f"critical boron: {s['critical_ppm']:.1f} ppm by weight (the search); the straight-line fit crosses k = 1 at {fit_critical:.1f}")
    print(f"boron worth: {worth:.2f} pcm per ppm; the bracket {'held' if bracketed else 'did NOT hold'} the answer")
    if a.plot:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(7, 4))
        ax.errorbar(ppm, k, yerr=sig, fmt="o", label="each run")
        xs = np.linspace(ppm.min(), ppm.max(), 50)
        ax.plot(xs, slope * xs + intercept, label=f"fit: {worth:.1f} pcm/ppm")
        ax.axhline(1.0, color="grey", lw=0.8)
        ax.set_xlabel("boron (ppm by weight)")
        ax.set_ylabel("k-infinity")
        ax.legend()
        fig.tight_layout()
        fig.savefig(a.plot, dpi=120)
        print(f"plot written to {a.plot}")
    out = {"critical_ppm": s["critical_ppm"], "runs": len(ppm), "bracketed": bracketed, "boron_worth_pcm_per_ppm": worth,
           "fit_critical_ppm": fit_critical, "k_nearest": float(k[near]), "k_nearest_sigma": float(sig[near])}
    print("RESULTS " + json.dumps(out))


if __name__ == "__main__":
    main()
