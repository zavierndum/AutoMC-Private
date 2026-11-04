"""
The TRIGA Input Builder as agent tools. One plain function per tool: a dict of arguments in, a JSON-serializable dict out, the shape of
an MCP tool handler (NeutronOS model_corral mcp_tools), so the agent, the command line (`python -m automc tool`) and an MCP server all
call the same functions.

  - Arguments are checked strictly: an unknown or missing argument is an error that names it.
  - Nothing raises. A failure comes back as {"ok": false, "error": {"type", "field", "message", "exit_code"}}, the payload of the
    command line's --json; success is {"ok": true, ...}.
  - Every result that depends on facility data carries "facility_data" with its label ("public" or "restricted"), so a caller can
    keep restricted results away from an API-hosted model.
  - Tools take case and sweep names, never paths: everything they write or read stays inside the workspace.
  - None of these tools runs transport.

TOOLS lists each tool: name, description (stating its limits in plain words), JSON schema of the arguments, handler.
"""
from __future__ import annotations

import functools
import json

from . import advice
from .errors import InvalidSpec, TrigaError, error_payload
from .presets import active_assumptions, all_assumptions, facility_data
from .safety import WORKSPACE, validate_name

SPEC_FIELDS = ("name", "config", "pnt", "three_el", "rods", "inserts", "conditions", "fuel", "environment", "tallies",
               "tallies_options", "nuclear_data", "run")

_OBJ = {"type": "object"}
_SPEC_PROPERTIES = {
    "preset": {"type": "string", "description": "preset reference, name or name@version (default: the installed default preset)"},
    "spec": {"type": "object", "description": "spec fields as one object, for example the 'spec' of an earlier result; keyword "
                                               "arguments below are applied on top of it"},
    "name": {"type": "string", "description": "case name, which becomes the folder name in the workspace"},
    "config": {"type": "integer", "description": "numbered configuration (sets pnt and three_el), if a facility pack numbers them"},
    "pnt": {"type": "string", "enum": ["none", "thermal", "epithermal"], "description": "pneumatic transfer insert at G-34"},
    "three_el": {"type": "string", "enum": ["none", "thermal", "epithermal", "fast"], "description": "three-element irradiator"},
    "rods": {"anyOf": [{"type": "number"}, {"type": "object"}],
             "description": "a number (a fraction, for all four rods), or an object {rod: value} with rods transient/regulating/shim_1/"
                            "shim_2 (or TR, RR, S1, S2) and value a number (fraction) or {value, unit} with unit fraction, percent, "
                            "console, cm_withdrawn or z_cm. Numbers, not strings."},
    "inserts": {**_OBJ, "description": "insert hardware overrides: {pnt: {...}, three_el: {...}, three_el_empty_state}"},
    "conditions": {**_OBJ, "description": "temperatures (K), water density (g/cm3 or \"auto\"), temperature_method"},
    "fuel": {**_OBJ, "description": "{source, force_mapped_locations}"},
    "environment": {**_OBJ, "description": "pool radius, beam-port lengths, world cylinder, barite region, detector geometry"},
    "tallies": {"type": "array", "items": {"type": "string"}, "description": "tally preset names; [] for none"},
    "tallies_options": {**_OBJ, "description": "energy bins, angle bins, mesh, size limit"},
    "nuclear_data": {**_OBJ, "description": "{library}"},
    "run": {**_OBJ, "description": "{size: smoke|standard|production, particles, batches, inactive, seed, threads}"},
}


def _schema(extra=None, required=(), spec_fields=True):
    props = dict(_SPEC_PROPERTIES) if spec_fields else {}
    props.update(extra or {})
    return {"type": "object", "properties": props, "required": list(required), "additionalProperties": False}


def _check(args, schema):
    if args is None:
        args = {}
    if not isinstance(args, dict):
        raise InvalidSpec("arguments", f"must be an object, got {type(args).__name__}")
    unknown = sorted(set(args) - set(schema["properties"]))
    if unknown:
        raise InvalidSpec(unknown[0], f"unknown argument; allowed: {sorted(schema['properties'])}")
    missing = [k for k in schema["required"] if k not in args]
    if missing:
        raise InvalidSpec(missing[0], "required argument")
    if "spec" in args and not isinstance(args["spec"], dict):
        raise InvalidSpec("spec", "must be an object of spec fields")
    return dict(args)


