"""
The code-neutral spec: dataclasses, strict validation, rod unit conversion, configuration resolution, hashing, JSON.

No CoreForge, no OpenMC. Everything here can run (and be tested) without any library profile.

Defaults live only in presets, which come from facility packs (presets.py, packs.py), and so does the table that numbers the
configurations. The dataclasses have no defaults, so `Spec.from_dict` needs a complete dictionary and a
forgotten field is an error, not a silent default.
"""
from __future__ import annotations

import copy
import dataclasses
import hashlib
import json
import math
import os
from dataclasses import dataclass
from pathlib import Path

from . import energy_bins as energy_bins_mod, inserts as inserts_mod, presets, tallies as tallies_mod, water
from .errors import InvalidSpec
from .facilities import netl

SPEC_VERSION = 1

FACILITIES = ("netl",)
PNT_VALUES = ("none", "thermal", "epithermal")
THREE_EL_VALUES = ("none", "thermal", "epithermal", "fast")
_INSERT_SYNONYMS = {"none": "none", "bare": "none", "t": "thermal", "thermal": "thermal",
                    "e": "epithermal", "epithermal": "epithermal", "f": "fast", "fast": "fast"}
TEMPERATURE_METHODS = ("auto", "nearest", "interpolation")
FUEL_SOURCES = ("depleted_boc505", "fresh")
BARITE_BASES = ("atom", "mass")
NUCLEAR_DATA_LIBRARIES = ("endfb-viii.0",)
RUN_SIZES = {            # size -> (particles per batch, batches, inactive batches)
    "smoke": (20_000, 40, 10),
    "standard": (500_000, 100, 20),
    "production": (5_000_000, 150, 20),
}

ROD_UNITS = ("fraction", "percent", "console", "cm_withdrawn", "z_cm")
_ROUND = 12              # fractions are rounded so that every unit gives the identical value for the same position
_SLACK = 1e-9            # floating-point slack at the ends of a range; anything beyond is an error, never clamped silently



# ======================================================================================================================
# small validators
# ======================================================================================================================
def _is_number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _num(path, v, lo=None, hi=None, lo_open=False, none_ok=False):
    if v is None and none_ok:
        return None
    if not _is_number(v):
        raise InvalidSpec(path, f"must be a finite number, got {v!r}")
    v = float(v)
    if lo is not None and (v <= lo if lo_open else v < lo):
        raise InvalidSpec(path, f"must be {'greater than' if lo_open else 'at least'} {lo}, got {v}")
    if hi is not None and v > hi:
        raise InvalidSpec(path, f"must be at most {hi}, got {v}")
    return v


def _int(path, v, lo=None, hi=None, none_ok=False):
    if v is None and none_ok:
        return None
    if isinstance(v, bool) or not isinstance(v, int):
        if isinstance(v, float) and v.is_integer():
            v = int(v)
        else:
            raise InvalidSpec(path, f"must be an integer, got {v!r}")
    if lo is not None and v < lo:
        raise InvalidSpec(path, f"must be at least {lo}, got {v}")
    if hi is not None and v > hi:
        raise InvalidSpec(path, f"must be at most {hi}, got {v}")
    return v


def _bool(path, v):
    if not isinstance(v, bool):
        raise InvalidSpec(path, f"must be true or false, got {v!r}")
    return v


def _enum(path, v, allowed):
    if not isinstance(v, str) or v not in allowed:
        raise InvalidSpec(path, f"must be one of {list(allowed)}, got {v!r}")
    return v


def _strict(cls, data, path):
    """Build a dataclass from a dict, rejecting unknown and missing keys."""
    if not isinstance(data, dict):
        raise InvalidSpec(path, f"must be an object, got {type(data).__name__}")
    names = [f.name for f in dataclasses.fields(cls)]
    unknown = sorted(set(data) - set(names))
    if unknown:
        raise InvalidSpec(f"{path}.{unknown[0]}", f"unknown field; allowed: {names}")
    missing = [n for n in names if n not in data]
    if missing:
        raise InvalidSpec(f"{path}.{missing[0]}", "missing (a spec needs every field; defaults come from a preset)")
    return cls(**data)


