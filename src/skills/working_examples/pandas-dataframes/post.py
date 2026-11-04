"""
Read the assembly's tallies as pandas DataFrames: the pin-by-pin fission map (both energy groups summed, then pivoted to 17 x 17), the
peak and the lowest pin, the mirror symmetry the boundaries imply, the distributed-cell tally (one row per pin, its place in the
geometry in the index), and how many batches the trigger asked for.

    python post.py RUN_FOLDER [--plot pins.png]

Prints the readings and one last line `RESULTS {json}` for scripts and tests.
"""
import argparse
import glob
import json
import os

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
    parser.add_argument("--plot")
    a = parser.parse_args()
    with openmc.StatePoint(statepoint(a.run_folder)) as sp:
        k, batches = sp.keff, sp.current_batch
        df = sp.get_tally(name="pin rates").get_pandas_dataframe()
        cells = sp.get_tally(name="fuel absorption in each pin").get_pandas_dataframe()
    # the mesh columns are a MultiIndex: ("mesh 1", "x"), ("mesh 1", "y"); flatten them
    df.columns = [c if isinstance(c, str) else (c[1] if c[0].startswith("mesh") else c[0]) for c in df.columns]
    fis = df[df["score"] == "fission"]
    by_pin = fis.groupby(["x", "y"]).agg(mean=("mean", "sum"), var=("std. dev.", lambda s: float((s ** 2).sum())))
    grid = by_pin["mean"].unstack("y").to_numpy()              # grid[x - 1, y - 1]
    sd = np.sqrt(by_pin["var"].unstack("y").to_numpy())
    norm = grid.mean()
    p, psd = grid / norm, sd / norm
    n = grid.shape[0]
    peak = np.unravel_index(np.argmax(p), p.shape)
    low = np.unravel_index(np.argmin(p), p.shape)
    # near a mirror the flux has no slope, so the pins next to the mirrored corner differ by less than their noise: the peak is
    # looked for within two pins of that corner. Mirrors on the left (x = 1) and top (y = n), vacuum on the right and bottom: the
    # map is symmetric across the line from the top-left corner to the bottom-right one, which takes pin (x, y) to (n + 1 - y, n + 1 - x)
    mirror = p[::-1, ::-1].T
    spread = np.abs(p - mirror) / np.sqrt(psd ** 2 + psd[::-1, ::-1].T ** 2)
    thermal_share = float(fis[fis["energy high [eV]"] <= 0.625 + 1e-9]["mean"].sum() / fis["mean"].sum())
    print(f"k-eff = {k.nominal_value:.5f} +/- {k.std_dev:.5f} after {batches} batches (the trigger decides how many)")
    print(f"pin fission rates relative to the mean: peak {p.max():.3f} at pin {peak[0] + 1},{peak[1] + 1}; lowest {p.min():.3f} at "
          f"pin {low[0] + 1},{low[1] + 1}")
    print(f"mirror symmetry: worst pair {spread.max():.1f} standard deviations apart")
    print(f"thermal share of fissions: {thermal_share:.3f}")
    print(f"distributed-cell tally: {len(cells)} rows, one per pin; its index names each pin's place in the geometry")
    if a.plot:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(5.5, 4.5))
        im = ax.imshow(p.T, origin="lower", extent=[0.5, n + 0.5, 0.5, n + 0.5])
        fig.colorbar(im, ax=ax, label="fission rate / mean")
        ax.set_xlabel("pin x")
        ax.set_ylabel("pin y")
        fig.tight_layout()
        fig.savefig(a.plot, dpi=120)
        print(f"map written to {a.plot}")
    out = {"keff": float(k.nominal_value), "keff_sigma": float(k.std_dev), "batches_run": int(batches), "pins_in_map": int(p.size),
           "distribcell_rows": int(len(cells)), "peak": float(p.max()), "lowest": float(p.min()),
           "peak_near_mirrored_corner": bool(peak[0] <= 2 and peak[1] >= n - 3), "lowest_at_open_corner": bool(low == (n - 1, 0)),
           "worst_mirror_sigma": float(spread.max()), "thermal_share_of_fissions": thermal_share}
    print("RESULTS " + json.dumps(out))


if __name__ == "__main__":
    main()
