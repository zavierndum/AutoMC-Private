"""
Advice about a resolved spec: warnings and a run-time estimate. Standard library only, so `spec --views` can show it without
building anything and the agent can read it before it launches something long.

Nothing here changes what is built. A warning says "this is allowed but probably not what you want, or will not run".
"""
from __future__ import annotations

import os

from . import presets, tallies, water
from .facilities import netl

# Transport speed in histories per second per thread on this machine class (16 cores). Measured 2026-10-16 on the full 3D core
# smoke run: 24,485 per second, 1,530 per thread (one run, no heavy tallies), so the range is kept conservative at the low end.
RATE_PER_THREAD_LOW = 1000.0
RATE_PER_THREAD_HIGH = 1600.0
LONG_RUN_WARNING_S = 600.0                        # warn when the estimate reaches ten minutes
TALLY_BIN_WARNING = 1_000_000                     # per tally
WATER_DENSITY_TOLERANCE = 0.005                   # relative; the default 1.0 g/cm3 at 293.6 K is 0.2 percent above the steam table
MIN_BARITE_THICKNESS_CM = 20.0                    # a heuristic: a thinner region is not a meaningful shield


def _fmt_duration(seconds):
    if seconds < 90:
        return f"{seconds:.0f} s"
    if seconds < 5400:
        return f"{seconds / 60:.0f} min"
    if seconds < 172800:
        return f"{seconds / 3600:.1f} h"
    return f"{seconds / 86400:.1f} days"


def run_time_estimate(spec, cpu_count=None):
    """
    A rough estimate for the transport run: {histories, threads, seconds_low, seconds_high, text}. Based on measured speeds of smaller
    models, so treat it as an order of magnitude, not a promise.
    """
    eff = spec.run.effective()
    threads = eff["threads"] or cpu_count or os.cpu_count() or 16
    histories = eff["particles"] * eff["batches"]
    low = histories / (RATE_PER_THREAD_HIGH * threads)
    high = histories / (RATE_PER_THREAD_LOW * threads)
    return {
        "histories": histories, "threads": threads, "seconds_low": round(low, 1), "seconds_high": round(high, 1),
        "text": f"{histories:,} histories on {threads} threads: about {_fmt_duration(low)} to {_fmt_duration(high)}",
    }


def tally_bins(spec):
    """Bins per requested tally preset, summed over its tallies, for the cost warning (see tallies.bin_counts)."""
    return {name: v["bins_total"] for name, v in tallies.bin_counts(spec).items()}


_UNCONFIRMED = {("pnt", "thermal"): "A7", ("pnt", "epithermal"): "A7", ("three_el", "thermal"): "A13", ("three_el", "fast"): "A15"}


def insert_warnings(spec):
    """Inserts whose hardware is not confirmed by the facility, and hardware that differs from the preset's table for its label."""
    out = []
    table = presets.get(spec.preset)[1].get("insert_labels", {}) if spec.preset else {}
    _, configs = presets.configurations(spec.facility, spec.preset)
    if spec.config is None and configs is not None and (spec.pnt, spec.three_el) not in {(p, t) for p, t, _ in configs.values()}:
        out.append(f"pnt = {spec.pnt} with three_el = {spec.three_el} is not one of the {len(configs)} numbered configurations; it "
                   f"builds, but no measured worth or ROM exists for it")
    for facility, label in (("pnt", spec.pnt), ("three_el", spec.three_el)):
        if label == "none":
            continue
        name = "PNT" if facility == "pnt" else "3-EL"
        reference = table.get(facility, {}).get(label)
        if reference is not None and spec.inserts[facility] != reference:
            out.append(f"the {label} {name} uses hardware different from the preset's for that label: {spec.inserts[facility]} "
                       f"(the preset has {reference})")
        elif (facility, label) in _UNCONFIRMED:
            out.append(f"the {label} {name} hardware is not confirmed by the facility (assumption {_UNCONFIRMED[(facility, label)]}); "
                       f"results for this configuration carry that assumption")
    return out


def for_spec(spec, cpu_count=None):
    """List of warning strings for a resolved spec (the run-time estimate is a warning only when it is long)."""
    out = []
    lo, hi = min(netl.XS_TEMPERATURES_K), max(netl.XS_TEMPERATURES_K)
    cond = spec.conditions
    method = cond.resolved_temperature_method()
    for field in ("fuel_temperature_K", "non_fuel_temperature_K", "water_temperature_K"):
        t = getattr(cond, field)
        if t < lo or t > hi:
            out.append(f"conditions.{field} = {t} K is outside the cross-section library range {lo:g} to {hi:g} K; "
                       f"OpenMC will refuse it or extrapolate")
        elif method == "nearest" and min(abs(t - x) for x in netl.XS_TEMPERATURES_K) > netl.XS_TEMPERATURE_TOLERANCE_K:
            out.append(f"conditions.{field} = {t} K is more than {netl.XS_TEMPERATURE_TOLERANCE_K:g} K from any library temperature "
                       f"{list(netl.XS_TEMPERATURES_K)} and temperature_method is 'nearest': the run will fail. Use 'auto' or 'interpolation'")
    if cond.water_density_gcc != "auto" and water.in_range(cond.water_temperature_K):
        ref = water.density_g_cm3(cond.water_temperature_K)
        dev = (cond.water_density_gcc - ref) / ref
        if abs(dev) > WATER_DENSITY_TOLERANCE:
            out.append(f"conditions.water_density_gcc = {cond.water_density_gcc:g} g/cm3 is {100 * abs(dev):.1f}% "
                       f"{'above' if dev > 0 else 'below'} the steam-table density of liquid water at {cond.water_temperature_K:g} K "
                       f"({ref:.4f} g/cm3); set it to \"auto\" to follow the temperature")
    env = spec.environment
    if env.barite:
        radial = env.world_radius_cm - env.pool_radius_cm
        axial = 0.5 * (env.world_height_cm - netl.POOL_HEIGHT_CM)
        if min(radial, axial) < MIN_BARITE_THICKNESS_CM:
            out.append(f"the barite region is only {min(radial, axial):.0f} cm thick (radially {radial:g} cm, axially {axial:g} cm each side); "
                       f"below {MIN_BARITE_THICKNESS_CM:g} cm it is not a meaningful shield. Check environment.world_radius_cm and world_height_cm")
    if not env.barite:
        ref = presets.get(spec.preset)[1]["environment"]
        if env.world_radius_cm != ref["world_radius_cm"] or env.world_height_cm != ref["world_height_cm"]:
            out.append("environment.world_radius_cm and world_height_cm have no effect while environment.barite is false: the world "
                       "cylinder only exists together with the barite region and the detector geometry")
    out += insert_warnings(spec)
    rods = list(spec.rods.values())
    if all(r == 0.0 for r in rods):
        out.append("all four rods are fully inserted: expect a deeply subcritical core (k-eff well below 1)")
    elif all(r == 1.0 for r in rods):
        out.append("all four rods are fully withdrawn: expect a strongly supercritical core (k-eff well above 1)")
    for name, v in tallies.bin_counts(spec).items():
        if v["bins_each"] > TALLY_BIN_WARNING:
            out.append(f"tally preset '{name}': {v['tallies']} tallies of {v['bins_each']:,} bins each; most bins will stay empty or noisy "
                       f"unless the run has very many histories, and the statepoint grows by about "
                       f"{tallies.statepoint_megabytes(v['bins_total']):,.0f} MB for this preset")
    est = run_time_estimate(spec, cpu_count)
    if est["seconds_high"] >= LONG_RUN_WARNING_S:
        out.append(f"estimated run time is long: {est['text']}")
    return out
