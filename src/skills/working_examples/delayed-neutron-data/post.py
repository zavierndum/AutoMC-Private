"""
Read the delayed-neutron data: beta and the decay constants by delayed group (summed over nuclides, and per nuclide for beta), the mean
energies of the prompt and delayed fission spectra, and two exact checks: prompt + delayed nu-fission = nu-fission, and each chi sums
to 1.

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
        k = sp.keff
        cell = next(c for c in sp.summary.geometry.get_all_cells().values() if c.name == "medium")
        xs = build.cross_sections(cell)
        for x in xs.values():
            x.load_from_statepoint(sp)
    # by nuclide, Beta and DecayRate give each nuclide's own values; nuclides="sum" would add them up, which is not the mixture's
    nuclides = ("U235", "U238", "Pu239")
    by_nuclide = {n: xs["beta"].get_xs(nuclides=[n]).ravel() for n in nuclides}          # per delayed group
    lam = {n: xs["decay rate"].get_xs(nuclides=[n]).ravel() for n in nuclides}          # 1/s, per delayed group
    edges = xs["chi prompt"].energy_groups.group_edges
    mid = np.sqrt(edges[1:] * edges[:-1])[::-1]                          # group 1 is the highest energy
    chi_p, chi_d = xs["chi prompt"].get_xs().ravel(), xs["chi delayed"].get_xs().ravel()
    if chi_d.size != chi_p.size:                                         # chi delayed may carry a delayed-group axis: sum it
        chi_d = chi_d.reshape(-1, chi_p.size).sum(axis=0)
    mean_p, mean_d = float((chi_p * mid).sum() / chi_p.sum()), float((chi_d * mid).sum() / chi_d.sum())
    prompt, total = xs["prompt nu-fission"].get_xs()[0], xs["nu-fission"].get_xs()[0]
    per_group = xs["delayed nu-fission"].get_xs().ravel()                 # the mixture's delayed nu-fission, per delayed group
    delayed = float(per_group.sum())
    beta = per_group / total                                             # the mixture's beta: delayed over all neutrons from fission
    out = {"keff": float(k.nominal_value), "keff_sigma": float(k.std_dev), "beta_total": float(beta.sum()),
           **{f"beta_{n}": float(by_nuclide[n].sum()) for n in nuclides},
           "decay_constants_U235": [float(x) for x in lam["U235"]], "decay_constants_rise": bool(np.all(np.diff(lam["U235"]) > 0)),
           "lambda_1_U235": float(lam["U235"][0]), "lambda_6_U235": float(lam["U235"][-1]),
           "chi_prompt_sum": float(chi_p.sum()), "chi_delayed_sum": float(chi_d.sum()),
           "chi_mean_MeV_prompt": mean_p / 1e6, "chi_mean_MeV_delayed": mean_d / 1e6,
           "prompt_plus_delayed_over_total": float((prompt + delayed) / total)}
    print(f"k-infinity = {k.nominal_value:.5f} +/- {k.std_dev:.5f}")
    print("the mixture's beta by delayed group: " + ", ".join(f"{b:.2e}" for b in beta) + f"; total {beta.sum():.5f}")
    print("each nuclide's own beta: " + ", ".join(f"{n} {by_nuclide[n].sum():.5f}" for n in nuclides))
    print("U-235's decay constants (1/s): " + ", ".join(f"{x:.4g}" for x in lam["U235"]))
    print(f"mean energy of the fission spectrum: prompt {mean_p / 1e6:.2f} MeV, delayed {mean_d / 1e6:.2f} MeV")
    print(f"(prompt + delayed nu-fission) / nu-fission = {out['prompt_plus_delayed_over_total']:.9f}")
    print("RESULTS " + json.dumps(out))


if __name__ == "__main__":
    main()