# ======================================================================================================================
# rod positions
# ======================================================================================================================
def normalize_rod_name(key, field="rods"):
    if key not in netl.ROD_ALIASES:
        raise InvalidSpec(f"{field}.{key}", f"unknown rod; allowed: {sorted(netl.ROD_ALIASES)}")
    return netl.ROD_ALIASES[key]


def rod_to_fraction(rod, value, unit="fraction", field=None):
    """Convert a rod position in `unit` to a fraction withdrawn (0 inserted, 1 fully withdrawn). Out of range raises."""
    field = field or f"rods.{rod}"
    if unit not in ROD_UNITS:
        raise InvalidSpec(field, f"unknown unit {unit!r}; allowed: {list(ROD_UNITS)}")
    if not _is_number(value):
        raise InvalidSpec(field, f"value must be a finite number, got {value!r}")
    v = float(value)
    ins = netl.INSERTED_Z_CM[rod]
    if unit == "fraction":
        f, lo, hi = v, 0.0, 1.0
    elif unit == "percent":
        f, lo, hi = v / 100.0, 0.0, 100.0
    elif unit == "console":
        f, lo, hi = v / netl.CONSOLE_MAX, 0.0, netl.CONSOLE_MAX
    elif unit == "cm_withdrawn":
        f, lo, hi = v / netl.ROD_TRAVEL_CM, 0.0, netl.ROD_TRAVEL_CM
    else:  # z_cm
        f, lo, hi = (v - ins) / netl.ROD_TRAVEL_CM, ins, ins + netl.ROD_TRAVEL_CM
    if v < lo - _SLACK * max(1.0, abs(hi)) or v > hi + _SLACK * max(1.0, abs(hi)):
        raise InvalidSpec(field, f"{v} {unit} is outside the allowed range {lo} to {hi} for this rod")
    return round(min(1.0, max(0.0, f)), _ROUND)


def rod_views(rod, fraction):
    """Every unit for a rod fraction, for results and display."""
    ins = netl.INSERTED_Z_CM[rod]
    return {
        "fraction": round(fraction, 9),
        "percent": round(fraction * 100.0, 6),
        "console": round(fraction * netl.CONSOLE_MAX, 6),
        "cm_withdrawn": round(fraction * netl.ROD_TRAVEL_CM, 6),
        "z_cm": round(ins + fraction * netl.ROD_TRAVEL_CM, 6),
    }


def _parse_rod_value(rod, raw, field):
    """raw is a number (fraction) or {"value": x, "unit": u}."""
    if isinstance(raw, dict):
        extra = sorted(set(raw) - {"value", "unit"})
        if extra or "value" not in raw:
            raise InvalidSpec(field, 'a rod value is a number or {"value": x, "unit": u}; got keys ' + str(sorted(raw)))
        return rod_to_fraction(rod, raw["value"], raw.get("unit", "fraction"), field)
    return rod_to_fraction(rod, raw, "fraction", field)


def _merge_rods(current, raw):
    """Apply a rods layer to the current {rod: fraction} dict. A bare value or {"value","unit"} sets all four; a dict sets named rods."""
    all_four = _is_number(raw) or (isinstance(raw, dict) and "value" in raw and set(raw) <= {"value", "unit"})
    if all_four:
        for rod in netl.RODS:
            current[rod] = _parse_rod_value(rod, raw, f"rods.{rod}")
        return
    if not isinstance(raw, dict):
        raise InvalidSpec("rods", f"must be a number, an object of rod values, or {{value, unit}}; got {raw!r}")
    seen = {}
    for key, val in raw.items():
        rod = normalize_rod_name(key)
        if rod in seen:
            raise InvalidSpec(f"rods.{key}", f"sets the same rod as {seen[rod]!r}")
        seen[rod] = key
        current[rod] = _parse_rod_value(rod, val, f"rods.{key}")


