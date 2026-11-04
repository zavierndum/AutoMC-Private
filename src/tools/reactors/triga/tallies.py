"""
Tally presets. Every preset is one entry of plain data, so a new detector or a new design is a new entry and not new code. The
general presets are in TALLY_PRESETS below; presets that need a facility's own geometry (the beam-port detector cells) come from a
facility pack (tally_presets.yaml) and use the same keys. `definitions(spec)` merges the two. Functions that read them:

  bin_counts(spec)                       standard library only: how many tallies and bins a spec asks for, for the cost warning
                                         and the size guard
  add_to_model(model, spec, definitions) needs OpenMC, so it runs only inside the build worker: builds the OpenMC tallies on a
                                         built model

The definitions are unverified against any reference output (assumption A11 of the facility packs).

Meshes are data too: `tallies_options.mesh` is a name from MESH_PRESETS or {dimension, lower_left_cm, upper_right_cm}.
"""
from __future__ import annotations

import math

from . import energy_bins as energy_bins_mod
from .errors import InvalidSpec, TrigaError

# ----------------------------------------------------------------------------------------------------------------------
# meshes
# ----------------------------------------------------------------------------------------------------------------------
MESH_PRESETS = {
    # The reference `flux_mesh`: 150 x 150 x 3 over +/-90 cm in x and y (the stock 90 cm pool, not our 150 cm pool) and +/-27.69997 cm in z.
    "stock_pool_90cm": {"dimension": [150, 150, 3],
                  "lower_left_cm": [-90.0, -90.0, -27.699970000000004],
                  "upper_right_cm": [90.0, 90.0, 27.699970000000004]},
}
_MESH_KEYS = ("dimension", "lower_left_cm", "upper_right_cm")


def resolve_mesh(value, field="tallies_options.mesh"):
    """A mesh name or an explicit mesh dictionary -> a validated {dimension, lower_left_cm, upper_right_cm} (new object)."""
    if isinstance(value, str):
        if value not in MESH_PRESETS:
            raise InvalidSpec(field, f"must be one of {sorted(MESH_PRESETS)} or an object with {list(_MESH_KEYS)}, got {value!r}")
        value = MESH_PRESETS[value]
    if not isinstance(value, dict) or set(value) != set(_MESH_KEYS):
        raise InvalidSpec(field, f"a mesh object needs exactly {list(_MESH_KEYS)}, got {value!r}")
    out = {}
    for key in _MESH_KEYS:
        v = value[key]
        if not isinstance(v, (list, tuple)) or len(v) != 3:
            raise InvalidSpec(f"{field}.{key}", f"must be a list of three numbers, got {v!r}")
        if key == "dimension":
            if any(isinstance(x, bool) or not isinstance(x, int) or x < 1 or x > 2000 for x in v):
                raise InvalidSpec(f"{field}.{key}", f"must be three whole numbers from 1 to 2000, got {list(v)!r}")
            out[key] = [int(x) for x in v]
        else:
            if any(isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) for x in v):
                raise InvalidSpec(f"{field}.{key}", f"must be three finite numbers in cm, got {list(v)!r}")
            out[key] = [float(x) for x in v]
    if any(lo >= hi for lo, hi in zip(out["lower_left_cm"], out["upper_right_cm"])):
        raise InvalidSpec(field, f"lower_left_cm must be below upper_right_cm in every direction, got {out['lower_left_cm']} and "
                                 f"{out['upper_right_cm']}")
    return out


def mesh_bins(mesh):
    n = 1
    for x in resolve_mesh(mesh)["dimension"]:
        n *= x
    return n


# ----------------------------------------------------------------------------------------------------------------------
# the preset table
# ----------------------------------------------------------------------------------------------------------------------
# kind "cell_flux":        one tally per matching cell, named <cell>_flux
# kind "surface_current":  one tally per matching entrance surface, paired with the detector cell of the same sorted position,
#                          named <surface>_current; `from_cell` adds a CellFromFilter on that cell
# kind "mesh_flux":        one tally on the regular mesh, named my_spatial_flux
# `filters` lists the filters in the order the reference script adds them (this order is part of the definition).
TALLY_PRESETS = {
    "flux_mesh": dict(
        kind="mesh_flux", filters=("mesh", "neutron"), score="flux", tally_name="my_spatial_flux", needs_detectors=False,
        doc="Neutron flux on a regular mesh (the reference `my_spatial_flux`)."),
}


def definitions(spec):
    """{name: definition} of every tally preset available to this spec: the general ones here, then the facility packs' (spec's own
    pack first)."""
    from .presets import pack_tally_presets
    out = dict(TALLY_PRESETS)
    for name, (_, d) in pack_tally_presets(spec.facility, spec.preset).items():
        out.setdefault(name, d)
    return out


def names(facility, preset):
    from .presets import pack_tally_presets
    return tuple(TALLY_PRESETS) + tuple(n for n in pack_tally_presets(facility, preset) if n not in TALLY_PRESETS)


def _angle_bins(spec):
    o = spec.tallies_options
    return {"polar": o.polar_bins, "azimuthal": o.azimuthal_bins}


def bin_counts(spec):
    """
    {preset: {"tallies": n, "bins_each": b, "bins_total": n*b}} for the tally presets the spec asks for. A bin is one
    (energy, polar, azimuth, ...) combination of one tally. Standard library only.
    """
    n_energy_bins = energy_bins_mod.bin_count(spec.tallies_options.energy_bins)
    angles = _angle_bins(spec)
    table = definitions(spec) if spec.tallies else {}
    out = {}
    for name in spec.tallies:
        d = table[name]
        if d["kind"] == "mesh_flux":
            n, each = 1, mesh_bins(spec.tallies_options.mesh)
        else:
            n, each = d["expected"], 1
            for f in d["filters"]:
                if f == "energy":
                    each *= n_energy_bins
                elif f in angles:
                    each *= angles[f]
        out[name] = {"tallies": n, "bins_each": each, "bins_total": n * each}
    return out


