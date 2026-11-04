"""Spec resolution, validation, hashing, JSON. No libraries needed."""
import json
import os
import re
import tempfile
import unittest

from tools.reactors.triga import InvalidSpec, Spec, resolve
from tools.reactors.triga.facilities import netl
from tools.reactors.triga.spec import SPEC_VERSION
from tools.reactors.triga import presets

from tests.reactors.triga._packs import REF, ref_port_lengths, require_restricted  # noqa: E402


def setUpModule():
    require_restricted()


class Defaults(unittest.TestCase):
    def test_default_is_preset_one(self):
        s = resolve()
        self.assertEqual(s.preset, "netl_boc505@1")
        self.assertEqual((s.config, s.pnt, s.three_el), (0, "none", "none"))
        self.assertEqual(s.rods, REF["rods"])
        self.assertEqual(s.conditions.water_density_gcc, 1.0)
        self.assertEqual(s.environment.pool_radius_cm, REF["environment"]["pool_radius_cm"])
        self.assertEqual(s.environment.barite_composition_basis, "atom")     # as the reference script writes it
        self.assertEqual(s.run.effective(), {"particles": 20000, "batches": 40, "inactive": 10, "seed": None, "threads": None})

    def test_pinning_the_preset_version(self):
        self.assertEqual(resolve(preset="netl_boc505@1"), resolve())

    def test_beam_port_lengths(self):
        length, length_15 = ref_port_lengths()
        self.assertEqual(resolve().environment.beam_port_lengths_cm(), (length, length_15))
        s = resolve(environment={"beam_port_1_5_length_cm": None})
        self.assertEqual(s.environment.beam_port_lengths_cm(), (length, length))
        s = resolve(environment={"pool_radius_cm": 100.0, "beam_port_length_factor": 2.0, "beam_port_1_5_length_cm": 500.0})
        self.assertEqual(s.environment.beam_port_lengths_cm(), (200.0, 500.0))

    def test_default_name(self):
        self.assertRegex(resolve().name, r"^triga-netl-c0-[0-9a-f]{8}$")
        self.assertRegex(resolve(pnt="epithermal", three_el="fast").name, r"^triga-netl-cx-[0-9a-f]{8}$")

    def test_explicit_name_is_kept_and_validated(self):
        self.assertEqual(resolve(name="my-case_1.0").name, "my-case_1.0")
        self.assertEqual(resolve(name="study-8/c8").name, "study-8/c8")                       # a case in a subfolder (WD-2)
        for bad in ("../x", "a/../b", "a/b/c/d/e", "", ".x", "x" * 65):
            with self.subTest(name=bad):
                with self.assertRaises(InvalidSpec):
                    resolve(name=bad)


class Configurations(unittest.TestCase):
    def test_config_sets_both_inserts(self):
        s = resolve(config=8)
        self.assertEqual((s.pnt, s.three_el), ("thermal", "fast"))
        for n, (p, t, _) in presets.configurations("netl", "netl_boc505@1")[1].items():
            with self.subTest(config=n):
                r = resolve(config=n)
                self.assertEqual((r.config, r.pnt, r.three_el), (n, p, t))

    def test_inserts_find_their_config(self):
        self.assertEqual(resolve(pnt="thermal").config, 1)
        self.assertEqual(resolve(pnt="t", three_el="f").config, 8)
        self.assertEqual(resolve(**{"3L": "fast"}).config, 5)
        self.assertEqual(resolve(three_el="bare").config, 0)

    def test_combination_outside_the_ten_has_no_config(self):
        s = resolve(pnt="epithermal", three_el="fast")
        self.assertIsNone(s.config)

    def test_conflicts_are_errors(self):
        with self.assertRaises(InvalidSpec) as ctx:
            resolve(config=3, pnt="thermal")
        self.assertEqual(ctx.exception.field, "pnt")
        with self.assertRaises(InvalidSpec) as ctx:
            resolve(config=8, three_el="thermal")
        self.assertEqual(ctx.exception.field, "three_el")

    def test_agreeing_redundant_values_are_fine(self):
        self.assertEqual(resolve(config=8, pnt="t", three_el="fast").config, 8)

    def test_bad_values(self):
        for kw in ({"config": 10}, {"config": -1}, {"config": "x"}, {"config": 1.5}, {"pnt": "fast"}, {"three_el": "bogus"}, {"pnt": 3}):
            with self.subTest(kw=kw):
                with self.assertRaises(InvalidSpec):
                    resolve(**kw)

    def test_both_spellings_of_3L_is_an_error(self):
        with self.assertRaises(InvalidSpec):
            resolve(**{"3L": "fast", "three_el": "fast"})


