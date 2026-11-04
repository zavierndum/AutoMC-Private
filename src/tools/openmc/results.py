"""
Read a finished OpenMC run folder, or compare two. For any OpenMC model; nothing here knows about a particular reactor. Needs OpenMC
to open statepoints (WSL automc environment); importing this module does not. Reads only; changes nothing.

    python -m automc read-run RUN_FOLDER            one run: k-eff, run settings, timing, speed, tallies, lost particles, log warnings
    python -m automc read-run --compare A B         two runs: do they agree exactly (same seed)? Their k by batch and every tally,
                                                    bin by bin (mean and standard deviation)

RUN_FOLDER holds statepoint.N.h5 (the highest N is used) and, if the run was started with `openmc ... | tee run.log`, run.log.
"""
import argparse
import glob
import re
import sys
from pathlib import Path

K_TOL = 1e-10            # k and tally means: the same histories differ only by summation order across threads, near 1e-14
STD_TOL = 1e-6           # a standard deviation comes from the difference of two nearly equal sums, which magnifies that rounding by
                         # up to 1 / (relative variance of the bin): about 1e-8 for a bin known to 1e-4


class NoResult(FileNotFoundError):
    """The folder holds no statepoint: the run has not finished, or it is not a run folder."""


def latest_statepoint(folder):
    files = glob.glob(str(Path(folder) / "statepoint.*.h5"))
    if not files:
        raise NoResult(f"no statepoint.*.h5 in {folder}; the run did not finish, or this is not a run folder")
    return max(files, key=lambda f: int(re.search(r"statepoint\.(\d+)\.h5$", f).group(1)))


def log_facts(folder):
    p = Path(folder) / "run.log"
    out = {"threads": None, "warnings": [], "errors": []}
    if not p.is_file():
        return out
    for line in p.read_text(errors="replace").splitlines():
        m = re.search(r"OpenMP Threads\s*\|\s*(\d+)", line) or re.search(r"Number of OpenMP threads:\s*(\d+)", line)
        if m:
            out["threads"] = int(m.group(1))
        if "WARNING" in line:
            out["warnings"].append(line.strip())
        if "ERROR" in line:
            out["errors"].append(line.strip())
    return out


def summarize(folder):
    path = latest_statepoint(folder)                  # before importing OpenMC, so a wrong folder gets a clear message everywhere
    import numpy as np
    import openmc
    with openmc.StatePoint(path) as sp:
        k = sp.keff
        gen = np.array(sp.k_generation, dtype=float)
        active = gen[sp.n_inactive:]
        rt = {k_: float(v) for k_, v in sp.runtime.items()} if getattr(sp, "runtime", None) else {}
        tallies = {}
        for tid, t in sp.tallies.items():
            vals = np.asarray(t.mean, dtype=float)
            tallies[t.name or f"tally {tid}"] = (vals.size, bool(np.isfinite(vals).all()), bool((vals >= 0).all()), int(np.count_nonzero(vals)))
        info = {
            "file": Path(path).name, "batches": sp.n_batches, "inactive": sp.n_inactive, "particles": sp.n_particles,
            "seed": sp.seed, "keff": (float(k.n), float(k.s)), "gen": gen, "active_mean": float(active.mean()) if active.size else float("nan"),
            "active_std": float(active.std(ddof=1)) if active.size > 1 else float("nan"), "runtime": rt, "tallies": tallies,
        }
    info["lost_particle_files"] = sorted(Path(p).name for p in glob.glob(str(Path(folder) / "particle_*.h5")))
    info["log"] = log_facts(folder)
    return info


def show(folder):
    i = summarize(folder)
    sim = i["runtime"].get("simulation") or i["runtime"].get("total simulation")
    n_hist = i["particles"] * i["batches"]
    print(f"run folder       {folder}")
    print(f"statepoint       {i['file']}   seed {i['seed']}")
    print(f"k-eff            {i['keff'][0]:.5f} +/- {i['keff'][1]:.5f}     ({i['batches'] - i['inactive']} active of {i['batches']} batches, {i['inactive']} inactive, {i['particles']:,} per batch)")
    print(f"k per batch      first 3 {[round(float(x), 4) for x in i['gen'][:3]]}  last 3 {[round(float(x), 4) for x in i['gen'][-3:]]}  active mean {i['active_mean']:.5f}, spread {i['active_std']:.5f}")
    if sim:
        threads = i["log"]["threads"]
        rate = n_hist / sim
        per = f", {rate / threads:,.0f} per thread on {threads} threads" if threads else ""
        print(f"timing           simulation {sim:,.1f} s ({sim / 60:.1f} min); {rate:,.0f} histories/s{per}")
    for name, (n, finite, nonneg, nz) in i["tallies"].items():
        print(f"tally            {name:48s} {n:>9,} bins, {nz:>9,} non-zero ({100 * nz / n:.1f}%)  "
              f"{'finite' if finite else 'NOT FINITE'}{'' if nonneg else ', has negative values'}")
    print(f"lost particles   {len(i['lost_particle_files'])} file(s) {i['lost_particle_files'][:3]}")
    for w in i["log"]["warnings"][:6]:
        print(f"log warning      {w[:150]}")
    for e in i["log"]["errors"][:6]:
        print(f"log ERROR        {e[:150]}")
    ok = (i["keff"][1] > 0 and 0.2 < i["keff"][0] < 2.0 and not i["lost_particle_files"] and not i["log"]["errors"]
          and all(t[1] for t in i["tallies"].values()))
    print("verdict          " + ("plausible: finite k-eff, finite tallies, no lost particles, no errors" if ok else "CHECK THE LINES ABOVE"))
    return 0 if ok else 1