# ======================================================================================================================
# the spec
# ======================================================================================================================
@dataclass
class Conditions:
    fuel_temperature_K: float
    non_fuel_temperature_K: float
    water_temperature_K: float
    water_density_gcc: object        # a number in g/cm3, or "auto": liquid water at one atmosphere at water_temperature_K (water.py)
    temperature_method: str

    def validate(self):
        for f in ("fuel_temperature_K", "non_fuel_temperature_K", "water_temperature_K"):
            setattr(self, f, _num(f"conditions.{f}", getattr(self, f), 0.0, 5000.0, lo_open=True))
        if isinstance(self.water_density_gcc, str):
            if self.water_density_gcc != "auto":
                raise InvalidSpec("conditions.water_density_gcc", f"must be a number in g/cm3 or \"auto\", got {self.water_density_gcc!r}")
            if not water.in_range(self.water_temperature_K):
                raise InvalidSpec("conditions.water_density_gcc",
                                  f"\"auto\" needs liquid water at one atmosphere, {water.LOWEST_K} K to {water.HIGHEST_K} K; "
                                  f"conditions.water_temperature_K is {self.water_temperature_K} K. Give a density in g/cm3 instead")
        else:
            self.water_density_gcc = _num("conditions.water_density_gcc", self.water_density_gcc, 0.0, 2.0, lo_open=True)
        _enum("conditions.temperature_method", self.temperature_method, TEMPERATURE_METHODS)
        return self

    def resolved_water_density_gcc(self):
        """The number the builder uses: the given density, or the steam-table density at the water temperature for \"auto\"."""
        if self.water_density_gcc == "auto":
            return water.density_g_cm3(self.water_temperature_K)
        return self.water_density_gcc

    def resolved_temperature_method(self):
        """`auto` becomes interpolation when any temperature is more than 10 K from a library temperature, otherwise nearest."""
        if self.temperature_method != "auto":
            return self.temperature_method
        for t in (self.fuel_temperature_K, self.non_fuel_temperature_K, self.water_temperature_K):
            if min(abs(t - x) for x in netl.XS_TEMPERATURES_K) > netl.XS_TEMPERATURE_TOLERANCE_K:
                return "interpolation"
        return "nearest"


@dataclass
class Fuel:
    source: str
    force_mapped_locations: bool

    def validate(self):
        _enum("fuel.source", self.source, FUEL_SOURCES)
        _bool("fuel.force_mapped_locations", self.force_mapped_locations)
        return self


@dataclass
class Environment:
    pool_radius_cm: float
    beam_port_length_factor: float
    beam_port_1_5_length_cm: float | None
    world_radius_cm: float | None
    world_height_cm: float | None
    barite: bool
    barite_composition_basis: str | None
    detector_geometry: bool

    def validate(self):
        self.pool_radius_cm = _num("environment.pool_radius_cm", self.pool_radius_cm, 0.0, 1000.0, lo_open=True)
        self.beam_port_length_factor = _num("environment.beam_port_length_factor", self.beam_port_length_factor, 0.0, 10.0, lo_open=True)
        self.beam_port_1_5_length_cm = _num("environment.beam_port_1_5_length_cm", self.beam_port_1_5_length_cm, 0.0, 5000.0,
                                            lo_open=True, none_ok=True)
        _bool("environment.barite", self.barite)
        _bool("environment.detector_geometry", self.detector_geometry)
        # The world cylinder and the barite numbers exist only with the barite region: required with it, may be null without it.
        self.world_radius_cm = _num("environment.world_radius_cm", self.world_radius_cm, 0.0, 5000.0, lo_open=True, none_ok=not self.barite)
        self.world_height_cm = _num("environment.world_height_cm", self.world_height_cm, 0.0, 5000.0, lo_open=True, none_ok=not self.barite)
        if self.barite or self.barite_composition_basis is not None:
            _enum("environment.barite_composition_basis", self.barite_composition_basis, BARITE_BASES)
        if self.world_radius_cm is not None and self.world_radius_cm <= self.pool_radius_cm:
            raise InvalidSpec("environment.world_radius_cm", f"must exceed the pool radius ({self.pool_radius_cm}), got {self.world_radius_cm}")
        if self.world_height_cm is not None and self.world_height_cm <= netl.POOL_HEIGHT_CM:
            raise InvalidSpec("environment.world_height_cm", f"must exceed the pool height ({netl.POOL_HEIGHT_CM}), got {self.world_height_cm}")
        return self

    def beam_port_lengths_cm(self):
        """(ports 2 to 4, port 1/5) in cm."""
        common = self.beam_port_length_factor * self.pool_radius_cm
        return common, (common if self.beam_port_1_5_length_cm is None else self.beam_port_1_5_length_cm)