class HashAndJson(unittest.TestCase):
    def test_hash_is_stable_and_ignores_labels(self):
        a = resolve(name="one")
        b = resolve(name="two")
        self.assertEqual(a.spec_hash(), b.spec_hash())
        self.assertEqual(len(a.spec_hash()), 64)

    def test_hash_changes_with_physics(self):
        base = resolve().spec_hash()
        self.assertNotEqual(base, resolve(conditions={"water_temperature_K": 320}).spec_hash())
        self.assertNotEqual(base, resolve(rods={"TR": 0.3}).spec_hash())
        self.assertNotEqual(base, resolve(config=1).spec_hash())

    def test_json_round_trip(self):
        s = resolve(config=8, rods={"S1": {"value": 600, "unit": "console"}}, run={"size": "standard", "seed": 7})
        again = Spec.from_json(s.to_json())
        self.assertEqual(again, s)
        self.assertEqual(again.to_dict(), s.to_dict())
        self.assertEqual(again.spec_hash(), s.spec_hash())

    def test_json_has_only_plain_types(self):
        json.dumps(resolve().to_dict())          # would raise on anything not serializable

    def test_from_dict_is_strict(self):
        d = resolve().to_dict()
        for mutate, field in [(lambda x: x.pop("run"), "run"), (lambda x: x.update(bogus=1), "bogus"),
                              (lambda x: x["run"].update(bogus=1), "run.bogus"), (lambda x: x["fuel"].pop("source"), "fuel.source"),
                              (lambda x: x.update(spec_version=SPEC_VERSION + 1), "spec_version")]:
            with self.subTest(field=field):
                bad = json.loads(json.dumps(d))
                mutate(bad)
                with self.assertRaises(InvalidSpec) as ctx:
                    Spec.from_dict(bad)
                self.assertIn(field, str(ctx.exception))

    def test_not_json(self):
        with self.assertRaises(InvalidSpec):
            Spec.from_json("{nope")


class Layers(unittest.TestCase):
    def test_sections_merge_field_by_field(self):
        s = resolve(conditions={"water_temperature_K": 320.0}, run={"seed": 5})
        self.assertEqual(s.conditions.water_temperature_K, 320.0)
        self.assertEqual(s.conditions.fuel_temperature_K, 293.6)          # untouched
        self.assertEqual(s.run.seed, 5)

    def test_spec_file_then_keywords(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "s.json")
            with open(path, "w") as fh:
                json.dump({"config": 8, "rods": 0.4, "run": {"size": "standard"}}, fh)
            s = resolve(spec=path, rods={"TR": 0.9})
            self.assertEqual((s.config, s.run.size), (8, "standard"))
            self.assertEqual(s.rods["transient"], 0.9)                    # keyword wins over the file
            self.assertEqual(s.rods["shim_1"], 0.4)

    def test_spec_dict_with_its_own_preset(self):
        d = resolve(config=2).to_dict()
        self.assertEqual(resolve(spec=d), resolve(config=2, name=d["name"]))

    def test_bad_spec_sources(self):
        with self.assertRaises(InvalidSpec):
            resolve(spec="/no/such/file.json")
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "bad.json")
            with open(path, "w") as fh:
                fh.write("{broken")
            with self.assertRaises(InvalidSpec):
                resolve(spec=path)
        with self.assertRaises(InvalidSpec):
            resolve(spec=[1, 2])

    def test_unknown_fields_and_presets(self):
        for kw in ({"foo": 1}, {"conditions": {"bogus": 1}}, {"conditions": 5}):
            with self.subTest(kw=kw):
                with self.assertRaises(InvalidSpec):
                    resolve(**kw)
        for ref in ("nope", "netl_boc505@9", "bad ref!", ""):
            with self.subTest(preset=ref):
                if ref == "":
                    resolve(preset=ref)       # empty means "use the default"
                else:
                    with self.assertRaises(InvalidSpec):
                        resolve(preset=ref)

    def test_presets_are_not_mutated_by_resolve(self):
        resolve(rods=0.1, conditions={"water_temperature_K": 999})
        self.assertEqual(resolve().rods, REF["rods"])
        self.assertEqual(resolve().conditions.water_temperature_K, 293.6)


