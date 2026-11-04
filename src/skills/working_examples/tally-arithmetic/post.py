"""
The six-factor formula by tally arithmetic: k = (fast fission factor) x (resonance escape) x (thermal utilization) x (eta) x (fast and
thermal non-leakage), each factor a ratio of tallies, with its uncertainty carried by OpenMC's derived tallies. Also a microscopic
cross section from a reaction rate and a flux.

    python post.py RUN_FOLDER

Prints the factors and one last line `RESULTS {json}` for scripts and tests.
"""
import argparse
import glob
import json
import math
import os

import openmc


def statepoint(folder):
    files = sorted(glob.glob(os.path.join(folder, "statepoint.*.h5")), key=lambda p: int(p.split(".")[-2]))
    if not files:
        raise SystemExit(f"no statepoint in {folder}")
    return files[-1]


def value(t):
    """(mean, standard deviation) of a one-bin tally, derived or not."""
    return float(t.mean.ravel()[0]), float(t.std_dev.ravel()[0])


def net_out(t):
    """Net current out of the mesh cell: the 'out' bins minus the 'in' bins of its mesh-surface filter (on the mirrored sides the
    two cancel; at the vacuum ends nothing comes back in)."""
    f = t.find_filter(openmc.MeshSurfaceFilter)
    outs = tuple(b for b in f.bins if b[-1].endswith("out"))
    ins = tuple(b for b in f.bins if b[-1].endswith(" in"))
    total = lambda bins: t.get_slice(filters=[openmc.MeshSurfaceFilter], filter_bins=[bins]).summation(
        filter_type=openmc.MeshSurfaceFilter, remove_filter=True)
    return total(outs) - total(ins)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("run_folder")
    a = parser.parse_args()
    with openmc.StatePoint(statepoint(a.run_folder)) as sp:
        k = sp.keff
        get = sp.get_tally
        production, absorption = get(name="production"), get(name="absorption")
        leak, thermal_leak = net_out(get(name="leakage")), net_out(get(name="thermal leakage"))
        th_abs, th_prod = get(name="thermal absorption"), get(name="thermal production")
        fuel_th_abs = get(name="fuel thermal absorption")
        # k from the balance: neutrons made per neutron lost (absorbed or leaked)
        k_balance = production / (absorption + leak)
        factors = {
            "fast_fission": production / th_prod,                                   # epsilon: all production over thermal production
            "resonance_escape": (th_abs + thermal_leak) / (absorption + thermal_leak),   # p: reaching thermal energies
            "thermal_utilization": fuel_th_abs / th_abs,                             # f: thermal absorptions that are in the fuel
            "eta": th_prod / fuel_th_abs,                                            # eta: neutrons made per thermal absorption in fuel
            "fast_non_leakage": (absorption + thermal_leak) / (absorption + leak),
            "thermal_non_leakage": th_abs / (th_abs + thermal_leak),
        }
        product = 1.0
        for f in factors.values():
            product = product * f
        # a microscopic cross section: U-235's thermal fission rate in the fuel over the fuel's thermal flux, per U-235 atom
        rates, flux = get(name="fuel reaction rates"), get(name="flux by group")
        fuel_id = rates.find_filter(openmc.CellFilter).bins[0]
        th = ((0.0, 0.625),)
        f235 = rates.get_values(nuclides=["U235"], scores=["fission"], filters=[openmc.EnergyFilter], filter_bins=[th]).ravel()[0]
        phi = flux.get_values(filters=[openmc.CellFilter, openmc.EnergyFilter], filter_bins=[(fuel_id,), th]).ravel()[0]
    kb, kb_s = value(k_balance)
    pr, pr_s = value(product)
    out = {"keff": float(k.nominal_value), "keff_sigma": float(k.std_dev), "k_balance": kb, "k_balance_sigma": kb_s,
           "six_factor_product": pr, "product_over_balance": pr / kb, "leakage_per_absorption": value(leak)[0] / value(absorption)[0]}
    print(f"k-eff (OpenMC's estimate) = {k.nominal_value:.5f} +/- {k.std_dev:.5f}")
    print(f"k from production / (absorption + leakage) = {kb:.5f} +/- {kb_s:.5f}")
    for name, f in factors.items():
        m, s = value(f)
        out[name], out[name + "_sigma"] = m, s
        print(f"  {name:20s} {m:.5f} +/- {s:.5f}")
    print(f"product of the six = {pr:.5f} (equal to the balance by construction: the ratios telescope)")
    diff = (kb - k.nominal_value) / math.hypot(kb_s, k.std_dev)
    out["balance_minus_keff_sigma"] = diff
    print(f"balance against OpenMC's k-eff: {diff:+.2f} combined standard deviations")
    # the fuel's U-235 atom density as OpenMC built it (from the run's frozen model.xml), atoms per barn-cm
    fuel = next(m for m in openmc.Model.from_model_xml(os.path.join(a.run_folder, "model.xml")).materials if m.name.startswith("UO2"))
    micro = f235 / phi / fuel.get_nuclide_atom_densities()["U235"]
    out["u235_thermal_fission_barns"] = float(micro)
    print(f"U-235 thermal (below 0.625 eV) fission cross section in this spectrum: {micro:.1f} b (585 b at 0.0253 eV)")
    print("RESULTS " + json.dumps(out))


if __name__ == "__main__":
    main()
