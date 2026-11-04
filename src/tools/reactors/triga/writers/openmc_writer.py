"""
Spec -> OpenMC model.xml for the NETL TRIGA. RUNS ONLY INSIDE THE WORKER PROCESS: it imports OpenMC, CoreForge and
progression_problems, which the calling process never does.

Builds every configuration: the PNT at G-34 and the 3-EL at E-11, F-13 and F-14 from the resolved insert hardware (inserts.py). Everything
the spec says about rods, conditions, fuel, environment and run settings is honored, including the tally presets (tallies.py).
Facility data (fuel compositions, the barite material, tally presets that need a facility's own cells) come from facility packs.

The builder follows the reference script with these deliberate differences, each recorded in the result:
  * fuel is placed at EVERY location of the reference depleted-fuel map, including D-03 (graphite in the library) and G-24 (vacant in the
    pinned library); the forced locations are listed (TRIGA_Input_Builder_Plan.md A10)
  * the default PNT that the pinned library puts at G-34 is removed, because config 0 has none
"""
from __future__ import annotations

import collections
import contextlib
import io

from .. import data, inserts as inserts_mod, libraries, tallies
from ..errors import MissingLibrary, TrigaError
from ..facilities import netl
from ..spec import Spec


def _check_library_constants(G):
    """The spec layer keeps its own copy of the rod geometry; fail loudly if the library disagrees (TRIGA_Input_Builder_Plan.md D8)."""
    pairs = [
        ("transient inserted z", G.TRANSIENT_ROD_FULLY_INSERTED_POSITION, netl.INSERTED_Z_CM["transient"]),
        ("regulating inserted z", G.FFCR_FULLY_INSERTED_POSITION, netl.INSERTED_Z_CM["regulating"]),
        ("transient travel", G.TRANSIENT_ROD_MAX_WITHDRAWAL_DISTANCE, netl.ROD_TRAVEL_CM),
        ("fuel-follower travel", G.FFCR_MAX_WITHDRAWAL_DISTANCE, netl.ROD_TRAVEL_CM),
    ]
    bad = [f"{what}: library {lib} vs spec layer {mine}" for what, lib, mine in pairs if abs(lib - mine) > 1e-9]
    if bad:
        raise MissingLibrary("the active library disagrees with the spec layer's rod geometry (" + "; ".join(bad) + "). "
                             "A new library version needs a new profile; do not edit constants silently.")


def _build_pnt(G, hw, temperature, coolant):
    """The PNT element for the hardware fields: unlined or cadmium-lined terminus, with or without the cadmium wrapper."""
    if hw["terminus"] == "cadmium_lined":
        terminus = G.PNT.cadmium_lined_terminus(temperature=temperature, outer_material=coolant)
    else:
        terminus = G.PNT.unlined_terminus(temperature=temperature, outer_material=coolant)
    wrapper = G.PNT.wrapper(temperature=temperature) if hw["cadmium_wrapper"] else None
    return G.pnt(temperature, coolant, terminus=terminus, wrapper=wrapper)


def _build_three_el(G, NM, ins, temperature, coolant):
    """The 3-EL element for the hardware fields, an empty canister, or None (the slots stay water)."""
    hw = ins["three_el"]
    if hw is None:
        if ins["three_el_empty_state"] != "empty_canister":
            return None
        hw = {"design": "conventional", "liner": "none", "liner_thickness_cm": None, "modified_geometry": None}
    if hw["design"] == "modified":
        return G.modified_three_element_irradiator(temperature, coolant, simplified=(hw["modified_geometry"] == "simplified"))
    if hw["liner"] == "none":
        # the liner region keeps its shape and is filled with air, like the interior: a canister with no absorbing liner
        liner = G.ThreeElementIrradiator.liner(material=NM.air(temperature))
    else:
        material = NM.cadmium(temperature) if hw["liner"] == "cadmium" else NM.lead(temperature)
        t = hw["liner_thickness_cm"]
        liner = G.ThreeElementIrradiator.liner(thickness=t, bottom_thickness=t, material=material)
    return G.three_element_irradiator(temperature, coolant, liner=liner)


