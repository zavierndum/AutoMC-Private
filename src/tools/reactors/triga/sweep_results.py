"""
Tabulate a sweep: k-eff, its statistical error, and the reactivity worth between neighbouring states.

    python -m automc triga read-sweep SWEEP_FOLDER [--beta-eff 0.0070]

SWEEP_FOLDER is a folder written by `python -m automc triga sweep` and run with its run_sweep.sh (it holds sweep.json and state-NNN/run-001/).
Needs OpenMC to read the statepoints (WSL automc environment); the arithmetic itself needs only the standard library.

For a rod sweep the states are ordered by rod position, and the script checks that k-eff does not fall as the rod is withdrawn, beyond
twice the statistical error of the step. Worth between two states is the reactivity difference in pcm, 1e5 * (1/k_low - 1/k_high);
its error treats the two k-eff as independent, which is conservative when the states share a random seed. Dollars need an effective delayed
neutron fraction, given with --beta-eff (none is assumed). Exit code 0 if every state has a result and the order check passes.
"""
import argparse
import json
import math
import sys
from pathlib import Path

from tools.openmc.results import NoResult, summarize

PCM = 1.0e5


def worth_pcm(k_a, s_a, k_b, s_b):
    """Reactivity worth of going from state a to state b, in pcm, and its error. Positive when k-eff rises."""
    worth = PCM * (1.0 / k_a - 1.0 / k_b)
    err = PCM * math.sqrt((s_a / k_a ** 2) ** 2 + (s_b / k_b ** 2) ** 2)
    return worth, err


def order_problems(rows, sigmas=2.0):
    """
    rows: dicts with k, s (error) in the order of increasing rod withdrawal. Returns a list of strings, one per interval where k-eff falls
    by more than `sigmas` times the error of the step.
    """
    out = []
    for a, b in zip(rows, rows[1:]):
        w, e = worth_pcm(a["k"], a["s"], b["k"], b["s"])
        if w < -sigmas * e:
            out.append(f"k-eff falls from {a['label']} to {b['label']}: {w:.0f} pcm, {abs(w) / e:.1f} sigma")
    return out


def rod_position(manifest, state):
    """The fraction of the varied rod in this state (for 'all rods', the common value), or None for sweeps that are not rod sweeps."""
    if manifest.get("kind") != "rod":
        return None
    varied = manifest.get("varied", "")
    rod = "transient" if varied.startswith("all rods") else varied.split(" in ")[0]
    return state["rods"].get(rod)


def load_rows(folder):
    manifest = json.loads((Path(folder) / "sweep.json").read_text(encoding="utf-8"))
    rows = []
    for st in manifest["states"]:
        run = Path(folder) / st["folder"] / "run-001"
        row = {"index": st["index"], "label": st["label"], "folder": st["folder"], "position": rod_position(manifest, st),
               "console": None, "k": None, "s": None, "seconds": None, "missing": True}
        if st.get("rods_console") and row["position"] is not None:
            rod = "transient" if manifest["varied"].startswith("all rods") else manifest["varied"].split(" in ")[0]
            row["console"] = st["rods_console"].get(rod)
        try:
            info = summarize(str(run))
            row.update(k=info["keff"][0], s=info["keff"][1], missing=False,
                       seconds=info["runtime"].get("simulation") or info["runtime"].get("total simulation"),
                       lost=len(info["lost_particle_files"]), errors=len(info["log"]["errors"]))
        except NoResult:
            pass
        rows.append(row)
    return manifest, rows


def table(folder, beta_eff=None):
    """
    The sweep as plain data: every state (ordered by rod position for rod sweeps), the worth of each step between neighbouring states
    that have results, the total, the order check, and whether every state has a result without lost particles or errors.
    """
    manifest, rows = load_rows(folder)
    is_rod = manifest.get("kind") == "rod"
    ordered = sorted(rows, key=lambda r: (r["position"] if r["position"] is not None else r["index"]))
    done = [r for r in ordered if not r["missing"]]

    def step(a, b):
        w, e = worth_pcm(a["k"], a["s"], b["k"], b["s"])
        out = {"from": a["label"], "to": b["label"], "worth_pcm": w, "error_pcm": e}
        if beta_eff:
            out["worth_dollars"] = w / PCM / beta_eff
        return out

    steps = [step(a, b) for a, b in zip(done, done[1:])]
    problems = order_problems(done) if is_rod and len(done) >= 2 else []
    return {
        "name": manifest["name"], "varied": manifest["varied"], "kind": manifest.get("kind"), "seed": manifest["seed"],
        "beta_eff": beta_eff, "states": ordered, "steps": steps,
        "total": step(done[0], done[-1]) if len(done) >= 2 else None,
        "order_check": None if not (is_rod and len(done) >= 2) else {"passed": not problems, "problems": problems},
        "complete": len(done) == len(rows),
        "clean": not any(r.get("lost") or r.get("errors") for r in done),
        "run_script": manifest.get("run_script", "run_sweep.sh"),
    }


def show(folder, beta_eff=None):
    t = table(folder, beta_eff)
    print(f"sweep {t['name']}   varied: {t['varied']}   seed {t['seed']}   {len(t['states'])} states")
    print(f"{'state':10s} {'label':36s} {'k-eff':>9s} {'+/-':>8s} {'worth from previous':>26s} {'time':>7s}")
    steps = iter(t["steps"])
    first = True
    for r in t["states"]:
        if r["missing"]:
            print(f"{r['folder']:10s} {r['label']:36s} {'no result (not run yet)':>40s}")
            continue
        if first:
            w, first = "", False
        else:
            st = next(steps)
            w = f"{st['worth_pcm']:+8.0f} +/- {st['error_pcm']:3.0f} pcm"
            if beta_eff:
                w += f" ({st['worth_dollars']:+.2f} $)"
        tm = f"{r['seconds']:.0f} s" if r["seconds"] else ""
        flag = "  LOST PARTICLES" if r.get("lost") else ""
        print(f"{r['folder']:10s} {r['label']:36s} {r['k']:9.5f} {r['s']:8.5f} {w:>26s} {tm:>7s}{flag}")
    ok = t["complete"] and t["clean"]
    if t["total"]:
        tot = t["total"]
        extra = f" ({tot['worth_dollars']:+.2f} $)" if beta_eff else ""
        print(f"\ntotal from {tot['from']} to {tot['to']}: {tot['worth_pcm']:+.0f} +/- {tot['error_pcm']:.0f} pcm{extra}")
    if t["order_check"]:
        if t["order_check"]["problems"]:
            ok = False
            for p_ in t["order_check"]["problems"]:
                print("ORDER CHECK FAILED:", p_)
        else:
            print("order check: k-eff does not fall with rod withdrawal beyond 2 sigma of each step")
    missing = sum(1 for r in t["states"] if r["missing"])
    if missing:
        print(f"{missing} state(s) have no result: run  bash {Path(folder) / t['run_script']}")
    if not beta_eff:
        print("pcm only; pass --beta-eff to convert to dollars once a delayed neutron fraction is chosen")
    return 0 if ok else 1


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m automc triga read-sweep", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder")
    ap.add_argument("--beta-eff", type=float, help="effective delayed neutron fraction, to express worths in dollars")
    a = ap.parse_args(argv)
    if not (Path(a.folder) / "sweep.json").is_file():
        print(f"{a.folder} has no sweep.json; give a folder written by `python -m automc triga sweep`")
        return 2
    return show(a.folder, a.beta_eff)


if __name__ == "__main__":
    sys.exit(main())
