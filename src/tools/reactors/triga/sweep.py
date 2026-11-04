"""
Sweeps: one build per state, all in one folder, plus a script that runs them and a manifest that records what each state is.

    plan = plan_sweep(rod_states("transient", [0, 0.25, 0.5, 0.75, 1.0]), name="rod-tr", common={"config": 0})
    result = build_sweep(plan)          # builds every state (identical states are reused), writes sweep.json and run_sweep.sh

A *state* is a small dictionary of spec fields laid over a *common* spec. Three front ends make states: `rod_states` (one rod, or all four
together, at several positions in any rod unit), `field_states` (any spec field at several values) and plain lists of dictionaries
(for example a rod-withdrawal sequence). Everything is validated, named and hashed before the first build starts, so a mistake in
the fifth state does not waste four builds. Nothing here runs transport: `run_sweep.sh` is for the user to run.

Every state gets the same random seed unless the states themselves vary it, so differences between states are less noisy (common
random numbers; each k-eff keeps its own statistical error). The seed is written explicitly into every state's spec.

Folder layout, inside the workspace:

    <sweep-name>/sweep.json        the manifest (definition of every state, hashes, seed, estimate)
    <sweep-name>/run_sweep.sh      runs OpenMC for each state in turn; skips states that already have a statepoint
    <sweep-name>/state-001/        an ordinary build folder (model.xml, result.json, build.log); transport runs go in state-001/run-001/
"""
from __future__ import annotations

import copy
import datetime
import json
import os
import shlex
from dataclasses import dataclass, field
from pathlib import Path

from . import __version__, advice
from .capabilities import check_supported
from .errors import InvalidSpec
from .facilities import netl
from .safety import WORKSPACE, validate_name
from .spec import ROD_UNITS, normalize_rod_name, resolve, rod_to_fraction

SWEEP_VERSION = 1
MAX_STATES = 50
DEFAULT_SEED = 1
MAX_NAME_LENGTH = 50            # the state names add "-s001"


# ----------------------------------------------------------------------------------------------------------------------
# front ends: states
# ----------------------------------------------------------------------------------------------------------------------
def rod_states(rod, positions, unit="fraction"):
    """
    States that move one rod (or "all" four rods together) through `positions` in `unit`. Returns (kind, varied, states, labels).
    The rod may be written in any accepted spelling (transient, TR, ...).
    """
    if unit not in ROD_UNITS:
        raise InvalidSpec("unit", f"must be one of {list(ROD_UNITS)}, got {unit!r}")
    if not isinstance(positions, (list, tuple)) or not positions:
        raise InvalidSpec("positions", "must be a non-empty list of numbers")
    rods = list(netl.RODS) if str(rod).strip().lower() == "all" else [normalize_rod_name(rod, "rod")]
    states, labels = [], []
    for i, p in enumerate(positions):
        if isinstance(p, bool) or not isinstance(p, (int, float)):
            raise InvalidSpec(f"positions[{i}]", f"must be a number, got {p!r}")
        value = {"value": p, "unit": unit}
        states.append({"rods": {r: dict(value) for r in rods}})
        labels.append(f"{'all rods' if len(rods) > 1 else rods[0]} = {p:g} {unit}")
    return "rod", f"{'all rods' if len(rods) > 1 else rods[0]} in {unit}", states, labels


def field_states(path, values):
    """States that set the spec field `path` (dotted, for example conditions.fuel_temperature_K) to each of `values`."""
    if not isinstance(path, str) or not path or path.startswith(".") or path.endswith(".") or ".." in path:
        raise InvalidSpec("field", f"must be a dotted field path such as conditions.fuel_temperature_K, got {path!r}")
    if not isinstance(values, (list, tuple)) or not values:
        raise InvalidSpec("values", "must be a non-empty list")
    states, labels = [], []
    for v in values:
        node = cur = {}
        parts = path.split(".")
        for part in parts[:-1]:
            cur[part] = {}
            cur = cur[part]
        cur[parts[-1]] = copy.deepcopy(v)
        states.append(node)
        labels.append(f"{path} = {json.dumps(v)}")
    return "field", path, states, labels


