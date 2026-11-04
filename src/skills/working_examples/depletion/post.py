"""
Read a depletion (depletion_results.h5): k against time, U-235 and Xe-135 in the fuel, and two consistency checks: the fission
power at every step equals the power asked for (the operator scales its rates so), and the U-235 destroyed equals the U-235 fissioned
(the teaching chain gives U-235 no capture; with a full chain the ratio would be about 1 + alpha).

    python post.py RUN_FOLDER [--plot depletion.png]

Prints them and one last line `RESULTS {json}` for scripts and tests.
"""
import argparse
import json
import os

import numpy as np
import openmc
import openmc.deplete

JOULE_PER_EV = 1.602176634e-19


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("run_folder")
    parser.add_argument("--plot")
    a = parser.parse_args()
    results = openmc.deplete.Results(os.path.join(a.run_folder, "depletion_results.h5"))
    days, k = results.get_keff(time_units="d")
    fuel = next(m for m in openmc.Model.from_model_xml(os.path.join(a.run_folder, "model.xml")).materials if m.depletable)
    mat = str(fuel.id)
    _, u235 = results.get_atoms(mat, "U235")
    _, xe135 = results.get_atoms(mat, "Xe135")
    # the power the integrator held (the OPTIONS of the build.py copied into the run) and the chain's energy per fission
    import importlib.util
    spec = importlib.util.spec_from_file_location("build", os.path.join(a.run_folder, "build.py"))
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    watts = build.OPTIONS["power_W"]
    chain = openmc.deplete.Chain.from_xml(os.path.join(a.run_folder, "chain_used.xml"))
    # eV per fission of every nuclide the chain lets fission (U-234 too: leaving it out misses about 1e-4 of the power)
    q = {n.name: r.Q for n in chain.nuclides for r in n.reactions if r.type == "fission"}
    def start_rate(step, nuc):
        """Fissions per second of `nuc` at the start of `step` (the final time point holds no rates: nothing follows it)."""
        r = results[step]
        return r.rates.get(mat, nuc, "fission") * r[mat, nuc]
    steps = len(days) - 1
    rate = {n: np.array([start_rate(i, n) for i in range(steps)]) for n in q}
    power = sum(rate[n] * q[n] for n in q) * JOULE_PER_EV                       # watts at the start of each step
    dt = np.diff(days) * 86400.0
    fissioned = float(np.sum(rate["U235"] * dt))            # each step uses the rates at its start (the predictor's assumption)
    destroyed_over_fissioned = (u235[0] - u235[-1]) / fissioned
    share = float(rate["U235"][0] / (rate["U235"][0] + rate["U238"][0]))
    print("day      k-eff")
    for d, (kk, ss) in zip(days, k):
        print(f"{d:5.0f}  {kk:.5f} +/- {ss:.5f}")
    print(f"U-235: {u235[0]:.4e} -> {u235[-1]:.4e} atoms ({100 * (1 - u235[-1] / u235[0]):.2f}% burned)")
    print(f"Xe-135: {xe135[1]:.3e} atoms after the first step, {xe135[-1]:.3e} at the end (equilibrium within days)")
    print(f"fission power at each step / the power asked for: {', '.join(f'{x:.6f}' for x in power / watts)}")
    print(f"U-235 destroyed / U-235 fissioned: {destroyed_over_fissioned:.4f} (the chain has no U-235 capture); "
          f"U-235's share of fissions at the start {share:.3f}")
    if a.plot:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(1, 2, figsize=(10, 3.8))
        ax[0].errorbar(days, k[:, 0], yerr=k[:, 1], marker="o")
        ax[0].set_xlabel("day")
        ax[0].set_ylabel("k-infinity")
        ax[1].plot(days, u235 / u235[0], marker="o", label="U-235 / start")
        ax2 = ax[1].twinx()
        ax2.plot(days, xe135, marker="s", color="C1", label="Xe-135 atoms")
        ax[1].set_xlabel("day")
        ax[1].legend(loc="lower left")
        ax2.legend(loc="lower right")
        fig.tight_layout()
        fig.savefig(a.plot, dpi=120)
        print(f"plot written to {a.plot}")
    diffs = np.diff(k[:, 0])
    out = {"steps": int(len(days) - 1), "days": float(days[-1]), "k_start": float(k[0, 0]), "k_start_sigma": float(k[0, 1]),
           "k_end": float(k[-1, 0]), "k_end_sigma": float(k[-1, 1]),
           "k_drop_sigma": float((k[0, 0] - k[-1, 0]) / np.hypot(k[0, 1], k[-1, 1])),
           "u235_burned_fraction": float(1 - u235[-1] / u235[0]), "u235_falls_every_step": bool(np.all(np.diff(u235) < 0)),
           "xe135_start": float(xe135[0]), "xe135_end_over_first_step": float(xe135[-1] / xe135[1]),
           "power_ratio_worst": float(np.max(np.abs(power / watts - 1.0))),
           "u235_destroyed_over_fissioned": float(destroyed_over_fissioned), "u235_share_of_fissions": share,
           "k_rises_steps": int((diffs > 0).sum())}
    print("RESULTS " + json.dumps(out))


if __name__ == "__main__":
    main()
