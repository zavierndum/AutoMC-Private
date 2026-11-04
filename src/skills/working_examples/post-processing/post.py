"""
Read the pin-cell statepoint the way most OpenMC analyses start: k-eff; a mesh tally as NumPy arrays (mean, standard deviation,
reshaped to the mesh); the relative errors; and the source bank (where the last batch's fission neutrons were born, their energies).

    python post.py RUN_FOLDER [--plot maps.png]

Prints the readings and one last line `RESULTS {json}` for scripts and tests.
"""
import argparse
import glob
import json
import os

import numpy as np
import openmc

R_FUEL = 0.39218


def statepoint(folder):
    files = sorted(glob.glob(os.path.join(folder, "statepoint.*.h5")), key=lambda p: int(p.split(".")[-2]))
    if not files:
        raise SystemExit(f"no statepoint in {folder}")
    return files[-1]


def touching_bins(nx, lower, width, radius):
    """How many mesh cells overlap a circle of `radius` at the origin: those whose nearest point to the origin is inside it."""
    edges = lower + width * np.arange(nx + 1)
    lo, hi = edges[:-1], edges[1:]
    near = np.where(lo > 0, lo, np.where(hi < 0, -hi, 0.0))           # distance from 0 to the nearest point of each interval
    return int(((near[:, None] ** 2 + near[None, :] ** 2) < radius ** 2).sum())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("run_folder")
    parser.add_argument("--plot")
    a = parser.parse_args()
    with openmc.StatePoint(statepoint(a.run_folder)) as sp:
        k = sp.keff
        t = sp.get_tally(name="flux and fission on the mesh")
        mesh = t.find_filter(openmc.MeshFilter).mesh
        nx, ny = mesh.dimension
        # a tally's arrays have the shape (filter bins, nuclides, scores); a mesh filter's bins run x fastest
        flux = t.get_values(scores=["flux"]).reshape(ny, nx)
        flux_sd = t.get_values(scores=["flux"], value="std_dev").reshape(ny, nx)
        fission = t.get_values(scores=["fission"]).reshape(ny, nx)
        bank = sp.source                                                  # a NumPy structured array: r, u, E, wgt, ...
    rel = np.divide(flux_sd, flux, out=np.zeros_like(flux), where=flux > 0)
    width = mesh.width[0]
    touching = touching_bins(nx, mesh.lower_left[0], width, R_FUEL)
    with_fission = int((fission > 0).sum())
    r_born = np.hypot(bank["r"]["x"], bank["r"]["y"])
    e_mev = bank["E"] / 1e6
    print(f"k-infinity = {k.nominal_value:.5f} +/- {k.std_dev:.5f}")
    print(f"mesh {nx} x {ny}: flux relative error median {np.median(rel):.4f}, largest {rel.max():.4f}")
    print(f"cells with fission: {with_fission}; cells that overlap the pellet: {touching}")
    print(f"source bank: {len(bank)} sites, all inside the pellet: {bool((r_born <= R_FUEL + 1e-9).all())}, "
          f"mean energy {e_mev.mean():.3f} MeV")
    if a.plot:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(1, 3, figsize=(13, 4))
        ext = [mesh.lower_left[0], mesh.upper_right[0], mesh.lower_left[1], mesh.upper_right[1]]
        for axis, data, title in ((ax[0], flux, "flux"), (ax[1], fission, "fission")):
            im = axis.imshow(data, origin="lower", extent=ext)
            axis.set_title(title)
            fig.colorbar(im, ax=axis)
        ax[2].hist(e_mev, bins=np.logspace(-3, 1.3, 60))
        ax[2].set_xscale("log")
        ax[2].set_xlabel("birth energy (MeV)")
        fig.tight_layout()
        fig.savefig(a.plot, dpi=110)
        print(f"plots written to {a.plot}")
    out = {"keff": float(k.nominal_value), "keff_sigma": float(k.std_dev), "fission_cells": with_fission, "touching_cells": touching,
           "fission_cells_over_touching": with_fission / touching, "flux_rel_err_median": float(np.median(rel)),
           "source_sites": int(len(bank)), "source_in_pellet": bool((r_born <= R_FUEL + 1e-9).all()),
           "source_mean_energy_MeV": float(e_mev.mean())}
    print("RESULTS " + json.dumps(out))


if __name__ == "__main__":
    main()