def _fields(a):
    return {k: a[k] for k in SPEC_FIELDS if k in a}


def _merge(base, extra):
    out = dict(base)
    for k, v in extra.items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def _resolve(a):
    from .spec import resolve
    return resolve(preset=a.get("preset"), spec=a.get("spec"), facility="netl", **_fields(a))


def as_tool(fn):
    """Wrap a tool body: check nothing raises, add "ok"."""
    @functools.wraps(fn)
    def handler(args=None):
        try:
            return {"ok": True, **fn(args)}
        except TrigaError as e:
            return {"ok": False, "error": error_payload(e)}
        except Exception as e:                                       # a bug: report it as data, never crash the caller
            return {"ok": False, "error": {"type": "TrigaError", "field": None, "message": f"unexpected {type(e).__name__}: {e}",
                                           "exit_code": 1}}
    return handler


# ----------------------------------------------------------------------------------------------------------------------
# the tools
# ----------------------------------------------------------------------------------------------------------------------
SPEC_SCHEMA = _schema()


@as_tool
def triga_input_spec(args):
    """Resolve and check a spec; report everything about it. Builds nothing."""
    from .capabilities import check_supported
    from .errors import UnsupportedFeature
    s = _resolve(_check(args, SPEC_SCHEMA))
    try:
        check_supported(s)
        buildable, why = True, None
    except UnsupportedFeature as e:
        buildable, why = False, str(e)
    return {"spec": s.to_dict(), "spec_hash": s.spec_hash(), "name": s.name, "rods_all_units": s.rod_views(),
            "beam_port_lengths_cm": dict(zip(("ports_2_to_4", "port_1_5"), s.environment.beam_port_lengths_cm())),
            "run_estimate": advice.run_time_estimate(s), "warnings": advice.for_spec(s), "assumptions": active_assumptions(s),
            "facility_data": facility_data(s), "buildable": buildable, "not_buildable_because": why}


BUILD_SCHEMA = _schema({
    "code": {"type": "string", "enum": ["openmc", "mcnp"], "description": "transport code of the input (openmc; mcnp is not built yet)"},
    "force": {"type": "boolean", "description": "rebuild and replace an existing folder of the same name; never one that holds runs"},
    "timeout_s": {"type": "number", "minimum": 10, "maximum": 3600, "description": "seconds before the build worker is killed"},
})


@as_tool
def triga_input_builder(args):
    """Build the input of one case into the workspace. Never runs transport."""
    from .build import build_input
    a = _check(args, BUILD_SCHEMA)
    timeout = a.get("timeout_s")
    if timeout is not None and not (isinstance(timeout, (int, float)) and not isinstance(timeout, bool) and 10 <= timeout <= 3600):
        raise InvalidSpec("timeout_s", f"must be a number from 10 to 3600, got {timeout!r}")
    r = build_input(a.get("code", "openmc"), "netl", preset=a.get("preset"), spec=a.get("spec"), force=bool(a.get("force", False)),
                    timeout_s=timeout, workspace=WORKSPACE, **_fields(a))
    return {"result": r.to_dict()}


SWEEP_SCHEMA = _schema({
    "sweep_name": {"type": "string", "description": "name of the sweep; its folder in the workspace"},
    "sweep": {"type": "object", "description": "exactly one of: {rod, positions, unit?} (one rod, or 'all', at several positions); "
                                               "{field, values} (a spec field by dotted path at several values); {states, labels?} "
                                               "(a list of objects of spec fields)"},
    "max_states": {"type": "integer", "minimum": 1, "maximum": 50, "description": "refuse more states than this (default 50)"},
    "timeout_s": {"type": "number", "minimum": 10, "maximum": 3600, "description": "seconds before one build worker is killed"},
}, required=("sweep_name", "sweep"))