def total_bins(spec):
    return sum(v["bins_total"] for v in bin_counts(spec).values())


def statepoint_megabytes(bins):
    """Rough statepoint size of the tally results: two 8-byte numbers (sum and sum of squares) per bin."""
    return bins * 16 / 1e6


def check_spec(spec):
    """Cross-field rules for tallies; called by Spec.validate. Raises InvalidSpec naming the field."""
    table = definitions(spec) if spec.tallies else {}
    needs = [n for n in spec.tallies if table[n]["needs_detectors"]]
    if needs and not spec.environment.detector_geometry:
        raise InvalidSpec("tallies", f"{needs} need the beam-port detector cells; set environment.detector_geometry to true "
                                     f"(and environment.barite, which comes with it) or drop these tallies")
    if spec.tallies:
        total = total_bins(spec)
        limit = spec.tallies_options.max_total_bins
        if total > limit:
            raise InvalidSpec("tallies_options.max_total_bins",
                              f"the requested tallies have {total:,} bins in total, above the limit {limit:,}. Reduce the energy "
                              f"bins (tallies_options.energy_bins), the angle bins, or the tally list; or raise the limit on purpose")


# ----------------------------------------------------------------------------------------------------------------------
# building (worker only)
# ----------------------------------------------------------------------------------------------------------------------
def _energy_edges(value):
    if isinstance(value, list):
        return [float(x) for x in value]
    if value == "energy_bins_mpact_51_groups":
        import openmc.mgxs
        return [float(x) for x in openmc.mgxs.GROUP_STRUCTURES["MPACT-51"]]
    return list(energy_bins_mod.ENERGY_BIN_EDGES_238_EV)


def add_to_model(model, spec, table=None):
    """Append the requested tally presets to an OpenMC model. Returns the names of the tallies added, in order. `table` is the
    {name: definition} the parent resolved (definitions(spec)); looked up again when not given."""
    import numpy as np
    import openmc

    if not spec.tallies:
        return []
    opts = spec.tallies_options
    edges = _energy_edges(opts.energy_bins)
    polar = openmc.PolarFilter(np.linspace(0.0, np.pi, opts.polar_bins + 1))
    azimuthal = openmc.AzimuthalFilter(np.linspace(-np.pi, np.pi, opts.azimuthal_bins + 1))
    neutron = openmc.ParticleFilter(["neutron"])
    energy = openmc.EnergyFilter(edges)

    cells = model.geometry.get_all_cells()
    by_name = {c.name: c for c in cells.values()}

    def select_cells(d):
        if "cell_exact" in d:
            found = [c for c in cells.values() if c.name == d["cell_exact"]]
        else:
            found = sorted((c for c in cells.values() if (c.name or "").startswith(d["cell_prefix"])), key=lambda c: c.name)
        if len(found) != d["expected"]:
            raise TrigaError(f"expected {d['expected']} cells for the tally preset, found {len(found)} "
                             f"({d.get('cell_exact') or d['cell_prefix']}*); the detector geometry may be missing from the library")
        return found

    table = definitions(spec) if table is None else table
    made = []
    for preset in spec.tallies:
        d = table[preset]

        def make(name, filters_by_kind):
            t = openmc.Tally(name=name)
            t.filters = [filters_by_kind[k] for k in d["filters"]]
            t.scores = [d["score"]]
            model.tallies.append(t)
            made.append(name)

        if d["kind"] == "mesh_flux":
            m = resolve_mesh(opts.mesh)
            mesh = openmc.RegularMesh()
            mesh.dimension = m["dimension"]
            mesh.lower_left = tuple(m["lower_left_cm"])
            mesh.upper_right = tuple(m["upper_right_cm"])
            make(d["tally_name"], {"mesh": openmc.MeshFilter(mesh), "neutron": neutron})
        elif d["kind"] == "cell_flux":
            for cell in select_cells(d):
                make(f"{cell.name}_flux", {"cell": openmc.CellFilter(cell), "energy": energy, "polar": polar,
                                           "azimuthal": azimuthal, "neutron": neutron})
        elif d["kind"] == "surface_current":
            detector_cells = select_cells(d)
            surfaces = sorted((s for s in model.geometry.get_all_surfaces().values()
                               if (s.name or "").startswith(d["cell_prefix"]) and (s.name or "").endswith(d["surface_suffix"])),
                              key=lambda s: s.name)
            if len(surfaces) != d["expected"]:
                raise TrigaError(f"expected {d['expected']} entrance surfaces, found {len(surfaces)}")
            if d["from_cell"] not in by_name:
                raise TrigaError(f"cell {d['from_cell']} not found in the model")
            from_filter = openmc.CellFromFilter(by_name[d["from_cell"]])
            for cell, surf in zip(detector_cells, surfaces):
                make(f"{surf.name}_current", {"surface": openmc.SurfaceFilter(surf), "cell": openmc.CellFilter(cell),
                                              "cell_from": from_filter, "energy": energy, "neutron": neutron,
                                              "polar": polar, "azimuthal": azimuthal})
        else:                                                           # unreachable unless the table is edited wrongly
            raise TrigaError(f"unknown tally kind {d['kind']!r} in preset {preset}")
    return made
