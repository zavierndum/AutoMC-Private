"""
Command line. Run from AutoMC/src:   python -m automc triga <command> [options]      (add -h to any command)

    build         build the transport-code input for a spec (needs the WSL automc environment)
    sweep         build one model per state of a rod, a spec field or a list of states
    read-sweep    tabulate k-eff and worths of a sweep after it has been run (needs OpenMC to read the statepoints)
    preview       draw geometry pictures of a built case (needs OpenMC; runs no transport)
    spec          resolve a spec from a preset plus overrides and print it, with warnings and a run-time estimate
    presets       list the available presets
    assumptions   list the assumptions that apply to a resolved spec

Exit codes: 0 ok, 1 other error, 2 invalid spec, 3 valid but not buildable yet, 4 missing library or environment, 5 timeout.
With --json, errors are printed to stdout as {"ok": false, "error": {...}} (same exit codes), for scripts and agents.
"""
from __future__ import annotations

import argparse
import json
import sys

from . import __version__, advice
from .errors import (BuildTimeout, InvalidSpec, MissingLibrary, TrigaError, UnsupportedFeature, error_payload, exit_code_for)
from .presets import active_assumptions, all_assumptions, describe, facility_data
from .spec import ROD_UNITS, normalize_rod_name, resolve

_PREFIX = {InvalidSpec: "invalid", UnsupportedFeature: "unsupported", MissingLibrary: "missing library", BuildTimeout: "timeout"}

BUILD_EXAMPLES = """\
examples (from AutoMC/src, in the WSL automc environment with OPENMC_CROSS_SECTIONS set):
  python -m automc triga build --config 0
  python -m automc triga build --config 0 --rod S1=600:console --rod TR=0.3 --name s1-600-tr-03
  python -m automc triga build --rods 0.5 --set conditions.water_temperature_K=320 --set run.size=\\"standard\\"
  python -m automc triga build --config 0 --force            rebuild and replace an existing folder of the same name
  python -m automc triga build --config 0 --json             the full result as JSON, for scripts and agents
rods:  NAME=VALUE or NAME=VALUE:UNIT; names transient regulating shim_1 shim_2 (or TR RR S1 S2); units: """ + ", ".join(ROD_UNITS)


# ----------------------------------------------------------------------------------------------------------------------
# turning options into spec fields
# ----------------------------------------------------------------------------------------------------------------------
def _rod_token(token):
    """NAME=VALUE or NAME=VALUE:UNIT  ->  (name, value-or-dict)"""
    if "=" not in token:
        raise InvalidSpec("--rod", f"expected NAME=VALUE or NAME=VALUE:UNIT, got {token!r}")
    name, rest = token.split("=", 1)
    value, _, unit = rest.partition(":")
    try:
        num = float(value)
    except ValueError:
        raise InvalidSpec(f"--rod {name}", f"value must be a number, got {value!r}") from None
    if unit and unit not in ROD_UNITS:
        raise InvalidSpec(f"--rod {name}", f"unknown unit {unit!r}; allowed: {list(ROD_UNITS)}")
    return name, ({"value": num, "unit": unit} if unit else num)


def _literal(text):
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def _set_dotted(fields, dotted, value):
    parts = dotted.split(".")
    node = fields
    for p in parts[:-1]:
        node = node.setdefault(p, {})
        if not isinstance(node, dict):
            raise InvalidSpec(f"--set {dotted}", "conflicts with an earlier value")
    node[parts[-1]] = value


