"""
Read a criticality run: k-eff with its error, how it compares with a benchmark value, and whether the fission source had converged
(the Shannon entropy over the batches).

    python post.py RUN_FOLDER [--benchmark 1.0000 --benchmark-sigma 0.0020]

Prints the comparison and one last line `RESULTS {json}` for scripts and tests. The same reading works for any eigenvalue run.
"""
import argparse
import glob
import json
import math
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
    parser.add_argument("--benchmark", type=float, default=1.0000, help="the benchmark's k-eff (Jezebel: 1.0000)")
    parser.add_argument("--benchmark-sigma", type=float, default=0.0020, help="its uncertainty (Jezebel: 0.0020)")
    a = parser.parse_args()
    with openmc.StatePoint(statepoint(a.run_folder)) as sp:
        k = sp.keff
        n_inactive = sp.n_inactive
        entropy = np.asarray(sp.entropy) if sp.entropy is not None and len(sp.entropy) else None
    keff, sigma = float(k.nominal_value), float(k.std_dev)
    combined = math.hypot(sigma, a.benchmark_sigma)
    diff_pcm = (keff - a.benchmark) * 1.0e5
    print(f"k-eff = {keff:.5f} +/- {sigma:.5f}")
    print(f"benchmark {a.benchmark:.4f} +/- {a.benchmark_sigma:.4f}: difference {diff_pcm:+.0f} pcm "
          f"({(keff - a.benchmark) / combined:+.2f} combined standard deviations)")
    out = {"keff": keff, "keff_sigma": sigma, "difference_pcm": diff_pcm}
    if entropy is not None and len(entropy) > n_inactive:
        active = entropy[n_inactive:]
        drift = abs(active[: len(active) // 2].mean() - active[len(active) // 2:].mean())
        spread = active.std()
        converged = bool(drift < 2 * spread / math.sqrt(max(len(active) // 2, 1)) + 1e-3 or drift < 0.05 * active.mean())
        print(f"Shannon entropy over the active batches: {active.mean():.3f} +/- {spread:.3f}; first half against second half differs by "
              f"{drift:.3f} ({'converged' if converged else 'still drifting: use more inactive batches'})")
        out.update(entropy_mean=float(active.mean()), source_converged=converged)
    print("RESULTS " + json.dumps(out))


if __name__ == "__main__":
    main()
