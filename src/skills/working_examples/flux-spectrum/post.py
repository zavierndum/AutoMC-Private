"""
Read the pin cell's flux spectra: divide each bin by its lethargy width (ln of the ratio of its edges) so that grids of any bin sizes
compare, find the thermal peak in the water, the dip of U-238's 6.67 eV resonance in the fuel, and the thermal share of each region's
flux.

    python post.py RUN_FOLDER [--plot spectra.png]

Prints them and one last line `RESULTS {json}` for scripts and tests.
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


def spectrum(t, cell_id):
    """(edges, flux per bin, flux per unit lethargy) of one cell of a spectrum tally."""
    edges = t.find_filter(openmc.EnergyFilter).values
    flux = t.get_values(filters=[openmc.CellFilter], filter_bins=[(cell_id,)]).ravel()
    lethargy = np.log(edges[1:] / np.where(edges[:-1] > 0, edges[:-1], edges[1] * 1e-3))   # SHEM starts at 0 eV
    return edges, flux, flux / lethargy


def around(edges, per_u, energy):
    i = int(np.searchsorted(edges, energy) - 1)
    return per_u[i]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("run_folder")
    parser.add_argument("--plot")
    a = parser.parse_args()
    with openmc.StatePoint(statepoint(a.run_folder)) as sp:
        k = sp.keff
        even, shem = sp.get_tally(name="spectrum, equal lethargy"), sp.get_tally(name="spectrum, SHEM-361")
        ids = list(even.find_filter(openmc.CellFilter).bins)
        names = {c.id: c.name for c in sp.summary.geometry.get_all_cells().values()}
        spectra = {names[i]: {"even": spectrum(even, i), "shem": spectrum(shem, i)} for i in ids}
    out = {"keff": float(k.nominal_value), "keff_sigma": float(k.std_dev)}
    for name, s in spectra.items():
        e, f, _ = s["even"]
        out[f"thermal_share_{name.lower()}"] = float(f[e[1:] <= 0.625].sum() / f.sum())
        out[f"grids_agree_{name.lower()}"] = float(s["shem"][1].sum() / f.sum())
    e, f, per_u = spectra["Water"]["even"]
    thermal = e[1:] < 1.0
    centres = np.sqrt(e[1:] * e[:-1])
    out["thermal_peak_eV"] = float(centres[thermal][np.argmax(per_u[thermal])])
    se, _, sper = spectra["Fuel"]["shem"]
    dip = around(se, sper, 6.67) / (0.5 * (around(se, sper, 5.0) + around(se, sper, 9.0)))
    out["u238_6_67eV_dip"] = float(dip)
    print(f"k-infinity = {k.nominal_value:.5f} +/- {k.std_dev:.5f}")
    print(f"thermal peak of the water's flux per unit lethargy: {out['thermal_peak_eV']:.4f} eV (2kT at 293.6 K: 0.0506 eV)")
    print(f"fuel flux at 6.67 eV against 5 and 9 eV (SHEM-361): {dip:.3f}, the self-shielding of U-238's first big resonance")
    print(f"flux below 0.625 eV: {out['thermal_share_fuel']:.3f} of the fuel's, {out['thermal_share_water']:.3f} of the water's")
    print(f"total flux, SHEM-361 grid over equal-lethargy grid: fuel {out['grids_agree_fuel']:.6f}, water {out['grids_agree_water']:.6f}")
    if a.plot:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(8, 4.5))
        for name, s in spectra.items():
            e, _, pu = s["even"]
            ax.step(e[:-1], pu, where="post", label=f"{name}, 500 equal-lethargy bins")
            se, _, spu = s["shem"]
            ax.step(se[1:-1], spu[1:], where="post", lw=0.7, label=f"{name}, SHEM-361")
        ax.set_xscale("log")
        ax.set_yscale("log")
        ax.set_xlabel("energy (eV)")
        ax.set_ylabel("flux per unit lethargy per source neutron")
        ax.legend(fontsize=8)
        fig.tight_layout()
        fig.savefig(a.plot, dpi=120)
        print(f"plot written to {a.plot}")
    print("RESULTS " + json.dumps(out))


if __name__ == "__main__":
    main()
