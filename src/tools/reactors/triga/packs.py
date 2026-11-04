"""
Facility packs: the facility data the builder reads (presets, the assumptions register, configuration tables, fuel data, materials,
tally presets, library profiles), kept out of the code so that the code can be public while some of the data is not. The format is
documented in AutoMC/src/facility_packs/README.md.

Two places:

    public       AutoMC/src/facility_packs/<pack>/     ships with AutoMC; the pack and every item in it must be labelled public
    restricted   the folders in AUTOMC_FACILITY_PACKS (os.pathsep-separated), by default AutoMC_restricted/facility_packs next to the
                 AutoMC folder; a restricted pack inside the AutoMC folder is refused

A spec's `preset` names its pack. Lookups go to that pack first, then to the other installed packs of the same facility, restricted
packs first, so a public preset can still use a restricted table when one is installed (and the result then says so).

Standard library plus PyYAML. No OpenMC, and no import of spec, presets or libraries (they import this module).
"""
from __future__ import annotations

import copy
import hashlib
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import automc_paths

from .errors import MissingLibrary

FORMAT = 1
DISTRIBUTIONS = ("public", "restricted")
LOCATIONS = ("public", "restricted")
PROVIDE_KINDS = ("configurations", "measured_worths", "tally_presets", "fuel_sources", "materials", "library_profiles",
                 "derived_snapshots", "skills", "eval_answers", "canaries")
_PACK_KEYS = ("format", "pack", "facility", "title", "distribution", "default_preset", "sources", "items", "provides")
_REQUIRED_KEYS = ("format", "pack", "facility", "title", "distribution", "sources", "items")
_FIXED_ITEMS = ("presets.yaml", "assumptions.yaml")
_NOT_ITEMS = ("pack.yaml", "README.md")            # files of the pack folder that describe it and need no label
_ASSUMPTION_KEYS = ("id", "title", "statement", "fields", "confirm_with", "status", "applies")
_PRESET_REF = re.compile(r"^([A-Za-z0-9_]+)@(\d+)$")
_NAME = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.+-]*$")


def _fail(where, message):
    raise MissingLibrary(f"facility pack {where}: {message}")


def _yaml(path, where):
    import yaml
    try:
        return yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except OSError as e:
        _fail(where, f"cannot read {Path(path).name}: {e}")
    except yaml.YAMLError as e:
        _fail(where, f"{Path(path).name} is not valid YAML: {e}")


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


# ----------------------------------------------------------------------------------------------------------------------
# conditions (the `applies` field of an assumption)
# ----------------------------------------------------------------------------------------------------------------------
def read_path(obj, path, where=None):
    """A dotted spec field (environment.barite, inserts.three_el.liner). A path through a null field reads as null; a field that does
    not exist is an error, so a typo in a pack cannot silently switch an assumption off."""
    for part in path.split("."):
        if obj is None:
            return None
        if isinstance(obj, dict):
            if part not in obj:
                _fail(where or "?", f"no spec field {path!r} (no {part!r})")
            obj = obj[part]
        else:
            if not hasattr(obj, part):
                _fail(where or "?", f"no spec field {path!r} (no {part!r})")
            obj = getattr(obj, part)
    return obj


def _same(a, b):
    if isinstance(a, bool) or isinstance(b, bool):         # true matches only true, never 1
        return a is b
    return a == b


