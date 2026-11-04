"""
Parameter coverage (Block 2, B2-3): changing one spec field changes the intended parts of the model and nothing else.

Part 1 runs everywhere: the steam-table density, `conditions.water_density_gcc = "auto"`, and the new warnings.
Part 2 builds a base model and one variant per parameter in the WSL automc environment (about 20 builds, four at a time, about 1 minute),
reduces each model.xml to a signature (tools/openmc/model_signature.py: materials, cells, surfaces, settings, tallies; object ids are
ignored) and checks which parts differ. No transport is run.

A parameter that changes nothing is a defect: this test found that `environment.world_radius_cm` and `world_height_cm` were silently
ignored, because the detector patch hardcoded a 300 x 300 cm world cylinder.
"""
import json
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from tools.reactors.triga import InvalidSpec, advice, build_input, data, water
from tools.reactors.triga.spec import Spec, resolve

from tests.reactors.triga.test_build_config0 import SKIP, setUpModule as _environment  # noqa: E402  (environment guard, cross sections)
from tests.reactors.triga._packs import require_restricted  # noqa: E402


def setUpModule():
    require_restricted()
    _environment()

from automc_paths import SRC  # noqa: E402


def cli(*args):
    p = subprocess.run([sys.executable, "-m", "automc", "triga", *args], cwd=SRC, capture_output=True, text=True, timeout=120)
    return p.returncode, p.stdout, p.stderr


def load_signature_module():
    from tools.openmc import model_signature
    return model_signature


class SteamTable(unittest.TestCase):
    def test_known_values(self):
        known = {0: 0.999840, 4: 0.999972, 20: 0.998207, 25: 0.997047, 40: 0.992219, 60: 0.983201, 80: 0.971800, 100: 0.958354}
        for celsius, rho in known.items():
            with self.subTest(celsius=celsius):
                self.assertAlmostEqual(water.density_g_cm3(celsius + 273.15), rho, delta=3e-5)

    def test_range_is_liquid_water_at_one_atmosphere(self):
        for bad in (273.0, 373.2, 500.0):
            with self.subTest(T=bad):
                with self.assertRaises(ValueError):
                    water.density_g_cm3(bad)
        self.assertTrue(water.in_range(273.15) and water.in_range(373.15) and not water.in_range(400.0))

    def test_density_falls_above_four_celsius(self):
        values = [water.density_g_cm3(t) for t in (280, 300, 320, 340, 360)]
        self.assertEqual(values, sorted(values, reverse=True))


class WaterDensityAuto(unittest.TestCase):
    def test_auto_follows_the_water_temperature(self):
        s = resolve(conditions={"water_temperature_K": 320.0, "water_density_gcc": "auto"})
        self.assertEqual(s.conditions.water_density_gcc, "auto")
        self.assertAlmostEqual(s.conditions.resolved_water_density_gcc(), water.density_g_cm3(320.0), places=12)
        self.assertEqual(s.to_dict()["conditions"]["water_density_gcc"], "auto")
        self.assertEqual(Spec.from_json(s.to_json()).to_dict(), s.to_dict())

    def test_a_number_is_used_as_given(self):
        self.assertEqual(resolve().conditions.resolved_water_density_gcc(), 1.0)               # the reference default is unchanged
        self.assertEqual(resolve(conditions={"water_density_gcc": 0.97}).conditions.resolved_water_density_gcc(), 0.97)

    def test_auto_and_a_number_hash_differently(self):
        self.assertNotEqual(resolve(conditions={"water_density_gcc": "auto"}).spec_hash(), resolve().spec_hash())

    def test_bad_values_name_the_field(self):
        for cond in ({"water_density_gcc": "heavy"}, {"water_density_gcc": "auto", "water_temperature_K": 400.0},
                     {"water_density_gcc": "auto", "water_temperature_K": 250.0}, {"water_density_gcc": -1.0}, {"water_density_gcc": None}):
            with self.subTest(cond=cond):
                with self.assertRaises(InvalidSpec) as ctx:
                    resolve(conditions=cond)
                self.assertEqual(ctx.exception.field, "conditions.water_density_gcc")

    def test_command_line_accepts_auto(self):
        code, out, err = cli("spec", "--set", "conditions.water_density_gcc=auto", "--set", "conditions.water_temperature_K=310")
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["conditions"]["water_density_gcc"], "auto")