def build(spec_dict, outdir, profile):
    """Build the model into `outdir`. Returns {"summary": {...}, "warnings": [...], "files": [relative names]}."""
    import openmc

    spec = Spec.from_dict(spec_dict)
    libraries.verify_active(profile)

    from progression_problems.TRIGA.default_geometries import FuelSpec
    from progression_problems.TRIGA.NETL import problem_5_utils
    from progression_problems.TRIGA.NETL.default_geometries import DefaultGeometries as G
    from progression_problems.TRIGA.NETL.default_materials import DefaultMaterials as NM
    from coreforge.geometry_elements.triga.netl import Core

    _check_library_constants(G)
    openmc.reset_auto_ids()                      # object ids restart, so a fresh process always writes identical files
    warnings = []
    cond, env, fuel = spec.conditions, spec.environment, spec.fuel
    fuel_t, nonfuel_t = cond.fuel_temperature_K, cond.non_fuel_temperature_K
    coolant = NM.water(temperature=cond.water_temperature_K, density=cond.resolved_water_density_gcc())

    # ---- core: fuel, PNT, 3-EL -------------------------------------------------------------------------------------------
    base = G.core(fuel_t, nonfuel_t, coolant)
    fuel_locations = {loc for loc, el in base.full_map.items() if hasattr(el, "fuel_meat")}
    mapped, forced, unplaced = {}, [], []
    mats = {}
    if fuel.source == "depleted_boc505":
        dep = data.depleted_module(data.fuel_module_path(spec))
        mats = dep.make_all_depleted_fuel_materials(temperature=fuel_t)
        mapped = dict(dep.GRID_LOCATION_TO_MCNP_MATERIAL)
        specs = {loc: FuelSpec(material=mats[mid]) for loc, mid in mapped.items() if loc in fuel_locations}
        core = G.core(fuel_t, nonfuel_t, coolant, fuel_materials=specs)
        not_fuel = [loc for loc in mapped if loc not in fuel_locations]
    else:
        core, not_fuel = base, []

    loading = {loc: el for loc, el in core.full_map.items() if loc not in Core.RESERVED_LOCATIONS}
    ins = spec.inserts
    if ins["pnt"] is None:
        loading[netl.PNT_LOCATION] = None        # the pinned library loads an unlined PNT here by default
    else:
        loading[netl.PNT_LOCATION] = _build_pnt(G, ins["pnt"], nonfuel_t, coolant)
    three_el_element = _build_three_el(G, NM, ins, nonfuel_t, coolant)
    for loc in not_fuel:
        if loc in Core.RESERVED_LOCATIONS:
            raise TrigaError(f"fuel map location {loc} is reserved for a control rod or the thimble and cannot hold fuel")
        if fuel.force_mapped_locations:
            template = G.core(fuel_t, nonfuel_t, coolant,
                              fuel_materials={"D-02": FuelSpec(material=mats[mapped[loc]])}).full_map["D-02"]
            loading[loc] = template
            forced.append(loc)
        else:
            unplaced.append(loc)
    if unplaced:
        warnings.append(f"fuel.force_mapped_locations is false: no fuel placed at {sorted(unplaced)} although the depleted-fuel map has it")
    core = Core(pitch=core.pitch, central_thimble=core.central_thimble, transient_rod=core.transient_rod,
                regulating_rod=core.regulating_rod, shim_1_rod=core.shim_1_rod, shim_2_rod=core.shim_2_rod,
                loading=loading, fill_material=core.fill_material, three_element_irradiator=three_el_element, name="core")

    # ---- reactor: rods, pool, beam ports ---------------------------------------------------------------------------------
    inserted = {"transient": G.TRANSIENT_ROD_FULLY_INSERTED_POSITION, "regulating": G.FFCR_FULLY_INSERTED_POSITION,
                "shim_1": G.FFCR_FULLY_INSERTED_POSITION, "shim_2": G.FFCR_FULLY_INSERTED_POSITION}
    travel = {"transient": G.TRANSIENT_ROD_MAX_WITHDRAWAL_DISTANCE, "regulating": G.FFCR_MAX_WITHDRAWAL_DISTANCE,
              "shim_1": G.FFCR_MAX_WITHDRAWAL_DISTANCE, "shim_2": G.FFCR_MAX_WITHDRAWAL_DISTANCE}
    z = {rod: inserted[rod] + spec.rods[rod] * travel[rod] for rod in netl.RODS}

    p_common, p_15 = env.beam_port_lengths_cm()
    # The library places each port from its own length (ports 2 to 4 start at the tank wall and extend outward), so the helpers must be
    # given the new geometry and left to compute the translation. Reusing the default placement with a longer tube would push the
    # tube through the core.
    ports = {
        1: G.Reactor.beam_port_1_5(geometry=G.beam_port(nonfuel_t, length=p_15)),
        2: G.Reactor.beam_port_2(geometry=G.beam_port(nonfuel_t, length=p_common)),
        3: G.Reactor.beam_port_3(geometry=G.beam_port(nonfuel_t, length=p_common)),
        4: G.Reactor.beam_port_4(geometry=G.beam_port(nonfuel_t, length=p_common)),
    }

    reactor = G.reactor(
        fuel_temp=fuel_t, non_fuel_temp=nonfuel_t, coolant=coolant,
        transient_rod_position=z["transient"], regulating_rod_position=z["regulating"],
        shim_1_rod_position=z["shim_1"], shim_2_rod_position=z["shim_2"],
        pool=G.pool(coolant, radius=env.pool_radius_cm), core=core,
        beam_port_1_5=ports[1], beam_port_2=ports[2], beam_port_3=ports[3], beam_port_4=ports[4])

    if env.barite:
        mat = data.barite(spec)                                    # from the facility pack
        barite = openmc.Material(name=mat["name"])
        barite.set_density("g/cm3", mat["density_gcc"])
        percent_type = "wo" if env.barite_composition_basis == "mass" else "ao"
        for element, value in mat["composition"]:
            barite.add_element(element, value, percent_type=percent_type)
        reactor.barite_concrete_material = barite                  # read by the patched reactor builder
        reactor.world_radius_cm = env.world_radius_cm              # also read by it; without them it uses 300 cm
        reactor.world_height_cm = env.world_height_cm

    # ---- model, settings, tallies ----------------------------------------------------------------------------------------
    noise = io.StringIO()
    with contextlib.redirect_stdout(noise):                        # the patched builder prints a debug line
        model = problem_5_utils.build_openmc_model(reactor)
    if noise.getvalue().strip():
        print(noise.getvalue().strip())

    if not spec.tallies_options.library_default_tallies:
        model.tallies = openmc.Tallies()
    n_generic = len(model.tallies)
    preset_tally_names = tallies.add_to_model(model, spec)
    eff = spec.run.effective()
    model.settings.particles = eff["particles"]
    model.settings.batches = eff["batches"]
    model.settings.inactive = eff["inactive"]
    if eff["seed"] is not None:
        model.settings.seed = eff["seed"]
    # tallies.out is a text copy of every tally result. It is redundant with the statepoint and, for the detector presets (about
    # 12 million bins), was 2 GB and took 99 s to write in a 3-batch run. summary.h5 is kept.
    model.settings.output = {"summary": True, "tallies": False}
    method = cond.resolved_temperature_method()
    if method == "interpolation":
        model.settings.temperature = {"method": "interpolation"}

    # ---- write -----------------------------------------------------------------------------------------------------------
    model.export_to_model_xml(str(outdir / "model.xml"))

    # ---- summary ---------------------------------------------------------------------------------------------------------
    cells = model.geometry.get_all_cells().values()
    names = collections.Counter(c.name for c in cells)
    detector_cells = sorted(n for n in names if "detector" in n)
    if env.detector_geometry and not detector_cells:
        raise TrigaError("detector geometry was requested but the model has no detector cells; is the detector patch active?")
    fuel_elements = [el for el in reactor.core.full_map.values() if hasattr(el, "fuel_meat")]
    depleted = [el for el in fuel_elements if el.fuel_meat.material.openmc_material.name.startswith("depleted_fuel_m")]
    placed_from_map = [loc for loc in mapped if loc in reactor.core.full_map and hasattr(reactor.core.full_map[loc], "fuel_meat")]
    bb = model.geometry.bounding_box
    summary = {
        "cells": len(model.geometry.get_all_cells()),
        "materials": len(model.materials),
        "tallies": len(model.tallies),
        "tally_presets": {"requested": list(spec.tallies), "tallies_added": preset_tally_names, "generic_library_default_tallies": n_generic,
                          "bins": tallies.bin_counts(spec)},
        "rods": {rod: {**spec.rod_views()[rod], "library_z_cm": round(float(z[rod]), 6)} for rod in netl.RODS},
        "slots": {loc: (type(reactor.core.full_map[loc]).__name__) for loc in (netl.PNT_LOCATION, *netl.THREE_EL_LOCATIONS)},
        "inserts": {"pnt": spec.pnt, "three_el": spec.three_el, "hardware": spec.inserts,
                    "described": inserts_mod.describe(spec.inserts, spec.pnt, spec.three_el)},
        "fuel": {"elements": len(fuel_elements), "depleted_elements": len(depleted),
                 "mapped_locations": len(mapped), "mapped_locations_holding_fuel": len(placed_from_map),
                 "forced_locations": sorted(forced), "unplaced_locations": sorted(unplaced)},
        "beam_ports": {str(k): {"length_cm": round(float(p.geometry.length), 6), "rotation_deg": float(p.rotation),
                               "translation_cm": [round(float(x), 6) for x in p.translation]} for k, p in ports.items()},
        "detector_cells": detector_cells,
        "fill_cells": {k: names.get(k, 0) for k in ("beam_port_2_water_fill", "beam_port_2_air_fill", "outside_pool_region")},
        "temperature_method": method,
        "water_density_gcc": cond.resolved_water_density_gcc(),
        "settings": {"particles": model.settings.particles, "batches": model.settings.batches,
                     "inactive": model.settings.inactive, "seed": eff["seed"], "threads_for_runner": eff["threads"],
                     "text_tally_output": False},
        "bounding_box_cm": {"lower_left": [float(x) for x in bb.lower_left], "upper_right": [float(x) for x in bb.upper_right]},
    }
    return {"summary": summary, "warnings": warnings, "files": ["model.xml"]}