@dataclass
class TalliesOptions:
    library_default_tallies: bool
    energy_bins: object
    polar_bins: int
    azimuthal_bins: int
    mesh: object                    # a name from tallies.MESH_PRESETS, or {dimension, lower_left_cm, upper_right_cm}
    max_total_bins: int

    def validate(self):
        _bool("tallies_options.library_default_tallies", self.library_default_tallies)
        self.energy_bins = energy_bins_mod.resolve(self.energy_bins)
        self.polar_bins = _int("tallies_options.polar_bins", self.polar_bins, 1, 1000)
        self.azimuthal_bins = _int("tallies_options.azimuthal_bins", self.azimuthal_bins, 1, 1000)
        self.max_total_bins = _int("tallies_options.max_total_bins", self.max_total_bins, 1, 10**9)
        m = tallies_mod.resolve_mesh(self.mesh)
        for name, preset_mesh in tallies_mod.MESH_PRESETS.items():     # one spelling per mesh, so equal meshes hash equally
            if tallies_mod.resolve_mesh(preset_mesh) == m:
                m = name
                break
        self.mesh = m
        return self


@dataclass
class NuclearData:
    library: str

    def validate(self):
        _enum("nuclear_data.library", self.library, NUCLEAR_DATA_LIBRARIES)
        return self


@dataclass
class Run:
    size: str
    particles: int | None
    batches: int | None
    inactive: int | None
    seed: int | None
    threads: int | None

    def validate(self):
        _enum("run.size", self.size, tuple(RUN_SIZES))
        self.particles = _int("run.particles", self.particles, 1, none_ok=True)
        self.batches = _int("run.batches", self.batches, 2, none_ok=True)
        self.inactive = _int("run.inactive", self.inactive, 0, none_ok=True)
        self.seed = _int("run.seed", self.seed, 1, 2 ** 63 - 1, none_ok=True)
        self.threads = _int("run.threads", self.threads, 1, 1024, none_ok=True)
        eff = self.effective()
        if eff["inactive"] >= eff["batches"]:
            raise InvalidSpec("run.inactive", f"must be less than batches ({eff['batches']}), got {eff['inactive']}")
        return self

    def effective(self):
        """Particles, batches and inactive batches after `size` and explicit overrides; seed and threads as given."""
        p, b, i = RUN_SIZES[self.size]
        return {"particles": self.particles or p, "batches": self.batches or b,
                "inactive": i if self.inactive is None else self.inactive, "seed": self.seed, "threads": self.threads}


_SECTIONS = {"conditions": Conditions, "fuel": Fuel, "environment": Environment, "tallies_options": TalliesOptions,
             "nuclear_data": NuclearData, "run": Run}
_TOP_KEYS = ("spec_version", "facility", "name", "preset", "config", "pnt", "three_el", "inserts", "rods", "conditions", "fuel", "environment",
             "tallies", "tallies_options", "nuclear_data", "run")


