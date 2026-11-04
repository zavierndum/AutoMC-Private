"""Rod position units: conversion, agreement across units, range errors. No libraries needed."""
import unittest

from tools.reactors.triga import InvalidSpec, resolve, rod_to_fraction, rod_views
from tools.reactors.triga.facilities import netl


class RodUnitsAgree(unittest.TestCase):
    """The same physical position gives the identical fraction whatever unit it is written in."""

    def test_half_travel_in_every_unit(self):
        cases = {
            "transient": [("fraction", 0.5), ("percent", 50.0), ("console", 480.0), ("cm_withdrawn", 19.05), ("z_cm", -73.025 + 19.05)],
            "regulating": [("fraction", 0.5), ("percent", 50.0), ("console", 480.0), ("cm_withdrawn", 19.05), ("z_cm", -76.518 + 19.05)],
            "shim_1": [("fraction", 0.5), ("percent", 50.0), ("console", 480.0), ("cm_withdrawn", 19.05), ("z_cm", -76.518 + 19.05)],
            "shim_2": [("fraction", 0.5), ("percent", 50.0), ("console", 480.0), ("cm_withdrawn", 19.05), ("z_cm", -76.518 + 19.05)],
        }
        for rod, pairs in cases.items():
            for unit, value in pairs:
                with self.subTest(rod=rod, unit=unit):
                    self.assertEqual(rod_to_fraction(rod, value, unit), 0.5)

    def test_endpoints_in_every_unit(self):
        for rod in netl.RODS:
            ins = netl.INSERTED_Z_CM[rod]
            for unit, lo, hi in [("fraction", 0, 1), ("percent", 0, 100), ("console", 0, 960),
                                 ("cm_withdrawn", 0, 38.1), ("z_cm", ins, ins + 38.1)]:
                with self.subTest(rod=rod, unit=unit):
                    self.assertEqual(rod_to_fraction(rod, lo, unit), 0.0)
                    self.assertEqual(rod_to_fraction(rod, hi, unit), 1.0)

    def test_console_is_linear_960(self):
        self.assertEqual(rod_to_fraction("shim_1", 600, "console"), 0.625)
        self.assertEqual(rod_to_fraction("shim_1", 240, "console"), 0.25)

    def test_a_position_in_cm(self):
        self.assertEqual(rod_to_fraction("transient", 9.525, "cm_withdrawn"), 0.25)       # a quarter of the 38.1 cm travel

    def test_views_roundtrip(self):
        for rod in netl.RODS:
            v = rod_views(rod, 0.625)
            self.assertEqual(v["console"], 600.0)
            self.assertEqual(v["percent"], 62.5)
            self.assertAlmostEqual(v["cm_withdrawn"], 23.8125, places=6)
            for unit in ("fraction", "percent", "console", "cm_withdrawn", "z_cm"):
                self.assertAlmostEqual(rod_to_fraction(rod, v[unit], unit), 0.625, places=6)

    def test_equivalent_units_give_the_same_spec_hash(self):
        a = resolve(rods={"TR": {"value": 480, "unit": "console"}})
        b = resolve(rods={"transient": 0.5})
        c = resolve(rods={"transient": {"value": 19.05, "unit": "cm_withdrawn"}})
        self.assertEqual(a.spec_hash(), b.spec_hash())
        self.assertEqual(b.spec_hash(), c.spec_hash())


class RodRanges(unittest.TestCase):
    def assertInvalid(self, rod, value, unit, needle="rods."):
        with self.assertRaises(InvalidSpec) as ctx:
            rod_to_fraction(rod, value, unit)
        self.assertIn(needle, str(ctx.exception))

    def test_out_of_range_is_an_error_never_clamped(self):
        self.assertInvalid("transient", 1.01, "fraction")
        self.assertInvalid("transient", -0.01, "fraction")
        self.assertInvalid("transient", 100.5, "percent")
        self.assertInvalid("transient", 961, "console")
        self.assertInvalid("transient", -1, "console")
        self.assertInvalid("transient", 38.2, "cm_withdrawn")
        self.assertInvalid("transient", -73.1, "z_cm")      # below fully inserted
        self.assertInvalid("transient", -34.9, "z_cm")      # above fully withdrawn
        self.assertInvalid("regulating", -76.6, "z_cm")

    def test_floating_point_slack_at_the_ends_is_accepted(self):
        self.assertEqual(rod_to_fraction("transient", 1 + 1e-12, "fraction"), 1.0)
        self.assertEqual(rod_to_fraction("transient", -1e-12, "fraction"), 0.0)

    def test_bad_values(self):
        for bad in (True, False, None, "0.5", float("nan"), float("inf"), [0.5]):
            with self.subTest(value=bad):
                self.assertInvalid("transient", bad, "fraction")

    def test_unknown_unit(self):
        self.assertInvalid("transient", 0.5, "furlongs")

    def test_error_names_the_rod_field(self):
        with self.assertRaises(InvalidSpec) as ctx:
            resolve(rods={"S1": {"value": 1200, "unit": "console"}})
        self.assertEqual(ctx.exception.field, "rods.S1")


class RodResolution(unittest.TestCase):
    def test_aliases(self):
        s = resolve(rods={"TR": 0.1, "RR": 0.2, "S1": 0.3, "S2": 0.4})
        self.assertEqual(s.rods, {"transient": 0.1, "regulating": 0.2, "shim_1": 0.3, "shim_2": 0.4})

    def test_single_rod_leaves_the_others_at_the_preset_value(self):
        base = resolve().rods
        s = resolve(rods={"S1": 0.2})
        self.assertEqual(s.rods, {**base, "shim_1": 0.2})

    def test_single_number_sets_all_four(self):
        self.assertEqual(set(resolve(rods=0.3).rods.values()), {0.3})

    def test_value_unit_object_sets_all_four(self):
        self.assertEqual(set(resolve(rods={"value": 480, "unit": "console"}).rods.values()), {0.5})

    def test_unknown_rod_and_duplicate_rod(self):
        with self.assertRaises(InvalidSpec):
            resolve(rods={"XX": 0.5})
        with self.assertRaises(InvalidSpec) as ctx:
            resolve(rods={"TR": 0.1, "transient": 0.2})
        self.assertIn("same rod", str(ctx.exception))

    def test_bad_rods_shapes(self):
        for bad in (True, "0.5", [0.5], {"value": 1, "unit": "percent", "extra": 1}):
            with self.subTest(rods=bad):
                with self.assertRaises(InvalidSpec):
                    resolve(rods=bad)

    def test_rod_views_on_the_spec(self):
        v = resolve(rods={"S1": {"value": 600, "unit": "console"}}).rod_views()
        self.assertEqual(v["shim_1"]["fraction"], 0.625)
        self.assertEqual(v["shim_1"]["console"], 600.0)
        self.assertAlmostEqual(v["shim_1"]["z_cm"], -76.518 + 0.625 * 38.1, places=6)


if __name__ == "__main__":
    unittest.main()
