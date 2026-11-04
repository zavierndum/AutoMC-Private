"""
Insert hardware: what physically sits in the PNT slot (G-34) and the 3-EL slots (E-11, F-13, F-14). Standard library only.

The user writes labels (`pnt = "thermal"`, `three_el = "fast"`, or `config = 8`). Each label resolves to a hardware description, taken from the
preset's label table and then overridden field by field by anything the user gives under `inserts`. The resolved hardware is stored in the
spec, so the spec hash covers what is actually built, and an old spec still builds the same hardware after the table changes.

    inserts.pnt                   null when pnt is "none", else {terminus, cadmium_wrapper}
    inserts.three_el              null when three_el is "none", else {design, liner, liner_thickness_cm, modified_geometry}
    inserts.three_el_empty_state  what the 3-EL slots hold when three_el is "none" ("water" or "empty_canister"); null otherwise

Rules: a field that does not apply is null (so it cannot change the hash of an unrelated build); hardware given for a facility whose label
is "none" is an error; hardware that came with a spec file for one label is dropped when the label is changed, because it described the
other label.
"""
from __future__ import annotations

import copy
import math

from .errors import InvalidSpec
from .facilities import netl

TERMINI = ("unlined", "cadmium_lined")
DESIGNS = ("conventional", "modified")
LINERS = ("cadmium", "lead", "none")
MODIFIED_GEOMETRIES = ("detailed", "simplified")
EMPTY_STATES = ("water", "empty_canister")
PNT_KEYS = ("terminus", "cadmium_wrapper")
THREE_EL_KEYS = ("design", "liner", "liner_thickness_cm", "modified_geometry")
INSERT_KEYS = ("pnt", "three_el", "three_el_empty_state")


def _keys(raw, keys, path):
    if not isinstance(raw, dict):
        raise InvalidSpec(path, f"must be an object or null, got {type(raw).__name__}")
    unknown = sorted(set(raw) - set(keys))
    if unknown:
        raise InvalidSpec(f"{path}.{unknown[0]}", f"unknown field; allowed: {list(keys)}")


def check_pnt(hw, path="inserts.pnt"):
    """A complete PNT hardware dict -> validated copy."""
    _keys(hw, PNT_KEYS, path)
    missing = [k for k in PNT_KEYS if k not in hw]
    if missing:
        raise InvalidSpec(f"{path}.{missing[0]}", "missing")
    if hw["terminus"] not in TERMINI:
        raise InvalidSpec(f"{path}.terminus", f"must be one of {list(TERMINI)}, got {hw['terminus']!r}")
    if not isinstance(hw["cadmium_wrapper"], bool):
        raise InvalidSpec(f"{path}.cadmium_wrapper", f"must be true or false, got {hw['cadmium_wrapper']!r}")
    return dict(hw)


def check_three_el(hw, path="inserts.three_el"):
    """A complete 3-EL hardware dict -> validated copy. Fields that do not apply to the design must be null."""
    _keys(hw, THREE_EL_KEYS, path)
    missing = [k for k in THREE_EL_KEYS if k not in hw]
    if missing:
        raise InvalidSpec(f"{path}.{missing[0]}", "missing (use null for a field that does not apply)")
    design = hw["design"]
    if design not in DESIGNS:
        raise InvalidSpec(f"{path}.design", f"must be one of {list(DESIGNS)}, got {design!r}")
    out = dict(hw)
    if design == "conventional":
        if hw["liner"] not in LINERS:
            raise InvalidSpec(f"{path}.liner", f"must be one of {list(LINERS)} for the conventional 3-EL, got {hw['liner']!r}")
        if hw["modified_geometry"] is not None:
            raise InvalidSpec(f"{path}.modified_geometry", "applies only to the modified 3-EL; use null")
        t = hw["liner_thickness_cm"]
        if hw["liner"] == "none":
            if t is not None:
                raise InvalidSpec(f"{path}.liner_thickness_cm", "applies only with a cadmium or lead liner; use null")
        else:
            if isinstance(t, bool) or not isinstance(t, (int, float)) or not math.isfinite(t):
                raise InvalidSpec(f"{path}.liner_thickness_cm", f"must be a number in cm, got {t!r}")
            if not 0.0 < t < netl.THREE_EL_MAX_LINER_THICKNESS_CM:
                raise InvalidSpec(f"{path}.liner_thickness_cm",
                                  f"must be above 0 and below {netl.THREE_EL_MAX_LINER_THICKNESS_CM} cm (the gap between the inner sleeve "
                                  f"and the outer casing), got {t}")
            out["liner_thickness_cm"] = float(t)
    else:
        if hw["liner"] is not None or hw["liner_thickness_cm"] is not None:
            raise InvalidSpec(f"{path}.liner", "the modified 3-EL has its own filters; liner and liner_thickness_cm must be null")
        if hw["modified_geometry"] not in MODIFIED_GEOMETRIES:
            raise InvalidSpec(f"{path}.modified_geometry", f"must be one of {list(MODIFIED_GEOMETRIES)}, got {hw['modified_geometry']!r}")
    return out


