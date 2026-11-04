"""
Presets, the assumptions register and the facility data a spec uses. All three come from facility packs (packs.py), not from code:

  - A preset is a complete spec dictionary in a pack's presets.yaml. It is the ONLY place where default values live (the spec
    dataclasses have no defaults), and it is never edited after it has been used: a change is a new version.
  - The assumptions register is each pack's assumptions.yaml. A build lists every assumption that applies to its spec, from the
    packs whose data it uses; the spec's own pack gives the wording when two packs share an id.
  - `facility_data(spec)` says which pack items a spec reads and their labels, so a result can say whether it was built from
    restricted data.

This module must not import `spec` (spec imports this one).
"""
from __future__ import annotations

import copy
import re

from . import data, libraries, packs
from .errors import InvalidSpec, MissingLibrary

_REF = re.compile(r"^([A-Za-z0-9_]+)(?:@(\d+))?$")


def default_preset(facility="netl"):
    """The preset a spec starts from when none is named: a restricted pack's default if one is installed, else a public one's."""
    name = packs.installed().default_preset(facility)
    if name is None:
        raise MissingLibrary(f"no installed facility pack sets a default preset for {facility!r}")
    return name


def get(ref):
    """Return (canonical reference like "netl_boc505@1", deep copy of the preset dict). A bare name means the latest version."""
    reg = packs.installed()
    m = _REF.match(ref or "")
    if not m:
        raise InvalidSpec("preset", f"{ref!r} is not a preset reference; use name or name@version. Available: {available()}")
    name, ver = m.group(1), m.group(2)
    versions = sorted(v for (n, v) in reg.preset_keys() if n == name)
    if not versions:
        raise InvalidSpec("preset", f"unknown preset {name!r}. Available: {available()}" + _not_installed_hint())
    version = int(ver) if ver else versions[-1]
    if version not in versions:
        raise InvalidSpec("preset", f"{name!r} has no version {version}; versions: {versions}")
    return f"{name}@{version}", copy.deepcopy(reg.pack_of(name, version).presets[(name, version)][1])


def _not_installed_hint():
    if any(p.location == "restricted" for p in packs.installed().packs):
        return ""
    return (". No restricted facility pack is installed (AUTOMC_FACILITY_PACKS, or AutoMC_restricted/facility_packs next to AutoMC); "
            "presets that use restricted data live there")


def available():
    return [f"{n}@{v}" for (n, v) in packs.installed().preset_keys()]


def describe():
    reg = packs.installed()
    out = []
    for n, v in reg.preset_keys():
        p = reg.pack_of(n, v)
        out.append({"preset": f"{n}@{v}", "description": p.presets[(n, v)][0], "pack": p.id, "distribution": p.distribution})
    return out


def pack_for(spec_or_preset):
    """The pack of a spec's preset (or of a canonical preset reference), or None."""
    ref = spec_or_preset if isinstance(spec_or_preset, str) or spec_or_preset is None else spec_or_preset.preset
    return packs.installed().pack_for_preset(ref) if ref else None


# ------------------------------------------------------------------------------------------------------------------------
# configuration tables and tally presets from packs
# ------------------------------------------------------------------------------------------------------------------------
def configurations(facility, preset, own_only=False):
    """
    (pack, {number: (pnt, three_el, description)}) from the first pack that numbers the configurations, or (None, None). With
    `own_only`, only the preset's own pack counts: insert labels alone never pull in another pack's numbering; an explicit
    configuration number does.
    """
    reg = packs.installed()
    if own_only:
        pack = reg.pack_for_preset(preset) if preset else None
        raw = pack.data("configurations") if pack is not None else None
    else:
        pack, raw = reg.find(facility, preset, "configurations")
    if raw is None:
        return None, None
    return pack, {int(n): (c["pnt"], c["three_el"], c["description"]) for n, c in raw.items()}


def pack_tally_presets(facility, preset):
    """{name: (pack, definition)} for every tally preset an installed pack provides, the spec's own pack first."""
    out = {}
    for p in packs.installed().ordered(facility, preset):
        for name, d in (p.data("tally_presets") or {}).items():
            out.setdefault(name, (p, d))
    return out