def _add_spec_options(p):
    p.add_argument("--preset", help="preset name or name@version (default: latest of the default preset)")
    p.add_argument("--spec-file", help="JSON file with spec fields to apply over the preset")
    p.add_argument("--config", type=int, help="configuration 0-9")
    p.add_argument("--pnt", help="none | thermal | epithermal")
    p.add_argument("--three-el", dest="three_el", help="none | thermal | epithermal | fast (also --3L)")
    p.add_argument("--3L", dest="three_el", help=argparse.SUPPRESS)
    p.add_argument("--rods", help="one value for all four rods: VALUE or VALUE:UNIT")
    p.add_argument("--rod", action="append", default=[], metavar="NAME=VALUE[:UNIT]",
                   help=f"one rod (transient/regulating/shim_1/shim_2 or TR/RR/S1/S2); UNIT is one of {', '.join(ROD_UNITS)}; repeatable")
    p.add_argument("--tallies", help="comma list of tally presets: flux_mesh, beam_port_2_detector_flux, beam_port_2_detector_current, beam_port_2_sphere_flux, beam_port_1_5_detector_flux, "
                                     "or none; replaces the preset's list (the preset netl_boc505_tallies already has four)")
    p.add_argument("--set", action="append", default=[], metavar="PATH=VALUE",
                   help="any field by dotted path, value as JSON, e.g. conditions.water_temperature_K=294; repeatable")
    p.add_argument("--name", help="case name (folder name); default triga-<facility>-c<config>-<hash>")


def _fields_from_args(a):
    fields = {}
    for key in ("config", "pnt", "three_el", "name"):
        if getattr(a, key, None) is not None:
            fields[key] = getattr(a, key)
    if getattr(a, "tallies", None) is not None:
        fields["tallies"] = [] if a.tallies.strip().lower() == "none" else [t.strip() for t in a.tallies.split(",") if t.strip()]
    rods = {}
    if a.rods is not None:                      # one value for all four, then --rod tokens override single rods
        v, _, u = a.rods.partition(":")
        try:
            raw = {"value": float(v), "unit": u} if u else float(v)
        except ValueError:
            raise InvalidSpec("--rods", f"expected a number, got {a.rods!r}") from None
        rods = {rod: raw for rod in ("transient", "regulating", "shim_1", "shim_2")}
    given = set()
    for tok in a.rod:
        name, val = _rod_token(tok)
        canonical = normalize_rod_name(name, "--rod")  # TR and transient are the same rod
        if canonical in given:
            raise InvalidSpec(f"--rod {name}", "given more than once")
        given.add(canonical)
        rods.pop(canonical, None)                      # --rod overrides the value --rods expanded for this rod ...
        rods[name] = val                               # ... but keeps the spelling the user typed, for error messages
    if rods:
        fields["rods"] = rods
    for item in a.set:
        if "=" not in item:
            raise InvalidSpec("--set", f"expected PATH=VALUE, got {item!r}")
        path, raw = item.split("=", 1)
        _set_dotted(fields, path, _literal(raw))
    return fields


SWEEP_EXAMPLES = """examples:
  python -m automc triga sweep --name rod-tr --sweep-rod transient --positions 0,0.25,0.5,0.75,1
  python -m automc triga sweep --name rod-tr-console --sweep-rod TR --positions 0,240,480,720,960 --unit console
  python -m automc triga sweep --name all-rods --sweep-rod all --positions 0.3,0.5,0.7
  python -m automc triga sweep --name fuel-temp --sweep-field conditions.fuel_temperature_K --values '[294,600,900]'
  python -m automc triga sweep --name withdrawal --states-file states.json      (a JSON list of objects of spec fields, optional "label")

Each state is an ordinary build in <workspace>/<name>/state-NNN/. Nothing is run: the command ends by printing how to run the sweep
(bash run_sweep.sh) and how to read it (python -m automc triga read-sweep). All states use the same random seed."""