def _front_end(sweep):
    from .sweep import field_states, list_states, rod_states
    if not isinstance(sweep, dict):
        raise InvalidSpec("sweep", "must be an object")
    kinds = [k for k in ("rod", "field", "states") if k in sweep]
    if len(kinds) != 1:
        raise InvalidSpec("sweep", "give exactly one of rod (with positions), field (with values) or states")
    allowed = {"rod": {"rod", "positions", "unit"}, "field": {"field", "values"}, "states": {"states", "labels"}}[kinds[0]]
    extra = sorted(set(sweep) - allowed)
    if extra:
        raise InvalidSpec(f"sweep.{extra[0]}", f"unknown key for a {kinds[0]} sweep; allowed: {sorted(allowed)}")
    if kinds[0] == "rod":
        if "positions" not in sweep:
            raise InvalidSpec("sweep.positions", "a rod sweep needs positions")
        return rod_states(sweep["rod"], sweep["positions"], sweep.get("unit", "fraction"))
    if kinds[0] == "field":
        if "values" not in sweep:
            raise InvalidSpec("sweep.values", "a field sweep needs values")
        return field_states(sweep["field"], sweep["values"])
    return list_states(sweep["states"], sweep.get("labels"))


@as_tool
def triga_sweep_builder(args):
    """Build one model per state of a sweep into the workspace, with a run script. Never runs transport."""
    from .sweep import build_sweep, plan_sweep
    a = _check(args, SWEEP_SCHEMA)
    validate_name(a["sweep_name"])
    common = _fields(a)
    common.pop("name", None)
    if a.get("spec"):
        common = _merge(a["spec"], common)
    plan = plan_sweep(_front_end(a["sweep"]), name=a["sweep_name"], preset=a.get("preset"), common=common,
                      max_states=a.get("max_states", 50), workspace=WORKSPACE)
    labels = [facility_data(st.spec)["distribution"] for st in plan.states]
    return {"sweep": build_sweep(plan, timeout_s=a.get("timeout_s")),
            "facility_data": {"distribution": "restricted" if "restricted" in labels else "public"}}


PREVIEW_SCHEMA = _schema({
    "case": {"type": "string", "description": "name of a built case in the workspace"},
    "which": {"type": "array", "items": {"type": "string", "enum": ["core_xy", "world_xy", "core_xz", "world_xz"]},
              "description": "which pictures (default all four)"},
    "pixels": {"type": "integer", "minimum": 100, "maximum": 6000, "description": "image width in pixels (default 1000)"},
}, required=("case",), spec_fields=False)


@as_tool
def triga_input_preview(args):
    """Draw geometry pictures of a built case. No transport."""
    from .preview import render_previews
    a = _check(args, PREVIEW_SCHEMA)
    validate_name(a["case"])                                         # a case name, never a path
    made = render_previews(a["case"], which=a.get("which"), pixels=a.get("pixels", 1000), workspace=WORKSPACE)
    built = WORKSPACE / a["case"] / "result.json"
    fd = json.loads(built.read_text(encoding="utf-8")).get("facility_data") if built.is_file() else None
    return {"previews": made, "facility_data": fd}


ASSUMPTIONS_SCHEMA = _schema({
    "all": {"type": "boolean", "description": "the whole register of the preset's pack instead of what applies to this spec"},
})


@as_tool
def triga_assumptions(args):
    """The assumptions that apply to a spec (or the whole register), with who can confirm each and its status."""
    a = _check(args, ASSUMPTIONS_SCHEMA)
    if a.get("all"):
        if _fields(a) or a.get("spec"):
            raise InvalidSpec("all", "lists the whole register; give no spec fields with it")
        register = all_assumptions(preset=a.get("preset"))
        from . import packs
        dists = {p.distribution for p in packs.installed().packs if p.id in {x["pack"] for x in register}}
        return {"assumptions": register, "facility_data": {"distribution": "restricted" if "restricted" in dists else "public"}}
    s = _resolve(a)
    return {"assumptions": active_assumptions(s), "facility_data": facility_data(s)}