# ------------------------------------------------------------------------------------------------------------------------
# what a spec reads from packs
# ------------------------------------------------------------------------------------------------------------------------
def _uses(spec):
    """(pack, item, kind) for every pack item this spec reads, in a stable order. Raises MissingLibrary if one is not installed."""
    reg = packs.installed()
    fac, ref = spec.facility, spec.preset
    out = []
    own = reg.pack_for_preset(ref) if ref else None
    if own is not None:
        out.append((own, "presets.yaml", "preset"))
    if spec.config is not None:
        pack, _ = reg.find(fac, ref, "configurations")
        if pack is not None:
            out.append((pack, pack.provides["configurations"], "configurations"))
    if spec.fuel.source != "fresh":
        pack, path = reg.find(fac, ref, "fuel_sources", spec.fuel.source)
        if pack is None:
            raise MissingLibrary(f"fuel.source {spec.fuel.source!r} needs a facility pack that provides it; none is installed"
                                 + _not_installed_hint())
        out.append((pack, pack.provides["fuel_sources"][spec.fuel.source], "fuel"))
    if spec.environment.barite:
        pack, _ = reg.find(fac, ref, "materials", data.BARITE_MATERIAL)
        if pack is None:
            raise MissingLibrary("environment.barite needs a facility pack that provides the barite material; none is installed"
                                 + _not_installed_hint())
        out.append((pack, pack.provides["materials"][data.BARITE_MATERIAL], "material"))
    from_packs = pack_tally_presets(fac, ref)
    for pack in {from_packs[t][0].id: from_packs[t][0] for t in spec.tallies if t in from_packs}.values():
        out.append((pack, pack.provides["tally_presets"], "tally presets"))
    profile = libraries.profile_for(spec)
    pack, prof = reg.find(fac, ref, "library_profiles", profile)
    if pack is not None:
        for r in list(prof["snapshots"]) + list(prof["patches"]):
            item = pack.item_of(r)
            if item is not None:
                out.append((pack, item, "library"))
    seen, unique = set(), []
    for p, item, kind in out:
        if (p.id, item) not in seen:
            seen.add((p.id, item))
            unique.append((p, item, kind))
    return unique


def facility_data(spec):
    """
    Which facility data a spec is built from, for result.json and for agents: the overall label ("restricted" if any item is), and
    per pack its label and every item read, with its own label and checksum (folders have none). No local paths.
    """
    by_pack = {}
    for p, item, kind in _uses(spec) + _assumption_items(spec):
        entry = by_pack.setdefault(p.id, {"pack": p.id, "distribution": p.distribution, "location": p.location, "items": []})
        target = p.folder / item
        entry["items"].append({"item": item, "used_for": kind, "distribution": p.items[item]["distribution"],
                               "sha256": packs.sha256_file(target) if target.is_file() else None})
    labels = {it["distribution"] for e in by_pack.values() for it in e["items"]}
    return {"distribution": "restricted" if "restricted" in labels else "public", "packs": list(by_pack.values())}


# ------------------------------------------------------------------------------------------------------------------------
# Assumptions register
# ------------------------------------------------------------------------------------------------------------------------
def _register(packs_in_order):
    """Every assumption of these packs, the first pack's wording winning for a shared id, in the first pack's order."""
    out, ids = [], set()
    for p in packs_in_order:
        for a in p.assumptions:
            if a["id"] not in ids:
                ids.add(a["id"])
                out.append((p, a))
    return out


def _assumption_packs(spec):
    own = pack_for(spec)
    used = {p.id: p for p, _, _ in _uses(spec)}
    ordered = ([own] if own is not None else []) + [p for p in packs.installed().ordered(spec.facility, spec.preset)
                                                    if p is not own and p.id in used]
    return ordered


def _assumption_items(spec):
    contributing = {p.id: p for p, a in _register(_assumption_packs(spec)) if a["test"](spec)}
    return [(p, "assumptions.yaml", "assumptions") for p in contributing.values() if "assumptions.yaml" in p.items]


def all_assumptions(facility="netl", preset=None):
    """The register of a preset's pack (the default preset's when none is given), then the other installed packs' extra entries."""
    reg = packs.installed()
    ref = get(preset or default_preset(facility))[0]
    return [{**{k: v for k, v in a.items() if k not in ("test", "applies")}, "pack": p.id}
            for p, a in _register(reg.ordered(facility, ref))]


def active_assumptions(spec):
    """The assumptions that apply to this resolved spec, each with the values the spec uses for its fields."""
    out = []
    for p, a in _register(_assumption_packs(spec)):
        if not a["test"](spec):
            continue
        out.append({
            "id": a["id"], "title": a["title"], "statement": a["statement"],
            "fields": {f: packs.read_path(spec, f, f"{p.id}, assumption {a['id']}") for f in a["fields"]},
            "confirm_with": a["confirm_with"], "status": a["status"], "pack": p.id,
        })
    return out