def compile_condition(raw, where):
    """
    The `applies` field -> a function of a resolved spec returning True or False.
        always                        every spec
        {path: value, ...}            every path equals its value
        {path: {not: value}}          the path does not equal the value
        {path: {in: [a, b]}}          the path is one of the values
        {path: {not_empty: true}}     the path holds a non-empty list
        {any: [{...}, {...}]}         at least one of the conditions holds (combined with the other keys by "and")
    """
    if raw == "always":
        return lambda spec: True
    if not isinstance(raw, dict) or not raw:
        _fail(where, f"applies must be 'always' or a mapping of spec fields to conditions, got {raw!r}")
    tests = []
    for key, cond in raw.items():
        if key == "any":
            if not isinstance(cond, list) or not cond:
                _fail(where, "applies.any must be a non-empty list of conditions")
            subs = [compile_condition(c, where) for c in cond]
            tests.append(lambda spec, subs=subs: any(f(spec) for f in subs))
            continue
        if not isinstance(key, str) or not key:
            _fail(where, f"applies has a key that is not a spec field: {key!r}")
        if isinstance(cond, dict):
            if len(cond) != 1 or next(iter(cond)) not in ("not", "in", "not_empty"):
                _fail(where, f"applies.{key} must be a value or one of {{not: v}}, {{in: [..]}}, {{not_empty: true}}, got {cond!r}")
            op, arg = next(iter(cond.items()))
            if op == "not":
                tests.append(lambda spec, k=key, v=arg: not _same(read_path(spec, k, where), v))
            elif op == "in":
                if not isinstance(arg, list):
                    _fail(where, f"applies.{key}.in must be a list")
                tests.append(lambda spec, k=key, vs=arg: any(_same(read_path(spec, k, where), v) for v in vs))
            else:
                if arg is not True:
                    _fail(where, f"applies.{key}.not_empty must be true")
                tests.append(lambda spec, k=key: bool(read_path(spec, k, where)))
        else:
            tests.append(lambda spec, k=key, v=cond: _same(read_path(spec, k, where), v))
    return lambda spec: all(t(spec) for t in tests)


# ----------------------------------------------------------------------------------------------------------------------
# one pack
# ----------------------------------------------------------------------------------------------------------------------
@dataclass
class Pack:
    id: str
    facility: str
    title: str
    distribution: str
    location: str                       # "public" or "restricted": where it was found
    folder: Path
    default_preset: str | None
    sources: dict
    items: dict
    provides: dict
    presets: dict = field(default_factory=dict)          # (name, version) -> (description, complete spec dict)
    assumptions: list = field(default_factory=list)      # dicts with the YAML keys plus "test" (compiled `applies`)
    _loaded: dict = field(default_factory=dict, repr=False)

    def path(self, ref):
        """A "pack:" or "snapshots:" reference (or a bare pack-relative path) -> absolute path."""
        if ref.startswith("snapshots:"):
            return automc_paths.SNAPSHOTS_DIR / ref[len("snapshots:"):]
        return self.folder / (ref[len("pack:"):] if ref.startswith("pack:") else ref)

    def item_of(self, ref):
        """The pack item a reference points into, or None for files outside the pack (library snapshots)."""
        if ref.startswith("snapshots:"):
            return None
        rel = ref[len("pack:"):] if ref.startswith("pack:") else ref
        for item in self.items:
            if rel == item or rel.startswith(item.rstrip("/") + "/"):
                return item
        return None

    def data(self, kind):
        """The YAML content of a single-file kind (configurations, measured_worths, tally_presets, eval_answers, canaries), or None if
        not provided."""
        rel = self.provides.get(kind)
        if rel is None:
            return None
        if kind not in self._loaded:
            self._loaded[kind] = _yaml(self.folder / rel, f"{self.id}, {rel}")
        return copy.deepcopy(self._loaded[kind])

    def skills_folder(self):
        """The pack's skills folder (one subfolder per skill, each with a SKILL.md), or None."""
        rel = self.provides.get("skills")
        return self.folder / rel if rel else None

    def material(self, name):
        rel = self.provides.get("materials", {}).get(name)
        if rel is None:
            return None
        key = ("material", name)
        if key not in self._loaded:
            self._loaded[key] = _yaml(self.folder / rel, f"{self.id}, {rel}")
        return copy.deepcopy(self._loaded[key])

    def describe(self):
        return {"pack": self.id, "facility": self.facility, "title": self.title, "distribution": self.distribution,
                "location": self.location, "presets": [f"{n}@{v}" for n, v in sorted(self.presets)]}


def _check_mapping(value, where, what):
    if not isinstance(value, dict):
        _fail(where, f"{what} must be a mapping, got {type(value).__name__}")
    return value