@dataclass
class Spec:
    spec_version: int
    facility: str
    name: str | None
    preset: str | None
    config: int | None
    pnt: str
    three_el: str
    inserts: dict                   # resolved insert hardware (inserts.py)
    rods: dict
    conditions: Conditions
    fuel: Fuel
    environment: Environment
    tallies: list
    tallies_options: TalliesOptions
    nuclear_data: NuclearData
    run: Run

    # ---- construction -------------------------------------------------------------------------------------------------
    @classmethod
    def from_dict(cls, data):
        """Exact, complete dictionary to Spec (no merging, no defaults). Validates."""
        if not isinstance(data, dict):
            raise InvalidSpec("spec", f"must be an object, got {type(data).__name__}")
        unknown = sorted(set(data) - set(_TOP_KEYS))
        if unknown:
            raise InvalidSpec(unknown[0], f"unknown field; allowed: {list(_TOP_KEYS)}")
        missing = [k for k in _TOP_KEYS if k not in data]
        if missing:
            raise InvalidSpec(missing[0], "missing (a spec needs every field; defaults come from a preset)")
        d = copy.deepcopy(data)
        for key, klass in _SECTIONS.items():
            d[key] = _strict(klass, d[key], key)
        spec = cls(**d)
        return spec.validate()

    @classmethod
    def from_json(cls, text):
        try:
            return cls.from_dict(json.loads(text))
        except json.JSONDecodeError as e:
            raise InvalidSpec("spec", f"not valid JSON: {e}") from e

    # ---- validation ---------------------------------------------------------------------------------------------------
    def validate(self):
        if self.spec_version != SPEC_VERSION:
            raise InvalidSpec("spec_version", f"this tool reads spec_version {SPEC_VERSION}, got {self.spec_version!r}")
        _enum("facility", self.facility, FACILITIES)
        if self.name is not None:
            from .safety import validate_name
            validate_name(self.name)
        self.pnt = _insert("pnt", self.pnt, PNT_VALUES)
        self.three_el = _insert("three_el", self.three_el, THREE_EL_VALUES)
        self.inserts = inserts_mod.check(self.inserts, self.pnt, self.three_el)
        if self.config is not None:
            table = _configurations(self.facility, self.preset)
            self.config = _config_number(self.config, table)
            exp = table[self.config]
            if (self.pnt, self.three_el) != (exp[0], exp[1]):
                raise InvalidSpec("config", f"config {self.config} means pnt={exp[0]}, three_el={exp[1]}, but the spec has "
                                            f"pnt={self.pnt}, three_el={self.three_el}")
        if not isinstance(self.rods, dict) or set(self.rods) != set(netl.RODS):
            raise InvalidSpec("rods", f"must hold exactly {list(netl.RODS)}, got {sorted(self.rods) if isinstance(self.rods, dict) else self.rods!r}")
        self.rods = {r: _num(f"rods.{r}", self.rods[r], 0.0, 1.0) for r in netl.RODS}
        for sec in _SECTIONS:
            getattr(self, sec).validate()
        if not isinstance(self.tallies, list) or any(not isinstance(t, str) for t in self.tallies):
            raise InvalidSpec("tallies", "must be a list of tally preset names")
        if self.tallies:
            known = tallies_mod.names(self.facility, self.preset)
            for i, t in enumerate(self.tallies):
                _enum(f"tallies[{i}]", t, known)
        if len(set(self.tallies)) != len(self.tallies):
            raise InvalidSpec("tallies", f"has duplicates: {self.tallies}")
        tallies_mod.check_spec(self)
        return self

    # ---- output -------------------------------------------------------------------------------------------------------
    def to_dict(self):
        d = {k: getattr(self, k) for k in _TOP_KEYS}
        for key in _SECTIONS:
            d[key] = dataclasses.asdict(d[key])
        d["rods"] = {r: self.rods[r] for r in netl.RODS}
        d["tallies"] = list(self.tallies)
        d["inserts"] = copy.deepcopy(self.inserts)
        return copy.deepcopy(d)

    def to_json(self, indent=2):
        return json.dumps(self.to_dict(), indent=indent, sort_keys=False)

    def spec_hash(self):
        """SHA-256 of the physical content. `name` and `preset` are labels, so they are left out."""
        d = self.to_dict()
        d.pop("name"), d.pop("preset")
        return hashlib.sha256(json.dumps(d, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()

    def rod_views(self):
        return {r: rod_views(r, self.rods[r]) for r in netl.RODS}

    def default_name(self):
        cfg = "x" if self.config is None else self.config
        return f"triga-{self.facility}-c{cfg}-{self.spec_hash()[:8]}"


def _insert(field, value, allowed):
    key = value.strip().lower() if isinstance(value, str) else value
    if key is None:
        key = "none"
    norm = _INSERT_SYNONYMS.get(key)
    if norm is None or norm not in allowed:
        raise InvalidSpec(field, f"must be one of {list(allowed)} (also accepted: t, e, f, bare), got {value!r}")
    return norm


# ======================================================================================================================
# resolution: preset + layers -> Spec
# ======================================================================================================================
_FIELD_ALIASES = {"3L": "three_el", "3l": "three_el", "3_el": "three_el", "three-el": "three_el", "three_el": "three_el"}


def _load_layer(spec):
    if spec is None:
        return {}
    if isinstance(spec, (str, os.PathLike)):
        try:
            with open(spec, "r", encoding="utf-8") as fh:
                spec = json.load(fh)
        except (OSError, json.JSONDecodeError) as e:
            raise InvalidSpec("spec", f"cannot read spec file {spec!s}: {e}") from e
    if not isinstance(spec, dict):
        raise InvalidSpec("spec", f"must be a dict or a path to a JSON file, got {type(spec).__name__}")
    return copy.deepcopy(spec)


def _normalize_keys(layer):
    out = {}
    for key, val in layer.items():
        k = _FIELD_ALIASES.get(key, key)
        if k in out:
            raise InvalidSpec(key, "given twice (for example both 3L and three_el)")
        out[k] = val
    return out


def resolve(preset=None, spec=None, **fields):
    """
    Preset, then `spec` (a dict or a JSON file), then keyword fields, merged in that order, validated, named.

        resolve(config=8, rods={"TR": 0.3, "S1": {"value": 600, "unit": "console"}}, run={"size": "standard"})

    Sections merge field by field. `rods` merges rod by rod (a single value sets all four). `tallies` replaces the list.
    `config`, `pnt` and `three_el` are reconciled: config sets both inserts; naming only inserts finds the matching config
    or leaves it None; naming both and disagreeing is an error.
    """
    layer0 = _normalize_keys(_load_layer(spec))
    layer1 = _normalize_keys(fields)
    facility = layer1.get("facility") or layer0.get("facility") or "netl"
    _enum("facility", facility, FACILITIES)
    ref = preset or layer0.pop("preset", None) or presets.default_preset(facility)
    layer0.pop("preset", None)
    canonical, data = presets.get(ref)
    label_table = data.pop("insert_labels")
    # (the preset's own table, the first installed table): labels find their number only in the own table; a number given
    # explicitly may come from any installed pack
    tables = (_configurations(data["facility"], canonical, required=False, own_only=True),
              _configurations(data["facility"], canonical, required=False))

    # Labels: the spec file's labels first, then the keyword labels replace them (a keyword config sets both inserts; a keyword
    # pnt or three_el replaces only that one). Disagreeing labels inside one layer are an error.
    labels0 = _layer_labels(layer0, data, tables)
    if labels0 is not None:
        data["pnt"], data["three_el"], data["config"] = labels0
    explicit = {k: layer1[k] for k in ("config", "pnt", "three_el") if k in layer1}
    insert_layers = []                                   # (labels the layer itself states, or None; its insert fields)
    for li, layer in enumerate((layer0, layer1)):
        if "inserts" in layer:
            insert_layers.append((labels0 if li == 0 else None, layer["inserts"]))
        for key, val in layer.items():
            if key in ("inserts", "config", "pnt", "three_el"):
                continue
            elif key == "rods":
                _merge_rods(data["rods"], val)
            elif key in _SECTIONS:
                if not isinstance(val, dict):
                    raise InvalidSpec(key, f"must be an object of field values, got {type(val).__name__}")
                data[key].update(copy.deepcopy(val))
            elif key == "tallies":
                if not isinstance(val, (list, tuple)):
                    raise InvalidSpec("tallies", "must be a list of tally preset names")
                data["tallies"] = list(val)
            elif key in ("facility", "name", "spec_version"):
                data[key] = val
            else:
                raise InvalidSpec(key, f"unknown field; allowed: {[k for k in _TOP_KEYS if k != 'preset']}")

    _apply_config(data, explicit, tables)
    overrides = {}
    for labels, raw in insert_layers:
        raw = dict(raw) if isinstance(raw, dict) else raw
        if labels is not None and isinstance(raw, dict):    # hardware from a spec file belongs to that file's labels
            if labels[0] != data["pnt"]:
                raw.pop("pnt", None)
            if labels[1] != data["three_el"]:
                raw.pop("three_el", None)
                raw.pop("three_el_empty_state", None)
        overrides = inserts_mod.merge_overrides(overrides, raw)
    data["inserts"] = inserts_mod.resolve_hardware(label_table, data["pnt"], data["three_el"], overrides,
                                                   data["inserts"]["three_el_empty_state"])
    data["preset"] = canonical
    resolved = Spec.from_dict(data)
    if resolved.name is None:
        resolved.name = resolved.default_name()
    return resolved


def _layer_labels(layer, data, tables):
    """
    The (pnt, three_el, config) a layer states itself, or None if it states no label (then its insert fields follow the final
    labels). Labels the layer leaves out come from the preset.
    """
    exp = {k: layer[k] for k in ("config", "pnt", "three_el") if k in layer}
    if not exp:
        return None
    tmp = {"pnt": data["pnt"], "three_el": data["three_el"], "config": data["config"]}
    _apply_config(tmp, exp, tables)
    return tmp["pnt"], tmp["three_el"], tmp["config"]


def _configurations(facility, preset, required=True, own_only=False):
    """{number: (pnt, three_el, description)} from the first facility pack that numbers the configurations (only the preset's own
    pack with `own_only`). When there is none: InvalidSpec on `config` if `required`, else None."""
    _, table = presets.configurations(facility, preset, own_only=own_only)
    if table is None and required:
        raise InvalidSpec("config", f"no installed facility pack numbers the configurations of {facility!r}; give the inserts by "
                                    f"label instead (pnt and three_el)")
    return table


def _config_number(cfg, table):
    if table is None:
        _configurations("netl", None)                    # raises the explanation
    n = _int("config", cfg, min(table), max(table))
    if n not in table:
        raise InvalidSpec("config", f"must be one of {sorted(table)}, got {n}")
    return n


def _config_for(table, pnt, three_el):
    """The configuration number for a (pnt, three_el) pair, or None if the table has none (or there is no table)."""
    for n, (p, t, _) in (table or {}).items():
        if p == pnt and t == three_el:
            return n
    return None


def _apply_config(data, explicit, tables):
    own, first = tables
    cfg = explicit.get("config")
    if "config" in explicit and cfg is not None:
        table = own if own is not None else first
        if table is None:
            _configurations(data.get("facility", "netl"), None)
        n = _config_number(cfg, table)
        p, t, _ = table[n]
        if "pnt" in explicit and _insert("pnt", explicit["pnt"], PNT_VALUES) != p:
            raise InvalidSpec("pnt", f"conflicts with config {n} (pnt={p}); give one or the other")
        if "three_el" in explicit and _insert("three_el", explicit["three_el"], THREE_EL_VALUES) != t:
            raise InvalidSpec("three_el", f"conflicts with config {n} (three_el={t}); give one or the other")
        data.update(config=n, pnt=p, three_el=t)
    elif explicit:
        if "pnt" in explicit:
            data["pnt"] = _insert("pnt", explicit["pnt"], PNT_VALUES)
        if "three_el" in explicit:
            data["three_el"] = _insert("three_el", explicit["three_el"], THREE_EL_VALUES)
        data["config"] = _config_for(own, data["pnt"], data["three_el"])