SWEEP_RESULTS_SCHEMA = _schema({
    "sweep_name": {"type": "string", "description": "name of a sweep in the workspace that has been run"},
    "beta_eff": {"type": "number", "exclusiveMinimum": 0, "maximum": 0.05,
                 "description": "effective delayed neutron fraction, to give worths in dollars too (none is assumed)"},
}, required=("sweep_name",), spec_fields=False)


@as_tool
def triga_sweep_results(args):
    """k-eff of every state of a sweep that has been run, and the worth between neighbouring states. Reads only."""
    from .sweep_results import table
    a = _check(args, SWEEP_RESULTS_SCHEMA)
    validate_name(a["sweep_name"])
    folder = WORKSPACE / a["sweep_name"]
    if not (folder / "sweep.json").is_file():
        raise InvalidSpec("sweep_name", f"no sweep named {a['sweep_name']!r} in the workspace (no sweep.json)")
    beta = a.get("beta_eff")
    if beta is not None and not (isinstance(beta, (int, float)) and not isinstance(beta, bool) and 0 < beta <= 0.05):
        raise InvalidSpec("beta_eff", f"must be a number above 0 and at most 0.05, got {beta!r}")
    out = table(folder, beta)
    states = json.loads((folder / "sweep.json").read_text(encoding="utf-8")).get("states", [])
    first = folder / states[0]["folder"] / "result.json" if states else None
    if first is not None and first.is_file():
        out["facility_data"] = json.loads(first.read_text(encoding="utf-8")).get("facility_data")
    return out


TOOLS = [
    {"name": "TRIGAInputSpecTool", "handler": triga_input_spec, "parameters": SPEC_SCHEMA,
     "description": "Resolve and check a TRIGA spec (NETL TRIGA, any of the configurations, rods in any unit) and report the resolved "
                    "values, every rod in every unit, warnings, the assumptions that apply, a run-time estimate, which facility data it "
                    "uses and whether it can be built. Builds nothing and runs nothing; use it before a build to check a request."},
    {"name": "TRIGAInputBuilderTool", "handler": triga_input_builder, "parameters": BUILD_SCHEMA,
     "description": "Build the OpenMC input (model.xml) of one NETL TRIGA case into the workspace and return the full result "
                    "(model facts, library provenance, assumptions, warnings, run-time estimate, facility data). Builds input only; "
                    "never runs transport. An identical earlier build is reused; a different folder of the same name is refused "
                    "unless force is true, and a folder that holds runs is never replaced."},
    {"name": "TRIGASweepBuilderTool", "handler": triga_sweep_builder, "parameters": SWEEP_SCHEMA,
     "description": "Build one model per state of a sweep (a rod at several positions, a spec field at several values, or a list of "
                    "states), all with the same random seed, plus a run script and a manifest, into one workspace folder. Builds input "
                    "only; never runs transport. Everything is checked before the first build."},
    {"name": "TRIGAInputPreviewTool", "handler": triga_input_preview, "parameters": PREVIEW_SCHEMA,
     "description": "Draw coloured geometry slices with a legend (core and world, xy and xz) of a case that has been built. Takes a "
                    "few seconds; runs no transport. Needs the Linux or WSL environment with OpenMC."},
    {"name": "TRIGAAssumptionsTool", "handler": triga_assumptions, "parameters": ASSUMPTIONS_SCHEMA,
     "description": "List the assumptions behind a spec (or the whole register with all=true): what each assumes, the spec fields and "
                    "values it concerns, who can confirm it and its status. Quote these with any result built from the spec."},
    {"name": "TRIGASweepResultsTool", "handler": triga_sweep_results, "parameters": SWEEP_RESULTS_SCHEMA,
     "description": "Read a sweep that has been run: k-eff and its statistical error for every state, the reactivity worth between "
                    "neighbouring states in pcm (and dollars if beta_eff is given), and for rod sweeps a check that k-eff does not "
                    "fall as a rod is withdrawn. Reads only. States not run yet are listed as missing."},
]