def _merge(base, changes):
    out = copy.deepcopy(base)
    for k, v in changes.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def _load_presets(raw, pack_id, facility):
    where = f"{pack_id}, presets.yaml"
    if raw is None:
        return {}
    _check_mapping(raw, where, "the file")
    entries = {}
    for ref, entry in raw.items():
        m = _PRESET_REF.match(str(ref))
        if not m:
            _fail(where, f"{ref!r} is not a preset reference of the form name@version")
        _check_mapping(entry, where, ref)
        keys = set(entry)
        if not ({"description", "spec"} == keys or {"description", "based_on", "changes"} == keys):
            _fail(where, f"{ref}: needs description and spec, or description, based_on and changes; got {sorted(keys)}")
        entries[(m.group(1), int(m.group(2)))] = entry
    done = {}

    def build(key, seen=()):
        if key in done:
            return done[key]
        if key in seen:
            _fail(where, f"based_on loop through {key[0]}@{key[1]}")
        entry = entries[key]
        if "spec" in entry:
            data = _check_mapping(entry["spec"], where, f"{key[0]}@{key[1]}.spec")
        else:
            m = _PRESET_REF.match(str(entry["based_on"]))
            base = (m.group(1), int(m.group(2))) if m else None
            if base not in entries:
                _fail(where, f"{key[0]}@{key[1]}: based_on {entry['based_on']!r} is not a preset of this pack")
            data = _merge(build(base, seen + (key,))[1], _check_mapping(entry["changes"], where, f"{key[0]}@{key[1]}.changes"))
        if data.get("facility") != facility:
            _fail(where, f"{key[0]}@{key[1]}: facility {data.get('facility')!r} is not the pack's facility {facility!r}")
        done[key] = (str(entry["description"]).strip(), data)
        return done[key]

    for key in entries:
        build(key)
    return done


def _load_assumptions(raw, pack_id):
    where = f"{pack_id}, assumptions.yaml"
    if raw is None:
        return []
    if not isinstance(raw, list):
        _fail(where, "must be a list of assumptions")
    out, ids = [], set()
    for a in raw:
        _check_mapping(a, where, "an assumption")
        if set(a) != set(_ASSUMPTION_KEYS):
            _fail(where, f"assumption {a.get('id')!r} needs exactly {list(_ASSUMPTION_KEYS)}, got {sorted(a)}")
        if a["id"] in ids:
            _fail(where, f"assumption id {a['id']} appears twice")
        ids.add(a["id"])
        if not isinstance(a["fields"], list) or not all(isinstance(f, str) for f in a["fields"]):
            _fail(where, f"{a['id']}.fields must be a list of spec field paths")
        entry = {k: (str(a[k]).strip() if k in ("title", "statement", "confirm_with", "status") else a[k]) for k in _ASSUMPTION_KEYS}
        entry["test"] = compile_condition(a["applies"], f"{pack_id}, assumption {a['id']}")
        out.append(entry)
    return out


def _inside(path, root):
    path, root = Path(path).resolve(), Path(root).resolve()
    return path == root or root in path.parents


