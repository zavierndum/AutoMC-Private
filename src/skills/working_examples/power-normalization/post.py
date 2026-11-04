"""
Normalize per-source tallies to a power. OpenMC scores everything per source neutron. If the energy released is H joules per source
neutron and the reactor runs at P watts, it makes S = P / H source neutrons per second, and any tally times S is a rate per second.

    python post.py RUN_FOLDER [--power-W 30000]

Prints the steps and one last line `RESULTS {json}` for scripts and tests.
"""
import argparse
import glob
import json
import os

import openmc

JOULE_PER_EV = 1.602176634e-19


def statepoint(folder):
    files = sorted(glob.glob(os.path.join(folder, "statepoint.*.h5")), key=lambda p: int(p.split(".")[-2]))
    if not files:
        raise SystemExit(f"no statepoint in {folder}")
    return files[-1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("run_folder")
    parser.add_argument("--power-W", type=float, default=30e3, help="the reactor's power (the notebook's 30 kW)")
    a = parser.parse_args()
    with openmc.StatePoint(statepoint(a.run_folder)) as sp:
        k = sp.keff
        fission = float(sp.get_tally(name="fission in the fuel").mean.ravel()[0])          # fissions per source neutron
        t = sp.get_tally(name="energy released")
        q_ev = float(t.get_values(scores=["fission-q-recoverable"]).ravel()[0])          # eV per source neutron
        kappa_ev = float(t.get_values(scores=["kappa-fission"]).ravel()[0])
    h_joule = q_ev * JOULE_PER_EV
    source_rate = a.power_W / h_joule                    # source neutrons per second
    fission_rate = fission * source_rate                 # fissions per second
    mev_per_fission = q_ev / fission / 1e6
    out = {"keff": float(k.nominal_value), "keff_sigma": float(k.std_dev), "fission_per_source": fission,
           "energy_per_source_eV": q_ev, "source_per_second": source_rate, "fissions_per_second": fission_rate,
           "MeV_per_fission": mev_per_fission, "kappa_MeV_per_fission": kappa_ev / fission / 1e6,
           "power_check": source_rate * h_joule / a.power_W,
           "power_from_200MeV": fission_rate * 200e6 * JOULE_PER_EV / a.power_W}
    print(f"k-eff = {k.nominal_value:.5f} +/- {k.std_dev:.5f}")
    print(f"per source neutron: {fission:.5f} fissions, {q_ev:.4e} eV released ({h_joule:.4e} J)")
    print(f"at {a.power_W:.0f} W: {source_rate:.4e} source neutrons per second, {fission_rate:.4e} fissions per second")
    print(f"energy per fission: {mev_per_fission:.1f} MeV recoverable (fission-q-recoverable), {out['kappa_MeV_per_fission']:.1f} MeV "
          f"(kappa-fission)")
    print(f"the rough 200 MeV per fission would give {out['power_from_200MeV'] * a.power_W:.0f} W")
    print("RESULTS " + json.dumps(out))


if __name__ == "__main__":
    main()
