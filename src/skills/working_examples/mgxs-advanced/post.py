"""
Read the pin's eight-group cross sections: microscopic ones by nuclide and their macroscopic sum, the scattering matrix, chi, a
condensation to two groups, and the tally triggers. Checks:

- macroscopic = the sum over nuclides of (atom density x microscopic), in every group;
- the 8-group fission cross section condensed to 2 groups equals the 2-group one tallied directly (the same events in wider bins);
- chi sums to 1.

    python post.py RUN_FOLDER

Prints them and one last line `RESULTS {json}` for scripts and tests.
"""
import argparse
import glob
import importlib.util
import json
import os

import numpy as np
import openmc
import openmc.mgxs as mgxs


def statepoint(folder):
    files = sorted(glob.glob(os.path.join(folder, "statepoint.*.h5")), key=lambda p: int(p.split(".")[-2]))
    if not files:
        raise SystemExit(f"no statepoint in {folder}")
    return files[-1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("run_folder")
    a = parser.parse_args()
    # build.py defines the cross sections; a run folder holds only the frozen model.xml, so it is in the case folder above it
    here = os.path.join(a.run_folder, "build.py")
    spec = importlib.util.spec_from_file_location("build", here if os.path.exists(here) else os.path.join(a.run_folder, "..", "build.py"))
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    with openmc.StatePoint(statepoint(a.run_folder)) as sp:
        k, batches = sp.keff, sp.current_batch
        cells = [c for c in sp.summary.geometry.get_all_cells().values() if c.fill is not None]
        lib = build.cross_sections(cells)
        for per_cell in lib.values():
            for xs in per_cell.values():
                xs.load_from_statepoint(sp)
    fuel = lib["fuel"]
    nf = fuel["nu-fission"]
    micro = {n: nf.get_xs(nuclides=[n], xs_type="micro").ravel() for n in ("U235", "U238")}
    macro = nf.get_xs(nuclides="sum", xs_type="macro").ravel()
    density = nf.get_nuclide_densities(["U235", "U238"])        # atoms per barn-cm, as the MGXS object sees them
    rebuilt = density[0] * micro["U235"] + density[1] * micro["U238"]
    macro_rule = float(np.max(np.abs(rebuilt - macro) / macro))
    condensed = fuel["fission"].get_condensed_xs(mgxs.EnergyGroups(build.COARSE)).get_xs(nuclides="sum").ravel()
    direct = fuel["fission, 2 groups"].get_xs().ravel()
    condense_rule = float(np.max(np.abs(condensed - direct) / direct))
    chi = fuel["chi"].get_xs().ravel()
    matrix = lib["water"]["nu-scatter matrix"].get_xs().reshape(8, 8)   # [group in, group out]; group 1 is the fastest
    upscatter = float(np.tril(matrix, -1).sum() / matrix.sum())          # out-group above in-group in energy: a lower index
    print(f"k-infinity = {k.nominal_value:.5f} +/- {k.std_dev:.5f} after {batches} batches (the triggers decide how many)")
    print("U-235 nu-fission, microscopic (b), groups 1-8: " + ", ".join(f"{x:.3g}" for x in micro["U235"]))
    print(f"macroscopic = sum of density x microscopic: worst group off by {macro_rule:.1e}")
    print(f"8 groups condensed to 2 against 2 tallied directly: worst group off by {condense_rule:.1e}")
    print("chi, groups 1-8: " + ", ".join(f"{x:.4f}" for x in chi) + f" (sum {chi.sum():.6f})")
    print(f"water: share of scattering that goes up in energy (thermal upscatter) {upscatter:.4f}")
    out = {"keff": float(k.nominal_value), "keff_sigma": float(k.std_dev), "batches_run": int(batches), "macro_rule_worst": macro_rule,
           "condense_rule_worst": condense_rule, "chi_sum": float(chi.sum()), "chi_top_group": float(chi[0]),
           "u235_nu_fission_barns_group_7": float(micro["U235"][6]), "water_upscatter_share": upscatter}
    print("RESULTS " + json.dumps(out))


if __name__ == "__main__":
    main()
