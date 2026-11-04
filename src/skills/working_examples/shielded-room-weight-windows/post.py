"""
Compare the analog and weight-window runs of the shielded room: how much of the building each run reached (mesh cells with any flux),
the relative errors, the figure of merit 1 / (R^2 T) (R a typical relative error, T the run's time; it does not depend on how long one
runs, so it measures the method), and whether the two agree where both have results (weight windows change the noise, not the answer).

    python post.py RUN_FOLDER [--plot flux_maps.png]

Prints the comparison and one last line `RESULTS {json}` for scripts and tests.
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
    d = np.load(os.path.join(a.run_folder, "comparison.npz"))
    info = json.load(open(os.path.join(a.run_folder, "comparison.json"), encoding="utf-8"))
    am, asd, wm, wsd = d["analog_mean"], d["analog_sd"], d["windows_mean"], d["windows_sd"]
    reached = lambda m: float((m > 0).mean())
    both = (am > 0) & (wm > 0)
    rel = lambda m, s: s[both] / m[both]
    r_a, r_w = float(np.median(rel(am, asd))), float(np.median(rel(wm, wsd)))
    fom_a, fom_w = 1.0 / (r_a ** 2 * info["analog_s"]), 1.0 / (r_w ** 2 * info["windows_s"])
    # where both runs are well converged (relative errors under 10%), the fluxes should agree within their errors
    good = both & (asd < 0.1 * am) & (wsd < 0.1 * wm)
    z = (wm[good] - am[good]) / np.hypot(asd[good], wsd[good])
    out = {"reached_analog": reached(am), "reached_windows": reached(wm), "reached_ratio": reached(wm) / reached(am),
           "analog_s": info["analog_s"], "windows_s": info["windows_s"], "median_rel_err_analog": r_a, "median_rel_err_windows": r_w,
           "fom_analog": fom_a, "fom_windows": fom_w, "fom_ratio": fom_w / fom_a, "agreeing_cells": int(good.sum()),
           "agreement_mean_z": float(z.mean()) if good.any() else None,
           "agreement_within_3_sigma": float((np.abs(z) < 3).mean()) if good.any() else None}
    print(f"mesh cells reached: analog {out['reached_analog']:.3f}, with weight windows {out['reached_windows']:.3f}")
    print(f"median relative error where both reached: analog {r_a:.3f}, with weight windows {r_w:.3f}")
    print(f"run times: analog {info['analog_s']:.1f} s ({info['analog_particles']} x {info['batches']}), weight windows "
          f"{info['windows_s']:.1f} s ({info['windows_particles']} x {info['batches']})")
    print(f"figure of merit 1/(R^2 T) over the cells both reached: analog {fom_a:.3g}, with weight windows {fom_w:.3g} "
          f"(x{fom_w / fom_a:.2f}; these cells are mostly near the source, where analog sampling already does well)")
    if good.any():
        print(f"{good.sum()} cells well converged in both: mean difference {z.mean():+.2f} sigma, "
              f"{100 * out['agreement_within_3_sigma']:.0f}% within 3 sigma")
    if a.plot:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.colors import LogNorm
        n = info["mesh"]
        fig, ax = plt.subplots(1, 2, figsize=(11, 5))
        for axis, m, title in ((ax[0], am, "analog"), (ax[1], wm, "weight windows")):
            img = np.where(m > 0, m, np.nan).reshape(n, n)
            im = axis.imshow(img, origin="lower", norm=LogNorm())
            axis.set_title(title)
            fig.colorbar(im, ax=axis)
        fig.tight_layout()
        fig.savefig(a.plot, dpi=110)
        print(f"maps written to {a.plot}")
    print("RESULTS " + json.dumps(out))


if __name__ == "__main__":
    main()