class NewWarnings(unittest.TestCase):
    def test_the_default_has_no_warnings(self):
        self.assertEqual(advice.for_spec(resolve(), cpu_count=16), [])

    def test_inconsistent_water_density(self):
        msgs = advice.for_spec(resolve(conditions={"water_temperature_K": 350.0}), cpu_count=16)
        self.assertTrue(any("water_density_gcc" in m and "above the steam-table density" in m and "auto" in m for m in msgs), msgs)
        msgs = advice.for_spec(resolve(conditions={"water_temperature_K": 280.0, "water_density_gcc": 0.95}), cpu_count=16)
        self.assertTrue(any("below the steam-table density" in m for m in msgs), msgs)

    def test_consistent_or_auto_water_density_is_quiet(self):
        self.assertEqual(advice.for_spec(resolve(conditions={"water_temperature_K": 350.0, "water_density_gcc": "auto"}), cpu_count=16), [])
        near = water.density_g_cm3(320.0) * 1.003                                 # inside the 0.5 percent tolerance
        self.assertEqual(advice.for_spec(resolve(conditions={"water_temperature_K": 320.0, "water_density_gcc": near}), cpu_count=16), [])

    def test_hot_water_outside_the_liquid_range_is_not_judged(self):
        msgs = advice.for_spec(resolve(conditions={"water_temperature_K": 450.0}), cpu_count=16)
        self.assertFalse(any("steam-table" in m for m in msgs), msgs)

    def test_thin_barite_region(self):
        msgs = advice.for_spec(resolve(environment={"world_radius_cm": 160.0}), cpu_count=16)
        self.assertTrue(any("barite region is only 10 cm thick" in m for m in msgs), msgs)
        msgs = advice.for_spec(resolve(environment={"world_height_cm": 170.0}), cpu_count=16)
        self.assertTrue(any("barite region is only 5 cm thick" in m for m in msgs), msgs)

    def test_world_fields_without_barite_have_no_effect(self):
        off = {"barite": False, "detector_geometry": False}
        msgs = advice.for_spec(resolve(environment={**off, "world_radius_cm": 400.0}), cpu_count=16)
        self.assertTrue(any("have no effect while environment.barite is false" in m for m in msgs), msgs)
        self.assertEqual(advice.for_spec(resolve(environment=off), cpu_count=16), [])


# ======================================================================================================================
# real builds
# ======================================================================================================================
TINY = {"run": {"particles": 1000, "batches": 3, "inactive": 1}}

#            label                    spec fields that change
VARIANTS = {
    "fuel_temperature": {"conditions": {"fuel_temperature_K": 320.0}},
    "non_fuel_temperature": {"conditions": {"non_fuel_temperature_K": 320.0}},
    "water_temperature": {"conditions": {"water_temperature_K": 320.0}},
    "water_density": {"conditions": {"water_density_gcc": 0.98}},
    "water_density_auto": {"conditions": {"water_temperature_K": 320.0, "water_density_gcc": "auto"}},
    "temperature_method": {"conditions": {"temperature_method": "interpolation"}},
    "fuel_fresh": {"fuel": {"source": "fresh"}},
    "fuel_not_forced": {"fuel": {"force_mapped_locations": False}},
    "pool_radius": {"environment": {"pool_radius_cm": 120.0}},
    "port_length_factor": {"environment": {"beam_port_length_factor": 1.5}},
    "port_1_5_length": {"environment": {"beam_port_1_5_length_cm": 600.0}},
    "world_radius": {"environment": {"world_radius_cm": 400.0}},
    "world_height": {"environment": {"world_height_cm": 400.0}},
    "no_barite_no_detectors": {"environment": {"barite": False, "detector_geometry": False}},
    "barite_mass_basis": {"environment": {"barite_composition_basis": "mass"}},
    "particles": {"run": {"particles": 2000}},
    "seed": {"run": {"seed": 9}},
    "no_library_tallies": {"tallies_options": {"library_default_tallies": False}},
    "unused_angle_bins": {"tallies_options": {"polar_bins": 5}},
    "rod": {"rods": {"transient": 0.9}},
}