def load_pack(folder, location):
    """Read and check one pack folder. `location` is where it was found: "public" (shipped with AutoMC) or "restricted"."""
    folder = Path(folder)
    where = str(folder.name)
    if location not in LOCATIONS:
        _fail(where, f"unknown location {location!r}")
    meta = _check_mapping(_yaml(folder / "pack.yaml", where), where, "pack.yaml")
    unknown = sorted(set(meta) - set(_PACK_KEYS))
    missing = [k for k in _REQUIRED_KEYS if k not in meta]
    if unknown or missing:
        _fail(where, f"pack.yaml: unknown keys {unknown}, missing keys {missing}")
    if meta["format"] != FORMAT:
        _fail(where, f"pack.yaml format {meta['format']!r}; this AutoMC reads format {FORMAT}")
    pack_id = str(meta["pack"])
    where = pack_id
    if not _NAME.match(pack_id):
        _fail(where, "the pack name may use letters, digits and _ . + - only")
    if meta["distribution"] not in DISTRIBUTIONS:
        _fail(where, f"distribution must be one of {DISTRIBUTIONS}, got {meta['distribution']!r}")

    sources = _check_mapping(meta["sources"], where, "sources")
    for sid, s in sources.items():
        _check_mapping(s, where, f"sources.{sid}")
        if set(s) != {"what", "distribution"} or s["distribution"] not in DISTRIBUTIONS:
            _fail(where, f"sources.{sid} needs exactly what and distribution (public or restricted)")

    items = _check_mapping(meta["items"], where, "items")
    for path, it in items.items():
        _check_mapping(it, where, f"items.{path}")
        if set(it) != {"what", "sources", "distribution"} or it["distribution"] not in DISTRIBUTIONS:
            _fail(where, f"items.{path} needs exactly what, sources and distribution (public or restricted)")
        unknown_src = [s for s in it["sources"] if s not in sources]
        if unknown_src:
            _fail(where, f"items.{path} names unknown sources {unknown_src}")
        if it["distribution"] == "public" and any(sources[s]["distribution"] == "restricted" for s in it["sources"]):
            _fail(where, f"items.{path} is labelled public but uses a restricted source")
        if not (folder / path).exists():
            _fail(where, f"items.{path} is listed but does not exist")
    restricted_items = sorted(p for p, it in items.items() if it["distribution"] == "restricted")
    if restricted_items and meta["distribution"] != "restricted":
        _fail(where, f"the pack is labelled {meta['distribution']} but holds restricted items {restricted_items}")
    if location == "public" and meta["distribution"] != "public":
        _fail(where, "a pack shipped with AutoMC (src/facility_packs) must be public; restricted packs live outside the AutoMC folder")
    if meta["distribution"] == "restricted" and _inside(folder, automc_paths.AUTOMC_ROOT):
        _fail(where, f"a restricted pack must not sit inside the AutoMC folder ({folder}); move it out, for example to "
                     f"{automc_paths.DEFAULT_RESTRICTED_PACKS_DIR}")
    for f in folder.rglob("*"):                                  # every file must carry a label
        if f.is_dir() or "__pycache__" in f.parts:
            continue
        rel = f.relative_to(folder).as_posix()
        if rel in _NOT_ITEMS:
            continue
        if not any(rel == p or rel.startswith(p.rstrip("/") + "/") for p in items):
            _fail(where, f"{rel} is not listed in pack.yaml items (every file needs a source and a label)")
    for fixed in _FIXED_ITEMS:
        if (folder / fixed).exists() and fixed not in items:
            _fail(where, f"{fixed} must be listed in items")

    provides = _check_mapping(meta.get("provides") or {}, where, "provides")
    unknown = sorted(set(provides) - set(PROVIDE_KINDS))
    if unknown:
        _fail(where, f"provides: unknown kinds {unknown}; known: {list(PROVIDE_KINDS)}")
    pack = Pack(id=pack_id, facility=str(meta["facility"]), title=str(meta["title"]), distribution=meta["distribution"],
                location=location, folder=folder, default_preset=meta.get("default_preset"), sources=sources, items=items,
                provides=provides)
    for kind in ("configurations", "measured_worths", "tally_presets", "skills", "eval_answers", "canaries"):
        if kind in provides and pack.item_of(provides[kind]) is None:
            _fail(where, f"provides.{kind} must name an item of the pack, got {provides[kind]!r}")
    for kind in ("fuel_sources", "materials"):
        for name, rel in _check_mapping(provides.get(kind, {}), where, f"provides.{kind}").items():
            if pack.item_of(rel) is None:
                _fail(where, f"provides.{kind}.{name} must name an item of the pack, got {rel!r}")
    for name, prof in _check_mapping(provides.get("library_profiles", {}), where, "provides.library_profiles").items():
        if set(prof) != {"snapshots", "commits", "patches"}:
            _fail(where, f"provides.library_profiles.{name} needs exactly snapshots, commits and patches")
        for ref in list(prof["snapshots"]) + list(prof["patches"]):
            if not (ref.startswith("pack:") or ref.startswith("snapshots:")):
                _fail(where, f"library profile {name}: {ref!r} must start with pack: or snapshots:")
            if ref.startswith("pack:") and pack.item_of(ref) is None:
                _fail(where, f"library profile {name}: {ref!r} is not an item of the pack")

    pack.presets = _load_presets(_yaml(folder / "presets.yaml", where) if (folder / "presets.yaml").exists() else None,
                                 pack_id, pack.facility)
    pack.assumptions = _load_assumptions(_yaml(folder / "assumptions.yaml", where) if (folder / "assumptions.yaml").exists() else None,
                                         pack_id)
    if pack.default_preset is not None and pack.default_preset not in {n for n, _ in pack.presets}:
        _fail(where, f"default_preset {pack.default_preset!r} is not a preset of this pack")
    return pack