def tally_differences(a, b):
    """The tallies of two run folders' last statepoints, bin by bin: {count, only_a, only_b, other_bins, mean, std}. `mean` and `std` are
    the largest differences over all tallies, each relative to that tally's largest value; the lists name tallies that cannot be compared."""
    import numpy as np
    import openmc
    out = {"count": 0, "only_a": [], "only_b": [], "other_bins": [], "mean": 0.0, "std": 0.0}
    with openmc.StatePoint(latest_statepoint(a)) as sa, openmc.StatePoint(latest_statepoint(b)) as sb:
        name = lambda tid, t: t.name or f"tally {tid}"
        out["only_a"] = [name(i, t) for i, t in sa.tallies.items() if i not in sb.tallies]
        out["only_b"] = [name(i, t) for i, t in sb.tallies.items() if i not in sa.tallies]
        for tid, ta in sa.tallies.items():
            tb = sb.tallies.get(tid)
            if tb is None:
                continue
            for key, attr in (("mean", "mean"), ("std", "std_dev")):
                va, vb = (np.asarray(getattr(t, attr), dtype=float) for t in (ta, tb))      # one tally at a time: meshes can be large
                if va.shape != vb.shape:
                    out["other_bins"].append(name(tid, ta))
                    break
                d = np.abs(va - vb)
                d[np.isnan(d)] = np.inf                                                       # NaN in one run only
                d[np.isnan(va) & np.isnan(vb)] = 0.0                                          # NaN in both
                finite = np.abs(va[np.isfinite(va)])
                scale = finite.max() if finite.size and finite.max() > 0 else 1.0
                out[key] = max(out[key], float(d.max() / scale) if d.size else 0.0)
            else:
                out["count"] += 1
    return out


def compare(a, b):
    import numpy as np
    ia, ib = summarize(a), summarize(b)
    n = min(len(ia["gen"]), len(ib["gen"]))
    d = np.abs(ia["gen"][:n] - ib["gen"][:n]).max() if n else float("nan")
    k_same = bool(np.array_equal(ia["gen"], ib["gen"])) and ia["keff"] == ib["keff"]
    k_close = len(ia["gen"]) == len(ib["gen"]) and d < K_TOL and abs(ia["keff"][0] - ib["keff"][0]) < K_TOL and abs(ia["keff"][1] - ib["keff"][1]) < K_TOL
    t = tally_differences(a, b)
    t_match = not (t["only_a"] or t["only_b"] or t["other_bins"])
    t_same = t_match and t["mean"] == 0 and t["std"] == 0
    t_close = t_match and t["mean"] < K_TOL and t["std"] < STD_TOL
    print(f"A  {a}: k-eff {ia['keff'][0]:.6f} +/- {ia['keff'][1]:.6f}  seed {ia['seed']}  threads {ia['log']['threads']}")
    print(f"B  {b}: k-eff {ib['keff'][0]:.6f} +/- {ib['keff'][1]:.6f}  seed {ib['seed']}  threads {ib['log']['threads']}")
    print(f"same seed        {ia['seed'] == ib['seed']}")
    print(f"largest difference between batch k values {d:.3e}")
    if not t_match:
        print(f"tallies          DO NOT MATCH: only in A {t['only_a']}, only in B {t['only_b']}, other bins {t['other_bins']}")
    elif t["count"]:
        print(f"tallies          {t['count']} in both; largest difference of the means {t['mean']:.3e}, of the standard deviations "
              f"{t['std']:.3e} (each relative to the tally's largest value)")
    else:
        print("tallies          none in either run")
    if k_same and t_same:
        print("verdict          bit-for-bit identical")
    elif (k_same or k_close) and (t_same or t_close):
        print(f"verdict          reproducible: same random histories, differences only at floating-point rounding (summation order across "
              f"threads): k and tally means below {K_TOL:g}, tally standard deviations below {STD_TOL:g}")
    else:
        which = [w for w, ok in (("the k values", k_same or k_close), ("the tallies", t_same or t_close)) if not ok]
        print(f"verdict          DIFFERENT: {' and '.join(which)}, not explained by rounding")
    return 0 if (k_same or k_close) and (t_same or t_close) and ia["seed"] == ib["seed"] else 1


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m automc read-run", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder", nargs="?")
    ap.add_argument("--compare", nargs=2, metavar=("A", "B"))
    a = ap.parse_args(argv)
    if not (a.compare or a.folder):
        ap.error("give a run folder, or --compare A B")
    try:
        return compare(*a.compare) if a.compare else show(a.folder)
    except NoResult as e:
        print(e, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