def check(inserts, pnt_label, three_el_label, path="inserts"):
    """A complete `inserts` dict, checked against the labels. Returns a validated copy."""
    _keys(inserts, INSERT_KEYS, path)
    missing = [k for k in INSERT_KEYS if k not in inserts]
    if missing:
        raise InvalidSpec(f"{path}.{missing[0]}", "missing (a spec needs every field; defaults come from a preset)")
    out = {}
    if pnt_label == "none":
        if inserts["pnt"] is not None:
            raise InvalidSpec(f"{path}.pnt", "is given but pnt is none; remove it or choose a PNT")
        out["pnt"] = None
    else:
        if inserts["pnt"] is None:
            raise InvalidSpec(f"{path}.pnt", f"is missing for pnt = {pnt_label}")
        out["pnt"] = check_pnt(inserts["pnt"], f"{path}.pnt")
    if three_el_label == "none":
        if inserts["three_el"] is not None:
            raise InvalidSpec(f"{path}.three_el", "is given but three_el is none; remove it or choose a 3-EL")
        if inserts["three_el_empty_state"] not in EMPTY_STATES:
            raise InvalidSpec(f"{path}.three_el_empty_state", f"must be one of {list(EMPTY_STATES)} when three_el is none, "
                                                             f"got {inserts['three_el_empty_state']!r}")
        out["three_el"] = None
        out["three_el_empty_state"] = inserts["three_el_empty_state"]
    else:
        if inserts["three_el"] is None:
            raise InvalidSpec(f"{path}.three_el", f"is missing for three_el = {three_el_label}")
        if inserts["three_el_empty_state"] is not None:
            raise InvalidSpec(f"{path}.three_el_empty_state", "applies only when three_el is none; use null")
        out["three_el"] = check_three_el(inserts["three_el"], f"{path}.three_el")
        out["three_el_empty_state"] = None
    return out


# ----------------------------------------------------------------------------------------------------------------------
# resolution from labels
# ----------------------------------------------------------------------------------------------------------------------
def merge_overrides(current, layer, path="inserts"):
    """Deep-merge one layer of user-given insert fields into `current` (both partial dicts). A null replaces."""
    if not isinstance(layer, dict):
        raise InvalidSpec(path, f"must be an object, got {type(layer).__name__}")
    _keys(layer, INSERT_KEYS, path)
    out = copy.deepcopy(current)
    for key, val in layer.items():
        if key in ("pnt", "three_el") and isinstance(val, dict):
            allowed = PNT_KEYS if key == "pnt" else THREE_EL_KEYS
            _keys(val, allowed, f"{path}.{key}")
            base = out.get(key) if isinstance(out.get(key), dict) else {}
            out[key] = {**base, **copy.deepcopy(val)}
        else:
            out[key] = copy.deepcopy(val)
    return out


def resolve_hardware(table, pnt_label, three_el_label, overrides, default_empty_state):
    """
    Labels plus partial overrides -> a complete `inserts` dict (not yet checked). `table` is the preset's label table:
    {"pnt": {label: hardware}, "three_el": {label: hardware}}.
    """
    out = {}
    for facility, label in (("pnt", pnt_label), ("three_el", three_el_label)):
        given = overrides.get(facility)
        if label == "none":
            if isinstance(given, dict):
                raise InvalidSpec(f"inserts.{facility}", f"is given but {facility} is none; remove it or choose a "
                                                         f"{'PNT' if facility == 'pnt' else '3-EL'}")
            out[facility] = None
            continue
        if label not in table[facility]:
            raise InvalidSpec(facility, f"the preset has no hardware for {facility} = {label}")
        hw = copy.deepcopy(table[facility][label])
        if isinstance(given, dict):
            if facility == "three_el" and given.get("design") and given["design"] != hw["design"]:
                # switching design: the fields of the other design do not carry over
                hw = {"design": given["design"], "liner": None, "liner_thickness_cm": None, "modified_geometry": None}
            hw.update(given)
            if facility == "three_el" and hw.get("design") == "conventional" and "liner_thickness_cm" not in given:
                if hw.get("liner") == "none":
                    hw["liner_thickness_cm"] = None                 # no liner, no thickness
                elif hw.get("liner") in ("cadmium", "lead") and hw.get("liner_thickness_cm") is None:
                    hw["liner_thickness_cm"] = netl.THREE_EL_LINER_THICKNESS_CM
        out[facility] = hw
    if three_el_label == "none":
        out["three_el_empty_state"] = overrides.get("three_el_empty_state", default_empty_state)
    else:
        if overrides.get("three_el_empty_state") is not None:
            raise InvalidSpec("inserts.three_el_empty_state", "applies only when three_el is none")
        out["three_el_empty_state"] = None
    return out


def describe(inserts, pnt_label, three_el_label):
    """One short line per slot, for summaries and warnings."""
    p = inserts["pnt"]
    t = inserts["three_el"]
    pnt = "water (no PNT)" if p is None else (f"PNT, {p['terminus'].replace('_', '-')} terminus"
                                              f"{', cadmium wrapper' if p['cadmium_wrapper'] else ''}")
    if t is None:
        three = "water (no 3-EL)" if inserts["three_el_empty_state"] == "water" else "empty 3-EL canister (no liner)"
    elif t["design"] == "modified":
        three = f"modified 3-EL ({t['modified_geometry']} geometry)"
    else:
        three = "conventional 3-EL, no liner" if t["liner"] == "none" else \
            f"conventional 3-EL, {t['liner']} liner {t['liner_thickness_cm']:g} cm"
    return {"G-34": pnt, "E-11/F-13/F-14": three}