def list_states(states, labels=None):
    """States given directly, for sequences that change several things at once."""
    if not isinstance(states, (list, tuple)) or not states or any(not isinstance(s, dict) for s in states):
        raise InvalidSpec("states", "must be a non-empty list of objects holding spec fields")
    out, labs = [], []
    for i, s in enumerate(states):
        s = copy.deepcopy(s)
        lab = s.pop("label", None)
        out.append(s)
        labs.append(str(lab) if lab is not None else (labels[i] if labels else f"state {i + 1}"))
    return "states", "a list of states", out, labs


# ----------------------------------------------------------------------------------------------------------------------
# plan
# ----------------------------------------------------------------------------------------------------------------------
@dataclass
class SweepState:
    index: int
    label: str
    name: str
    folder: str
    overrides: dict
    spec: object                    # a resolved Spec
    spec_hash: str


@dataclass
class SweepPlan:
    name: str
    kind: str
    varied: str
    seed: int
    base_spec_hash: str
    states: list
    workspace: Path
    estimate: dict = field(default_factory=dict)

    @property
    def folder(self):
        return self.workspace / self.name


def _load_common(common):
    if common is None:
        return {}
    if isinstance(common, (str, os.PathLike)):
        try:
            with open(common, "r", encoding="utf-8") as fh:
                common = json.load(fh)
        except (OSError, json.JSONDecodeError) as e:
            raise InvalidSpec("common", f"cannot read spec file {common!s}: {e}") from e
    if not isinstance(common, dict):
        raise InvalidSpec("common", f"must be a dict of spec fields or a path to a JSON file, got {type(common).__name__}")
    return copy.deepcopy(common)


def plan_sweep(front_end, *, name, preset=None, common=None, workspace=None, max_states=MAX_STATES, code="openmc"):
    """
    Resolve, check and name every state without building anything. `front_end` is what rod_states, field_states or list_states returned.
    Raises InvalidSpec (naming the state) for any invalid state, UnsupportedFeature for a state that cannot be built yet, and
    InvalidSpec for duplicate states or too many states.
    """
    kind, varied, state_dicts, labels = front_end
    validate_name(name)
    if len(name) > MAX_NAME_LENGTH:
        raise InvalidSpec("name", f"a sweep name is at most {MAX_NAME_LENGTH} characters, got {len(name)}")
    if len(state_dicts) > max_states:
        raise InvalidSpec("states", f"{len(state_dicts)} states is more than the limit {max_states}; raise max_states on purpose")
    base_layer = _load_common(common)
    base_layer.pop("name", None)
    probe = resolve(preset=preset, spec=base_layer)
    seed = probe.run.seed if probe.run.seed is not None else DEFAULT_SEED
    layer = copy.deepcopy(base_layer)
    layer.setdefault("run", {})
    if not isinstance(layer["run"], dict):
        raise InvalidSpec("run", "must be an object")
    layer["run"].setdefault("seed", seed)
    base_hash = resolve(preset=preset, spec=layer).spec_hash()

    ws = Path(workspace or WORKSPACE)
    states, seen = [], {}
    for i, (overrides, label) in enumerate(zip(state_dicts, labels), start=1):
        fields = copy.deepcopy(overrides)
        fields.pop("name", None)
        try:
            s = resolve(preset=preset, spec=layer, **fields)
            check_supported(s, code)
        except Exception as e:
            if hasattr(e, "field"):
                e.args = (f"state {i} ({label}): {e}",)
            raise
        h = s.spec_hash()
        if h in seen:
            raise InvalidSpec("states", f"states {seen[h]} and {i} are the same model ({label}); remove the duplicate")
        seen[h] = i
        sname = f"{name}-s{i:03d}"
        s.name = sname
        states.append(SweepState(index=i, label=label, name=sname, folder=f"state-{i:03d}", overrides=overrides, spec=s, spec_hash=h))
    lows = sum(advice.run_time_estimate(st.spec)["seconds_low"] for st in states)
    highs = sum(advice.run_time_estimate(st.spec)["seconds_high"] for st in states)
    est = {"states": len(states), "seconds_low": round(lows, 1), "seconds_high": round(highs, 1),
           "text": f"{len(states)} states, about {advice._fmt_duration(lows)} to {advice._fmt_duration(highs)} of transport in total"}
    return SweepPlan(name=name, kind=kind, varied=varied, seed=seed, base_spec_hash=base_hash, states=states, workspace=ws, estimate=est)


