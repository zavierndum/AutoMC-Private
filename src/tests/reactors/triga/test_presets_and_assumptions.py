"""Presets and the assumptions register. No libraries needed."""
import json
import unittest

from tools.reactors.triga import InvalidSpec, active_assumptions, all_assumptions, describe_presets, resolve
from tools.reactors.triga import presets

from tests.reactors.triga._packs import REF, ref_port_lengths, require_restricted  # noqa: E402


def setUpModule():
    require_restricted()


class Presets(unittest.TestCase):
    def test_reference_forms(self):
        self.assertEqual(presets.get("netl_boc505")[0], "netl_boc505@1")
        self.assertEqual(presets.get("netl_boc505@1")[0], "netl_boc505@1")

    def test_get_returns_an_independent_copy(self):
        _, a = presets.get("netl_boc505")
        a["rods"]["transient"] = 0.0
        a["conditions"]["water_temperature_K"] = 1.0
        _, b = presets.get("netl_boc505")
        self.assertEqual(b["rods"]["transient"], REF["rods"]["transient"])
        self.assertEqual(b["conditions"]["water_temperature_K"], 293.6)

    def test_every_preset_resolves_to_a_valid_spec(self):
        for ref in presets.available():
            with self.subTest(preset=ref):
                self.assertEqual(resolve(preset=ref).preset, ref)

    def test_describe(self):
        d = describe_presets()
        self.assertEqual(d[0]["preset"], "netl_boc505@1")
        self.assertTrue(d[0]["description"])

    def test_bad_references(self):
        for ref in ("nope", "netl_boc505@0", "netl_boc505@2", "netl boc505", "a/b"):
            with self.subTest(ref=ref):
                with self.assertRaises(InvalidSpec):
                    presets.get(ref)


class Assumptions(unittest.TestCase):
    def ids(self, **kw):
        return {a["id"] for a in active_assumptions(resolve(**kw))}

    def test_register_is_complete_and_serializable(self):
        ids = [a["id"] for a in all_assumptions()]
        self.assertEqual(ids, [f"A{i}" for i in range(1, 19)])
        json.dumps(all_assumptions())
        for a in all_assumptions():
            self.assertTrue(a["statement"] and a["confirm_with"] and a["status"], a["id"])

    def test_config_zero_default(self):
        self.assertEqual(self.ids(), {"A1", "A2", "A3", "A4", "A5", "A6", "A9", "A10", "A16"})

    def test_inserts_add_their_assumptions(self):
        ids = self.ids(config=8)
        self.assertTrue({"A7", "A8"} <= ids)
        self.assertNotIn("A7", self.ids(config=0))

    def test_barite_off_drops_barite_assumptions(self):
        ids = self.ids(environment={"barite": False, "detector_geometry": False})
        self.assertFalse({"A1", "A3"} & ids)
        self.assertIn("A2", ids)

    def test_fresh_fuel_drops_data_assumptions(self):
        ids = self.ids(fuel={"source": "fresh"})
        self.assertFalse({"A9", "A10"} & ids)

    def test_active_entries_carry_the_values_in_use(self):
        a2 = next(a for a in active_assumptions(resolve(environment={"pool_radius_cm": 120.0})) if a["id"] == "A2")
        self.assertEqual(a2["fields"]["environment.pool_radius_cm"], 120.0)
        a1 = next(a for a in active_assumptions(resolve()) if a["id"] == "A1")
        self.assertEqual(a1["fields"], {"environment.barite_composition_basis": "atom"})
        self.assertIn("likely wrong", a1["status"])
        json.dumps(active_assumptions(resolve(config=8)))


if __name__ == "__main__":
    unittest.main()