def _merge_layers(base, extra):
    out = dict(base)
    for k, v in extra.items():
        out[k] = _merge_layers(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def _sweep_front_end(a):
    from .sweep import field_states, list_states, rod_states
    chosen = [x for x in (a.sweep_rod, a.sweep_field, a.states_file) if x]
    if len(chosen) != 1:
        raise InvalidSpec("--sweep-rod / --sweep-field / --states-file", "give exactly one of them")
    if a.sweep_rod:
        if not a.positions:
            raise InvalidSpec("--positions", "a rod sweep needs --positions, for example 0,0.25,0.5,0.75,1")
        try:
            positions = [float(x) for x in a.positions.split(",") if x.strip()]
        except ValueError:
            raise InvalidSpec("--positions", f"must be a comma list of numbers, got {a.positions!r}") from None
        return rod_states(a.sweep_rod, positions, a.unit)
    if a.sweep_field:
        if not a.values:
            raise InvalidSpec("--values", "a field sweep needs --values as a JSON list, for example '[294,600,900]'")
        try:
            values = json.loads(a.values)
        except json.JSONDecodeError as e:
            raise InvalidSpec("--values", f"not valid JSON: {e}") from None
        return field_states(a.sweep_field, values)
    try:
        with open(a.states_file, "r", encoding="utf-8") as fh:
            return list_states(json.load(fh))
    except (OSError, json.JSONDecodeError) as e:
        raise InvalidSpec("--states-file", f"cannot read {a.states_file}: {e}") from None


def _print_sweep(m):
    n_reused = sum(1 for st in m["states"] if st["reused"])
    head = "reused identical builds" if n_reused == len(m["states"]) else f"built {len(m['states']) - n_reused} of {len(m['states'])} states"
    print(f"{head}: sweep {m['name']}")
    print(f"  folder    {m['outdir']}")
    print(f"  varied    {m['varied']}   (same random seed {m['seed']} in every state)")
    for st in m["states"]:
        rods = " ".join(f"{k[:2].upper()}={v:g}" for k, v in st["rods"].items())
        print(f"  {st['folder']}  {st['label']:36s} rods {rods}{'  (reused)' if st['reused'] else ''}")
    print(f"  transport {m['estimate']['text']}")
    print("\nNothing was run. To run the sweep yourself (WSL, automc environment):")
    print(f"  bash '{m['outdir']}/run_sweep.sh'")
    print("Then read the table:")
    print(f"  python -m automc triga read-sweep '{m['outdir']}'      (from AutoMC/src)")


def _resolve_from_args(a):
    return resolve(preset=a.preset, spec=a.spec_file, **_fields_from_args(a))


# ----------------------------------------------------------------------------------------------------------------------
# output
# ----------------------------------------------------------------------------------------------------------------------
def _kb(n):
    return f"{n / 1e6:.2f} MB" if n >= 1e6 else f"{n / 1e3:.0f} kB"


def _print_build(r):
    s = r.summary
    f = s["fuel"]
    rods = "  ".join(f"{k} {v['fraction']:g} ({v['console']:g} console)" for k, v in s["rods"].items())
    est = s["run_estimate"]
    model = next(x for x in r.files if x["path"] == "model.xml")
    head = "reused an identical build" if r.reused else f"built in {r.build_seconds:g} s"
    print(f"{head}: {r.name}")
    print(f"  folder    {r.outdir}")
    print(f"  model     model.xml {_kb(model['bytes'])}, {s['cells']:,} cells, {s['materials']:,} materials, {s['tallies']} tallies")
    tp = s.get("tally_presets", {})
    if tp.get("requested"):
        bins = sum(v["bins_total"] for v in tp["bins"].values())
        print(f"  tallies   {len(tp['tallies_added'])} added for {', '.join(tp['requested'])}; {bins:,} bins in total "
              f"(about {bins * 16 / 1e6:,.0f} MB in the statepoint) plus {tp['generic_library_default_tallies']} generic tallies")
    print(f"  profile   {r.profile['name']}   preset {r.preset}   spec {r.spec_hash[:12]}")
    if r.facility_data:
        fd = r.facility_data
        label = "RESTRICTED" if fd["distribution"] == "restricted" else fd["distribution"]
        origin = ", ".join("%s (%s)" % (p["pack"], p["distribution"]) for p in fd["packs"]) or "no facility pack"
        print(f"  data      {label}   from {origin}")
    forced = ", ".join(f["forced_locations"]) if f["forced_locations"] else "none"
    if f["mapped_locations"]:
        print(f"  fuel      {f['mapped_locations_holding_fuel']} of {f['mapped_locations']} mapped locations hold fuel (forced: {forced})")
    else:
        print(f"  fuel      {r.spec['fuel']['source']}, {f['elements']} elements (the library's loading)")
    print(f"  rods      {rods}")
    print(f"  run       {s['settings']['particles']:,} x {s['settings']['batches']} batches ({s['settings']['inactive']} inactive); {est['text']}")
    print(f"  assumed   {' '.join(a['id'] for a in r.assumptions)}   (python -m automc triga assumptions --all)")
    for w in r.warnings:
        print(f"  warning   {w}", file=sys.stderr)
    folder = r.outdir
    print("\nThe builder never runs transport. To run it yourself in this terminal (WSL, automc environment):")
    print(f"  mkdir -p '{folder}/run-001' && cp '{folder}/model.xml' '{folder}/run-001/' && cd '{folder}/run-001' && openmc -s {est['threads']}")
    print(f"To see the geometry first:  python -m automc triga preview {r.name}")


def _fail(exc, as_json):
    code = exit_code_for(exc)
    if as_json:
        print(json.dumps({"ok": False, "error": error_payload(exc)}, indent=2))
    else:
        label = next((v for k, v in _PREFIX.items() if isinstance(exc, k)), "error")
        print(f"{label}: {exc}", file=sys.stderr)
    return code


# ----------------------------------------------------------------------------------------------------------------------
def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m automc triga", description="TRIGA input builder. Run `python -m automc triga <command> -h` for options.",
                                 epilog="Exit codes: 0 ok, 1 error, 2 invalid spec, 3 not buildable yet, 4 missing library, 5 timeout.")
    ap.add_argument("--version", action="version", version=f"triga {__version__}")
    sub = ap.add_subparsers(dest="command", required=True)

    p_build = sub.add_parser("build", help="build the transport-code input for a spec", epilog=BUILD_EXAMPLES,
                             formatter_class=argparse.RawDescriptionHelpFormatter)
    _add_spec_options(p_build)
    p_build.add_argument("--code", default="openmc", help="openmc (default); mcnp arrives in Block 4")
    p_build.add_argument("--outdir", help="output folder; must be inside the workspace unless --allow-outside")
    p_build.add_argument("--workspace", help="workspace folder (default AutoMC/workspace)")
    p_build.add_argument("--allow-outside", action="store_true", help="let --outdir point outside the workspace (trusted local use only)")
    p_build.add_argument("--timeout", type=float, help="seconds before the build worker is killed (default 600)")
    p_build.add_argument("--force", action="store_true", help="rebuild and replace an existing folder, even an identical one")
    p_build.add_argument("--json", action="store_true", help="print the full result (or the error) as JSON")
    p_build.add_argument("--quiet", action="store_true", help="print only the output folder")

    p_sweep = sub.add_parser("sweep", help="build one model per state of a rod, a spec field or a list of states",
                             epilog=SWEEP_EXAMPLES, formatter_class=argparse.RawDescriptionHelpFormatter)
    _add_spec_options(p_sweep)
    p_sweep.add_argument("--sweep-rod", metavar="ROD", help="the rod to move: transient, regulating, shim_1, shim_2 (or TR, RR, S1, S2), or all")
    p_sweep.add_argument("--positions", help="with --sweep-rod: comma list of positions")
    p_sweep.add_argument("--unit", default="fraction", help=f"with --sweep-rod: unit of the positions, one of {', '.join(ROD_UNITS)} (default fraction)")
    p_sweep.add_argument("--sweep-field", metavar="PATH", help="a spec field by dotted path, for example conditions.fuel_temperature_K")
    p_sweep.add_argument("--values", help="with --sweep-field: JSON list of values")
    p_sweep.add_argument("--states-file", help="JSON file: a list of objects of spec fields (optional \"label\") for sequences")
    p_sweep.add_argument("--workspace", help="workspace folder (default AutoMC/workspace)")
    p_sweep.add_argument("--timeout", type=float, help="seconds before one build worker is killed (default 600)")
    p_sweep.add_argument("--max-states", type=int, default=50, help="refuse more states than this (default 50)")
    p_sweep.add_argument("--json", action="store_true", help="print the manifest (or the error) as JSON")

    p_read = sub.add_parser("read-sweep", help="tabulate k-eff and worths of a sweep that has been run")
    p_read.add_argument("folder", help="the sweep folder (holds sweep.json and state-NNN/run-001/)")
    p_read.add_argument("--beta-eff", type=float, help="effective delayed neutron fraction, to express worths in dollars")

    p_prev = sub.add_parser("preview", help="draw geometry pictures of a built case (no transport)")
    p_prev.add_argument("case", help="case name inside the workspace, or a path to a case folder")
    p_prev.add_argument("--which", help="comma list of: core_xy, world_xy, core_xz, world_xz (default all four)")
    p_prev.add_argument("--pixels", type=int, default=1000, help="image width in pixels (100-6000, default 1000)")
    p_prev.add_argument("--workspace", help="workspace folder (default AutoMC/workspace)")
    p_prev.add_argument("--json", action="store_true")

    p_spec = sub.add_parser("spec", help="resolve and print a spec")
    _add_spec_options(p_spec)
    p_spec.add_argument("--views", action="store_true",
                        help="also show every rod in every unit, the hash, beam port lengths, warnings and the run-time estimate")
    p_spec.add_argument("--json", action="store_true", help="print errors as JSON")

    sub.add_parser("presets", help="list presets")

    p_as = sub.add_parser("assumptions", help="list assumptions (all, or only those that apply to a resolved spec)")
    _add_spec_options(p_as)
    p_as.add_argument("--all", action="store_true", help="list every assumption, not just the ones that apply")
    p_as.add_argument("--json", action="store_true", help="print errors as JSON")

    a = ap.parse_args(argv)
    as_json = bool(getattr(a, "json", False))
    try:
        if a.command == "presets":
            print(json.dumps(describe(), indent=2))
        elif a.command == "spec":
            s = _resolve_from_args(a)
            if a.views:
                out = {"spec": s.to_dict(), "rods_all_units": s.rod_views(), "spec_hash": s.spec_hash(),
                       "beam_port_lengths_cm": dict(zip(("ports_2_to_4", "port_1_5"), s.environment.beam_port_lengths_cm())),
                       "resolved_temperature_method": s.conditions.resolved_temperature_method(), "run_effective": s.run.effective(),
                       "run_estimate": advice.run_time_estimate(s), "warnings": advice.for_spec(s), "facility_data": facility_data(s)}
                print(json.dumps(out, indent=2))
            else:
                print(json.dumps(s.to_dict(), indent=2))
        elif a.command == "assumptions":
            print(json.dumps(all_assumptions(preset=a.preset) if a.all else active_assumptions(_resolve_from_args(a)), indent=2))
        elif a.command == "build":
            from .build import build_input
            fields = _fields_from_args(a)
            r = build_input(a.code, "netl", preset=a.preset, spec=a.spec_file, outdir=a.outdir, workspace=a.workspace,
                            timeout_s=a.timeout, force=a.force, allow_outside=a.allow_outside, **fields)
            if a.json:
                print(r.to_json())
            elif a.quiet:
                print(r.outdir)
            else:
                _print_build(r)
        elif a.command == "sweep":
            from .sweep import build_sweep, plan_sweep
            fields = _fields_from_args(a)
            name = fields.pop("name", None)
            if not name:
                raise InvalidSpec("--name", "a sweep needs a name; it becomes the folder name")
            common = fields
            if a.spec_file:
                try:
                    with open(a.spec_file, "r", encoding="utf-8") as fh:
                        common = _merge_layers(json.load(fh), fields)
                except (OSError, json.JSONDecodeError) as e:
                    raise InvalidSpec("--spec-file", f"cannot read {a.spec_file}: {e}") from None
            plan = plan_sweep(_sweep_front_end(a), name=name, preset=a.preset, common=common, workspace=a.workspace, max_states=a.max_states)
            manifest = build_sweep(plan, timeout_s=a.timeout, allow_outside=False)
            if a.json:
                print(json.dumps(manifest, indent=2))
            else:
                _print_sweep(manifest)
        elif a.command == "read-sweep":
            from .sweep_results import main as read_sweep_main
            return read_sweep_main([a.folder] + (["--beta-eff", str(a.beta_eff)] if a.beta_eff else []))
        elif a.command == "preview":
            from .preview import render_previews
            made = render_previews(a.case, which=a.which.split(",") if a.which else None, pixels=a.pixels, workspace=a.workspace)
            if a.json:
                print(json.dumps(made, indent=2))
            else:
                for m in made:
                    print(f"  {m['name']:9s} {m['path']}  ({_kb(m['bytes'])})")
    except TrigaError as e:
        return _fail(e, as_json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