# ----------------------------------------------------------------------------------------------------------------------
# build
# ----------------------------------------------------------------------------------------------------------------------
def _atomic_write(path, text):
    tmp = Path(str(path) + f".tmp-{os.getpid()}")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def run_script_text(plan, threads):
    n = len(plan.states)
    folders = " ".join(st.folder for st in plan.states)
    labels = "\n".join(f"  {shlex.quote(st.label)}" for st in plan.states)
    return f"""#!/usr/bin/env bash
# Written by triga (sweep {plan.name}). Runs OpenMC for each state in turn, in the folder state-NNN/run-001.
# Start it from the WSL automc environment:  bash run_sweep.sh      (THREADS=8 bash run_sweep.sh to change the thread count)
# It skips a state that already has a statepoint, so an interrupted sweep can be started again.
# Expected transport time: {plan.estimate.get("text", "")}.
set -u
cd "$(dirname "$0")"
THREADS=${{THREADS:-{threads}}}
STATES=({folders})
LABELS=(
{labels}
)
total={n}
for i in "${{!STATES[@]}}"; do
  s="${{STATES[$i]}}"
  echo "=== state $((i + 1)) of $total: ${{LABELS[$i]}}"
  if ls "$s"/run-001/statepoint.*.h5 >/dev/null 2>&1; then
    echo "    already has a statepoint, skipping"
    continue
  fi
  mkdir -p "$s/run-001" && cp "$s/model.xml" "$s/run-001/" \\
    && (cd "$s/run-001" && openmc -s "$THREADS" 2>&1 | tee run.log) \\
    || echo "    state $((i + 1)) FAILED"
done
echo "done: read the results from AutoMC/src with  python -m automc triga read-sweep $(pwd)"
"""


def build_sweep(plan, *, timeout_s=None, python=None, allow_outside=False):
    """
    Build every state (identical existing builds are reused), then write run_sweep.sh and sweep.json. Returns the manifest as a dict.
    A folder that already holds a different sweep is refused, so run results inside it are never overwritten.
    """
    from .build import build_input
    folder = plan.folder
    manifest_path = folder / "sweep.json"
    if folder.exists():
        if manifest_path.is_file():
            old = json.loads(manifest_path.read_text(encoding="utf-8"))
            if [s["spec_hash"] for s in old.get("states", [])] != [s.spec_hash for s in plan.states]:
                raise InvalidSpec("name", f"{folder} already holds a different sweep; choose another name (its runs are not touched)")
        elif any(folder.iterdir()):
            raise InvalidSpec("name", f"{folder} exists and is not a sweep folder; choose another name")
    folder.mkdir(parents=True, exist_ok=True)

    built = []
    for st in plan.states:
        r = build_input("openmc", "netl", spec=st.spec.to_dict(), outdir=str(folder / st.folder), workspace=str(plan.workspace),
                        timeout_s=timeout_s, python=python, allow_outside=allow_outside)
        est = advice.run_time_estimate(st.spec)
        built.append({
            "index": st.index, "label": st.label, "name": st.name, "folder": st.folder, "spec_hash": st.spec_hash,
            "overrides": st.overrides, "rods": {k: round(v, 12) for k, v in st.spec.rods.items()},
            "rods_console": {k: v["console"] for k, v in st.spec.rod_views().items()},
            "reused": r.reused, "build_seconds": r.build_seconds, "run_estimate": est,
        })
    threads = built[0]["run_estimate"]["threads"]
    _atomic_write(folder / "run_sweep.sh", run_script_text(plan, threads))
    manifest = {
        "sweep_version": SWEEP_VERSION, "triga_version": __version__, "name": plan.name, "kind": plan.kind, "varied": plan.varied,
        "seed": plan.seed, "base_spec_hash": plan.base_spec_hash, "preset": plan.states[0].spec.preset,
        "built_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "estimate": plan.estimate, "run_script": "run_sweep.sh", "states": built, "outdir": str(folder),
    }
    _atomic_write(manifest_path, json.dumps(manifest, indent=2))
    return manifest


def load_sweep(folder):
    """Read sweep.json from a sweep folder."""
    p = Path(folder) / "sweep.json"
    if not p.is_file():
        raise InvalidSpec("sweep", f"{folder} has no sweep.json")
    return json.loads(p.read_text(encoding="utf-8"))
