"""
Read a run of the slab example: the fluence profile along the beam, total and fast (above 0.1 MeV), and how fast it falls.

    python post.py RUN_FOLDER [--plot profile.png]

Prints the profile and one last line `RESULTS {json}` for scripts and tests.
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
    parser.add_argument("--plot", help="write the profile as a PNG")
    a = parser.parse_args()
    model = openmc.Model.from_model_xml(os.path.join(a.run_folder, "model.xml"))
    interface = next(c for c in model.geometry.get_all_cells().values() if c.name == "concrete slab")
    with openmc.StatePoint(statepoint(a.run_folder)) as sp:
        total = sp.get_tally(name="fluence profile")
        fast = sp.get_tally(name="fast fluence profile")
        mesh = total.find_filter(openmc.MeshFilter).mesh
        phi, phi_sd = total.mean.ravel(), total.std_dev.ravel()
        phi_fast, phi_fast_sd = fast.mean.ravel(), fast.std_dev.ravel()
    edges = np.linspace(mesh.lower_left[0], mesh.upper_right[0], mesh.dimension[0] + 1)
    centres = (edges[:-1] + edges[1:]) / 2
    water_end = min(s.x0 for s in interface.region.get_surfaces().values() if isinstance(s, openmc.XPlane) and s.x0 > 0)
    in_concrete = centres > water_end
    # every bin's volume is the same, so the mesh flux (track length per source neutron) compares bin to bin as it is
    print(f"{'x (cm)':>8} {'slab':>9} {'fluence':>12} {'+/-':>9} {'fast':>12}")
    for x, f, s, ff in zip(centres, phi, phi_sd, phi_fast):
        print(f"{x:8.2f} {'concrete' if x > water_end else 'water':>9} {f:12.4e} {s:9.2e} {ff:12.4e}")
    fast_conc = phi_fast[in_concrete]
    relax = None
    if len(fast_conc) > 2 and np.all(fast_conc > 0):
        slope = np.polyfit(centres[in_concrete], np.log(fast_conc), 1)[0]
        relax = -1.0 / slope                      # cm per e-fold of the fast fluence in the concrete
        print(f"fast fluence in the concrete falls by e every {relax:.2f} cm")
    out = {"first_bin": float(phi[0]), "first_bin_sigma": float(phi_sd[0]), "last_bin": float(phi[-1]), "last_bin_sigma": float(phi_sd[-1]),
           "fast_last_bin": float(phi_fast[-1]), "fast_last_bin_sigma": float(phi_fast_sd[-1]),
           "fast_falls_through_concrete": bool(np.all(np.diff(fast_conc) < 0)), "fast_relaxation_cm": relax,
           "total_track_length": float(phi.sum()), "total_track_length_sigma": float(math.sqrt((phi_sd ** 2).sum()))}
    if a.plot:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots()
        ax.errorbar(centres, phi, yerr=phi_sd, fmt="o-", ms=3, label="all energies")
        ax.plot(centres, phi_fast, "s--", ms=3, label="above 0.1 MeV")
        ax.axvline(water_end, color="gray", lw=0.8)
        ax.set(xlabel="depth (cm)", ylabel="fluence per source neutron (cm per bin)", yscale="log", title="water | concrete")
        ax.legend()
        fig.savefig(a.plot, dpi=130, bbox_inches="tight")
        print(f"profile drawn in {a.plot}")
    print("RESULTS " + json.dumps(out))


if __name__ == "__main__":
    main()
