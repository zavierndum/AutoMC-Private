"""
Facility data the builder needs that is not code, read from facility packs (packs.py): fuel compositions and materials.

  - A fuel source other than "fresh" is a Python module in a pack (for example the depleted BOC505 fuel of the restricted NETL pack,
    assumption A9), loaded by file path inside the build worker: make_all_depleted_fuel_materials(temperature) and
    GRID_LOCATION_TO_MCNP_MATERIAL.
  - The barite concrete is a YAML file in a pack: name, density, and the composition exactly as its source writes it.

The parent process finds them (`fuel_module_path`, `barite`) and hands them to the worker; nothing here imports OpenMC.
"""
from __future__ import annotations

import importlib.util
import math
from pathlib import Path

from . import packs
from .errors import MissingLibrary

BARITE_MATERIAL = "barite_concrete"          # the name a pack provides the barite region's material under

_cache = {}


def fuel_module_path(spec):
    """Absolute path of the module for spec.fuel.source, or None for fresh fuel. Raises MissingLibrary if no pack provides it."""
    if spec.fuel.source == "fresh":
        return None
    _, path = packs.installed().find(spec.facility, spec.preset, "fuel_sources", spec.fuel.source)
    if path is None:
        raise MissingLibrary(f"fuel.source {spec.fuel.source!r}: no installed facility pack provides it (restricted data live in a "
                             f"pack outside AutoMC, see AUTOMC_FACILITY_PACKS)")
    return Path(path)


def depleted_module(path):
    """The fuel module at `path`: make_all_depleted_fuel_materials(temperature, density) and GRID_LOCATION_TO_MCNP_MATERIAL."""
    path = Path(path)
    if path in _cache:
        return _cache[path]
    if not path.is_file():
        raise MissingLibrary(f"fuel data not found at {path}; the facility pack that lists it is incomplete")
    spec = importlib.util.spec_from_file_location(f"triga_fuel_{path.stem}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _cache[path] = module
    return module


def barite(spec):
    """The barite material {name, density_gcc, composition: [[element, number], ...]} from a pack, or None when barite is off."""
    if not spec.environment.barite:
        return None
    pack, mat = packs.installed().find(spec.facility, spec.preset, "materials", BARITE_MATERIAL)
    if mat is None:
        raise MissingLibrary("environment.barite: no installed facility pack provides the barite material (restricted data live in a "
                             "pack outside AutoMC, see AUTOMC_FACILITY_PACKS)")
    where = f"{pack.id}, {pack.provides['materials'][BARITE_MATERIAL]}"
    ok = (isinstance(mat, dict) and set(mat) == {"name", "density_gcc", "composition"} and isinstance(mat["name"], str)
          and isinstance(mat["density_gcc"], (int, float)) and math.isfinite(mat["density_gcc"]) and mat["density_gcc"] > 0
          and isinstance(mat["composition"], list) and mat["composition"]
          and all(isinstance(x, list) and len(x) == 2 and isinstance(x[0], str) and isinstance(x[1], (int, float)) and x[1] > 0
                  for x in mat["composition"]))
    if not ok:
        raise MissingLibrary(f"facility pack {where}: a material needs name, density_gcc > 0 and composition [[element, number], ...]")
    return mat