def merged(base, extra):
    out = {k: dict(v) if isinstance(v, dict) else v for k, v in base.items()}
    for k, v in extra.items():
        out[k] = {**out.get(k, {}), **v} if isinstance(v, dict) else v
    return out


@unittest.skipIf(SKIP, SKIP or "")
class EveryParameterChangesWhatItShould(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ms = load_signature_module()
        cls.ws = Path(tempfile.mkdtemp(prefix="triga_params_"))

        def one(item):
            label, fields = item
            r = build_input("openmc", "netl", name=label.replace("_", "-"), workspace=cls.ws, **merged(TINY, fields))
            return label, r

        with ThreadPoolExecutor(max_workers=4) as pool:
            built = dict(pool.map(one, [("base", {})] + list(VARIANTS.items())))
        cls.results = built
        cls.base = cls.ms.signature(Path(built["base"].outdir) / "model.xml")
        cls.diff = {k: cls.ms.compare(cls.base, cls.ms.signature(Path(r.outdir) / "model.xml")) for k, r in built.items() if k != "base"}

    def parts(self, label):
        return set(self.diff[label])

    def changed_names(self, label, part="materials"):
        return set(self.diff[label][part]["names"])

    def water_densities(self, label):
        sig = self.ms.signature(Path(self.results[label].outdir) / "model.xml")
        return {float(k[2]) for k in sig["materials"] if k[0] == "Water"}

    # ---- conditions -------------------------------------------------------------------------------------------------
    def test_fuel_temperature_changes_only_fuel_materials(self):
        self.assertEqual(self.parts("fuel_temperature"), {"materials", "settings"})
        names = self.changed_names("fuel_temperature")
        self.assertTrue(all(n.startswith("depleted_fuel_m") or n in ("Fuel_Follower_Fuel", "Zirc_Filler") for n in names), names)
        n_map = len(data.depleted_module(data.fuel_module_path(resolve())).GRID_LOCATION_TO_MCNP_MATERIAL)
        self.assertEqual(sum(1 for n in names if n.startswith("depleted_fuel_m")), n_map)
        self.assertNotIn("Water", names)

    def test_non_fuel_temperature_leaves_fuel_and_water_alone(self):
        self.assertEqual(self.parts("non_fuel_temperature"), {"materials", "settings"})
        names = self.changed_names("non_fuel_temperature")
        self.assertTrue({"Aluminum", "Graphite", "Air"} <= names, names)
        self.assertEqual({n for n in names if n.startswith("depleted_fuel_m") or n == "Water"}, set())

    def test_water_temperature_changes_only_water(self):
        self.assertEqual(self.parts("water_temperature"), {"materials", "settings"})
        self.assertEqual(self.changed_names("water_temperature"), {"Water"})

    def test_water_density_changes_only_the_water_density(self):
        self.assertEqual(self.parts("water_density"), {"materials"})
        self.assertEqual(self.changed_names("water_density"), {"Water"})
        self.assertEqual(self.water_densities("water_density"), {0.98})
        self.assertEqual(self.water_densities("base"), {1.0})

    def test_auto_density_is_the_steam_table_value_at_the_water_temperature(self):
        self.assertEqual(self.changed_names("water_density_auto"), {"Water"})
        (value,) = self.water_densities("water_density_auto")
        self.assertAlmostEqual(value, water.density_g_cm3(320.0), places=9)
        self.assertAlmostEqual(self.results["water_density_auto"].summary["water_density_gcc"], water.density_g_cm3(320.0), places=9)

    def test_temperature_method_changes_only_settings(self):
        self.assertEqual(self.parts("temperature_method"), {"settings"})

    # ---- fuel -------------------------------------------------------------------------------------------------------
    def test_fresh_fuel_replaces_the_depleted_fuel(self):
        self.assertEqual(self.parts("fuel_fresh"), {"materials", "cells", "surfaces"})
        sig = self.ms.signature(Path(self.results["fuel_fresh"].outdir) / "model.xml")
        self.assertEqual({k[0] for k in sig["materials"] if k[0].startswith("depleted_fuel_m")}, set())
        self.assertTrue(any(k[0] == "Fuel" for k in sig["materials"]))
        self.assertEqual(self.results["fuel_fresh"].summary["fuel"]["depleted_elements"], 0)

    def test_unforced_fuel_leaves_the_two_locations_as_the_library_has_them(self):
        self.assertEqual(self.results["fuel_not_forced"].summary["fuel"]["unplaced_locations"], ["D-03", "G-24"])
        self.assertEqual(self.results["fuel_not_forced"].summary["fuel"]["forced_locations"], [])
        self.assertEqual(self.results["base"].summary["fuel"]["forced_locations"], ["D-03", "G-24"])
        self.assertEqual(self.parts("fuel_not_forced"), {"materials", "cells", "surfaces"})

    # ---- environment ------------------------------------------------------------------------------------------------
    def test_pool_radius_moves_surfaces_only(self):
        self.assertEqual(self.parts("pool_radius"), {"surfaces"})
        surfaces = " ".join(self.diff["pool_radius"]["surfaces"]["names"])
        self.assertIn("120.0", surfaces)

    def test_port_length_factor_moves_surfaces_only(self):
        self.assertEqual(self.parts("port_length_factor"), {"surfaces"})

    def test_port_1_5_length_touches_fewer_surfaces_than_the_other_ports(self):
        self.assertEqual(self.parts("port_1_5_length"), {"surfaces"})
        self.assertLess(self.diff["port_1_5_length"]["surfaces"]["only_in_a"], self.diff["port_length_factor"]["surfaces"]["only_in_a"])

    def test_world_radius_moves_only_the_vacuum_boundary(self):
        self.assertEqual(self.parts("world_radius"), {"surfaces"})
        names = self.diff["world_radius"]["surfaces"]["names"]
        self.assertEqual(len(names), 2, names)
        self.assertTrue(all("z-cylinder" in n and "vacuum" in n for n in names), names)
        self.assertTrue(any("400.0" in n for n in names))

    def test_world_height_moves_only_the_vacuum_boundary(self):
        self.assertEqual(self.parts("world_height"), {"surfaces"})
        names = self.diff["world_height"]["surfaces"]["names"]
        self.assertEqual(len(names), 4, names)
        self.assertTrue(all("z-plane" in n and "vacuum" in n for n in names), names)
        self.assertTrue(any("200.0" in n for n in names) and any("-200.0" in n for n in names))

    def test_removing_barite_and_detectors(self):
        self.assertEqual(self.parts("no_barite_no_detectors"), {"materials", "cells", "surfaces"})
        self.assertEqual(self.changed_names("no_barite_no_detectors"), {"barite_concrete"})
        cells = self.changed_names("no_barite_no_detectors", "cells")
        self.assertTrue({"beam_port_2_detector_1", "outside_pool_region", "beam_port_2_water_fill"} <= cells, cells)

    def test_barite_basis_changes_only_the_barite_material(self):
        self.assertEqual(self.parts("barite_mass_basis"), {"materials"})
        self.assertEqual(self.changed_names("barite_mass_basis"), {"barite_concrete"})

    # ---- run settings, tallies, rods ---------------------------------------------------------------------------------
    def test_run_fields_change_only_settings(self):
        self.assertEqual(self.parts("particles"), {"settings"})
        self.assertEqual(self.parts("seed"), {"settings"})

    def test_dropping_the_library_tallies_changes_only_tallies(self):
        self.assertEqual(self.parts("no_library_tallies"), {"tallies"})
        self.assertEqual(self.diff["no_library_tallies"]["tallies"]["only_in_a"], 8)

    def test_tally_options_without_tallies_change_nothing(self):
        self.assertEqual(self.parts("unused_angle_bins"), set())

    def test_a_rod_moves_surfaces_only(self):
        self.assertEqual(self.parts("rod"), {"surfaces"})

    def test_every_variant_built_and_differs_from_the_base_where_it_should(self):
        self.assertEqual(set(self.diff), set(VARIANTS))
        for label, d in self.diff.items():
            if label != "unused_angle_bins":
                self.assertTrue(d, f"{label} changed nothing in the model")


if __name__ == "__main__":
    unittest.main()