# ----------------------------------------------------------------------------------------------------------------------
# all installed packs
# ----------------------------------------------------------------------------------------------------------------------
class Registry:
    """The installed packs, restricted first, with the lookups the builder needs."""

    def __init__(self, packs):
        self.packs = list(packs)
        ids, presets = {}, {}
        for p in self.packs:
            if p.id in ids:
                _fail(p.id, f"two packs have this name: {ids[p.id]} and {p.folder}")
            ids[p.id] = p.folder
            for key in p.presets:
                if key in presets:
                    _fail(p.id, f"preset {key[0]}@{key[1]} is also in pack {presets[key].id}")
                presets[key] = p
        self._preset_pack = presets
        self._defaults = {}
        for loc in ("restricted", "public"):                    # a restricted pack's default wins over a public one
            for p in self.packs:
                if p.location == loc and p.default_preset:
                    have = self._defaults.get(p.facility)
                    if have is None:
                        self._defaults[p.facility] = (loc, p.default_preset, p.id)
                    elif have[0] == loc:
                        _fail(p.id, f"two {loc} packs set a default preset for {p.facility}: {have[2]} and {p.id}")

    def preset_keys(self):
        return sorted(self._preset_pack)

    def pack_of(self, name, version):
        return self._preset_pack.get((name, version))

    def pack_for_preset(self, ref):
        """The pack that holds a canonical preset reference like netl_boc505@1, or None."""
        m = _PRESET_REF.match(ref or "")
        return self._preset_pack.get((m.group(1), int(m.group(2)))) if m else None

    def default_preset(self, facility):
        d = self._defaults.get(facility)
        return d[1] if d else None

    def ordered(self, facility, preset=None):
        """The packs of a facility in lookup order: the preset's own pack, then the others (restricted first, as installed)."""
        own = self.pack_for_preset(preset) if preset else None
        rest = [p for p in self.packs if p.facility == facility and p is not own]
        return ([own] if own is not None else []) + rest

    def find(self, facility, preset, kind, name=None):
        """
        The first pack (in lookup order) that provides `kind` (and `name` within it), and the value: for single-file kinds the loaded
        YAML, for fuel_sources the absolute file path, for materials the loaded YAML, for library_profiles the profile dict.
        (None, None) if no installed pack provides it.
        """
        for p in self.ordered(facility, preset):
            if kind in ("configurations", "measured_worths", "tally_presets"):
                value = p.data(kind)
                if value is not None:
                    return p, value
            elif kind == "fuel_sources":
                rel = p.provides.get("fuel_sources", {}).get(name)
                if rel is not None:
                    return p, p.path(rel)
            elif kind == "materials":
                value = p.material(name)
                if value is not None:
                    return p, value
            elif kind == "library_profiles":
                prof = p.provides.get("library_profiles", {}).get(name)
                if prof is not None:
                    return p, prof
            else:
                raise ValueError(f"unknown kind {kind!r}")
        return None, None

    def names(self, facility, preset, kind):
        """Every name of a mapping kind (fuel_sources, materials, library_profiles, tally_presets) the installed packs provide."""
        out = set()
        for p in self.ordered(facility, preset):
            if kind == "tally_presets":
                out |= set(p.data(kind) or {})
            else:
                out |= set(p.provides.get(kind, {}))
        return out


def restricted_dirs(environ=None):
    environ = os.environ if environ is None else environ
    raw = environ.get("AUTOMC_FACILITY_PACKS")
    if raw:
        return [Path(x).expanduser() for x in raw.split(os.pathsep) if x.strip()]
    return [automc_paths.DEFAULT_RESTRICTED_PACKS_DIR]


def discover(public=None, restricted=None):
    """Load every pack folder (a folder holding pack.yaml) in the given places. Defaults: the shipped and the configured folders."""
    public = [automc_paths.PUBLIC_PACKS_DIR] if public is None else list(public)
    restricted = restricted_dirs() if restricted is None else list(restricted)
    found = []
    for location, roots in (("restricted", restricted), ("public", public)):
        for root in roots:
            root = Path(root)
            if not root.is_dir():
                continue
            for folder in sorted(p for p in root.iterdir() if (p / "pack.yaml").is_file()):
                found.append(load_pack(folder, location))
    return Registry(found)


_registry = None


def installed():
    """The installed packs (read once per process; `reload()` reads them again)."""
    global _registry
    if _registry is None:
        _registry = discover()
    return _registry


def reload():
    global _registry
    _registry = None
    return installed()