class Validation(unittest.TestCase):
    def bad(self, field, **kw):
        with self.assertRaises(InvalidSpec) as ctx:
            resolve(**kw)
        self.assertEqual(ctx.exception.field, field, str(ctx.exception))

    def test_conditions(self):
        self.bad("conditions.water_density_gcc", conditions={"water_density_gcc": 0})
        self.bad("conditions.water_density_gcc", conditions={"water_density_gcc": 5})
        self.bad("conditions.fuel_temperature_K", conditions={"fuel_temperature_K": -5})
        self.bad("conditions.water_temperature_K", conditions={"water_temperature_K": "hot"})
        self.bad("conditions.temperature_method", conditions={"temperature_method": "cubic"})

    def test_environment(self):
        self.bad("environment.world_radius_cm", environment={"world_radius_cm": 100.0})            # not larger than the pool
        self.bad("environment.world_height_cm", environment={"world_height_cm": 150.0})            # not above pool height
        self.bad("environment.barite_composition_basis", environment={"barite_composition_basis": "volume"})
        self.bad("environment.barite", environment={"barite": "yes"})
        self.bad("environment.pool_radius_cm", environment={"pool_radius_cm": 0})

    def test_run(self):
        self.bad("run.size", run={"size": "huge"})
        self.bad("run.inactive", run={"batches": 10, "inactive": 10})
        self.bad("run.seed", run={"seed": 0})
        self.bad("run.threads", run={"threads": 0})
        self.bad("run.particles", run={"particles": 1.5})
        self.bad("run.inactive", run={"size": "smoke", "inactive": 40})     # smoke has 40 batches

    def test_run_sizes(self):
        self.assertEqual(resolve(run={"size": "standard"}).run.effective()["particles"], 500_000)
        eff = resolve(run={"size": "production"}).run.effective()
        self.assertEqual((eff["particles"], eff["batches"], eff["inactive"]), (5_000_000, 150, 20))
        eff = resolve(run={"size": "smoke", "particles": 1000, "inactive": 0}).run.effective()
        self.assertEqual((eff["particles"], eff["batches"], eff["inactive"]), (1000, 40, 0))

    def test_tallies(self):
        self.assertEqual(resolve(tallies=["beam_port_2_detector_flux", "flux_mesh"]).tallies, ["beam_port_2_detector_flux", "flux_mesh"])
        self.bad("tallies[0]", tallies=["nope"])
        self.bad("tallies", tallies=["beam_port_2_detector_flux", "beam_port_2_detector_flux"])
        self.bad("tallies", tallies="beam_port_2_detector_flux")
        self.bad("tallies_options.polar_bins", tallies_options={"polar_bins": 0})

    def test_facility_and_version(self):
        self.bad("facility", facility="mars")
        self.bad("spec_version", spec_version=2)

    def test_numbers_are_normalized(self):
        s = resolve(conditions={"water_temperature_K": 300})          # an int becomes a float
        self.assertIsInstance(s.conditions.water_temperature_K, float)
        self.assertIsInstance(resolve(run={"particles": 1000.0}).run.particles, int)


class TemperatureMethod(unittest.TestCase):
    def method(self, **cond):
        return resolve(conditions=cond).conditions.resolved_temperature_method()

    def test_auto_picks_nearest_close_to_library_temperatures(self):
        self.assertEqual(self.method(), "nearest")                      # 293.6 is 0.4 K from 294
        self.assertEqual(self.method(water_temperature_K=293.15), "nearest")
        self.assertEqual(self.method(fuel_temperature_K=600.0), "nearest")

    def test_auto_picks_interpolation_far_from_them(self):
        self.assertEqual(self.method(water_temperature_K=320.0), "interpolation")
        self.assertEqual(self.method(fuel_temperature_K=450.0), "interpolation")

    def test_explicit_method_is_respected(self):
        self.assertEqual(self.method(temperature_method="nearest", water_temperature_K=320.0), "nearest")
        self.assertEqual(self.method(temperature_method="interpolation"), "interpolation")


if __name__ == "__main__":
    unittest.main()
