"""
Read a multigroup run of either layout.

- infinite: k and the flux by group, and the seven-group balance solved exactly: with no space,
  Sigma_t,g phi_g = sum_g' Sigma_s,g'->g phi_g' + chi_g / k sum_g' nuSigma_f,g' phi_g', so k is the largest eigenvalue of
  (diag(Sigma_t) - S^T)^-1 chi nuSigma_f^T, and the Monte Carlo k must agree with it within its error.
- pincell: k and the thermal flux in the fuel against the moderator.
- core: k against the benchmark's reference; the pin powers (fission rate per fuel pin, normalised so the 1,056 fuel pins average
  1, as the benchmark does), the three assembly powers (each the sum of its 264 pins) and the highest and lowest pin, all against
  the reference MCNP solution.

    python post.py RUN_FOLDER

Prints them and one last line `RESULTS {json}` for scripts and tests.
"""
import argparse
import glob
import importlib.util
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


def load_build(run_folder):
    """The case's build.py: a run folder holds a copy next to model.xml, or the case folder above it does."""
    here = os.path.join(run_folder, "build.py")
    here = here if os.path.exists(here) else os.path.join(run_folder, "..", "build.py")
    spec = importlib.util.spec_from_file_location("build", here)
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    return build


def exact_k_infinity(data):
    """The seven-group k-infinity of one data set (build.py's DATA): the largest eigenvalue of the balance equations."""
    total, chi = np.array(data["total"]), np.array(data["chi"])
    nu_fission = np.multiply(data["nu"], data["fission"])
    S = np.array(data["scatter"])                                        # [group in, group out]
    M = np.linalg.solve(np.diag(total) - S.T, np.outer(chi, nu_fission))
    return float(np.max(np.real(np.linalg.eigvals(M))))


def core_results(sp, k, build):
    """Pin and assembly powers from the mesh tally, normalised over the fuel pins, against the reference."""
    t = sp.get_tally(name="pin fission rate")
    n = 2 * build.N
    rate = t.mean.reshape(n, n)[::-1]                                     # the mesh counts rows from the bottom; Figure 3 from the top
    error = t.std_dev.reshape(n, n)[::-1]
    pin_map = [u + m for u, m in zip(build.UO2_MAP, build.MOX_MAP)] + [m + u for m, u in zip(build.MOX_MAP, build.UO2_MAP)]
    fuel = np.array([[c in "ULMH" for c in row] for row in pin_map])
    scale = fuel.sum() / rate[fuel].sum()
    power, power_err = rate * scale, error * scale
    blocks = {"inner UO2": (slice(0, build.N), slice(0, build.N)), "MOX": (slice(0, build.N), slice(build.N, n)),
              "MOX (lower left)": (slice(build.N, n), slice(0, build.N)), "outer UO2": (slice(build.N, n), slice(build.N, n))}
    assembly = {name: float(power[b][fuel[b]].sum()) for name, b in blocks.items()}
    assembly_err = {name: float(math.sqrt((power_err[b][fuel[b]] ** 2).sum())) for name, b in blocks.items()}
    ref = build.REFERENCE
    sigma = math.hypot(k.std_dev, ref["keff"] * ref["keff_rel_error"])
    out = {"k_reference": ref["keff"], "k_minus_reference_pcm": (k.nominal_value - ref["keff"]) * 1e5,
           "k_minus_reference_sigma": (k.nominal_value - ref["keff"]) / sigma}
    print(f"benchmark reference (MCNP): {ref['keff']:.6f}; difference {out['k_minus_reference_pcm']:+.0f} pcm "
          f"({out['k_minus_reference_sigma']:+.1f} combined standard deviations)")
    print("assembly powers (pin powers averaging 1):")
    worst = 0.0
    for name in ("inner UO2", "MOX", "MOX (lower left)", "outer UO2"):
        target = ref["assembly"][name.split(" (")[0]]
        diff = assembly[name] / target - 1
        worst = max(worst, abs(diff))
        print(f"  {name:18s} {assembly[name]:7.1f} +/- {assembly_err[name]:4.1f}   reference {target:5.1f}   {100 * diff:+.2f}%")
    hi, lo = np.unravel_index(np.argmax(np.where(fuel, power, -1)), power.shape), np.unravel_index(
        np.argmin(np.where(fuel, power, np.inf)), power.shape)
    where = lambda rc: f"row {rc[0] + 1}, column {rc[1] + 1}"            # Figure 3's numbering, from 1 at the top left
    print(f"highest pin {power[hi]:.3f} +/- {power_err[hi]:.3f} at {where(hi)} (reference {ref['max_pin']}); "
          f"lowest {power[lo]:.3f} +/- {power_err[lo]:.3f} at {where(lo)} (reference {ref['min_pin']})")
    out.update(assembly_powers=assembly, assembly_worst_rel_diff=worst, max_pin=float(power[hi]), max_pin_where=where(hi),
               min_pin=float(power[lo]), min_pin_where=where(lo), mox_symmetry_rel_diff=assembly["MOX (lower left)"] / assembly["MOX"] - 1)
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("run_folder")
    a = parser.parse_args()
    build = load_build(a.run_folder)
    with openmc.StatePoint(statepoint(a.run_folder)) as sp:
        k = sp.keff
        layout = "core" if any(t.name == "pin fission rate" for t in sp.tallies.values()) else None
        if layout == "core":
            print(f"k-eff = {k.nominal_value:.5f} +/- {k.std_dev:.5f}")
            out = {"keff": float(k.nominal_value), "keff_sigma": float(k.std_dev), "layout": "core", **core_results(sp, k, build)}
            print("RESULTS " + json.dumps(out))
            return
        t = sp.get_tally(name="flux by group")
        cells = [c.name for c in sp.summary.geometry.get_all_cells().values()]
    out = {"keff": float(k.nominal_value), "keff_sigma": float(k.std_dev), "layout": "infinite" if len(cells) == 1 else "pincell"}
    print(f"k-infinity = {k.nominal_value:.5f} +/- {k.std_dev:.5f}")
    flux = t.mean.reshape(len(cells), -1)                                 # [cell, group]; the energy bins run up from group 7
    for name, f in zip(cells, flux):
        share = f[::-1] / f.sum()
        print(f"{name}: flux share by group 1 (fast) to 7 (thermal): " + ", ".join(f"{x:.3f}" for x in share))
    if out["layout"] == "infinite":
        exact = exact_k_infinity(build.DATA["UO2"])
        out.update(k_exact=exact, k_minus_exact_sigma=(k.nominal_value - exact) / k.std_dev)
        print(f"exact seven-group k-infinity of the UO2 data: {exact:.5f}; Monte Carlo is {out['k_minus_exact_sigma']:+.2f} sigma off")
    else:
        fuel_area = math.pi * build.R_FUEL ** 2                           # the tally sums over each cell: divide by its volume
        per_volume = flux[:, 0] / [fuel_area, build.PITCH ** 2 - fuel_area]   # (both cells span the same height)
        out["fuel_over_moderator_thermal_flux"] = float(per_volume[0] / per_volume[1])
        print(f"thermal (group 7) flux per volume, fuel / moderator: {out['fuel_over_moderator_thermal_flux']:.3f}")
    print("RESULTS " + json.dumps(out))


if __name__ == "__main__":
    main()
