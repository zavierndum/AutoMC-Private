"""
Insert hardware in the spec layer (Block 3, B3-1). Standard library only: runs on Windows Python too. Nothing is built here.
"""
import json
import subprocess
import sys
import unittest
from pathlib import Path

from tools.reactors.triga import InvalidSpec, UnsupportedFeature, active_assumptions, advice, check_supported, presets
from tools.reactors.triga.facilities import netl
from tools.reactors.triga.inserts import describe
from tools.reactors.triga.spec import Spec, resolve

from automc_paths import SRC  # noqa: E402
from tests.reactors.triga._packs import require_restricted  # noqa: E402


def setUpModule():
    require_restricted()

PNT_T = {"terminus": "unlined", "cadmium_wrapper": False}
PNT_E = {"terminus": "cadmium_lined", "cadmium_wrapper": False}
EL_T = {"design": "conventional", "liner": "lead", "liner_thickness_cm": 0.1016, "modified_geometry": None}
EL_E = {"design": "conventional", "liner": "cadmium", "liner_thickness_cm": 0.1016, "modified_geometry": None}
EL_F = {"design": "modified", "liner": None, "liner_thickness_cm": None, "modified_geometry": "detailed"}


def cli(*args):
    p = subprocess.run([sys.executable, "-m", "automc", "triga", *args], cwd=SRC, capture_output=True, text=True, timeout=120)
    return p.returncode, p.stdout, p.stderr


class LabelsResolveToHardware(unittest.TestCase):
    EXPECTED = {
        0: (None, None, "water"), 1: (PNT_T, None, "water"), 2: (PNT_E, None, "water"), 3: (None, EL_T, None),
        4: (None, EL_E, None), 5: (None, EL_F, None), 6: (PNT_T, EL_T, None), 7: (PNT_T, EL_E, None), 8: (PNT_T, EL_F, None),
        9: (PNT_E, EL_T, None),
    }

    def test_every_configuration(self):
        for n, (pnt, three_el, empty) in self.EXPECTED.items():
            with self.subTest(config=n):
                ins = resolve(config=n).inserts
                self.assertEqual(ins, {"pnt": pnt, "three_el": three_el, "three_el_empty_state": empty})

    def test_labels_and_config_give_the_same_spec(self):
        self.assertEqual(resolve(config=8).spec_hash(), resolve(pnt="t", three_el="f").spec_hash())
        self.assertEqual(resolve(config=4).spec_hash(), resolve(**{"3L": "epithermal"}).spec_hash())

    def test_each_configuration_hashes_differently(self):
        self.assertEqual(len({resolve(config=n).spec_hash() for n in range(10)}), 10)

    def test_round_trip(self):
        for n in range(10):
            s = resolve(config=n)
            self.assertEqual(Spec.from_json(s.to_json()).to_dict(), s.to_dict())

    def test_describe(self):
        self.assertEqual(describe(resolve(config=0).inserts, "none", "none"), {"G-34": "water (no PNT)", "E-11/F-13/F-14": "water (no 3-EL)"})
        d = describe(resolve(config=9).inserts, "epithermal", "thermal")
        self.assertEqual(d["G-34"], "PNT, cadmium-lined terminus")
        self.assertEqual(d["E-11/F-13/F-14"], "conventional 3-EL, lead liner 0.1016 cm")
        self.assertEqual(describe(resolve(config=5).inserts, "none", "fast")["E-11/F-13/F-14"], "modified 3-EL (detailed geometry)")


class Overrides(unittest.TestCase):
    def test_one_field_at_a_time(self):
        s = resolve(config=2, inserts={"pnt": {"cadmium_wrapper": True}})
        self.assertEqual(s.inserts["pnt"], {"terminus": "cadmium_lined", "cadmium_wrapper": True})
        s = resolve(config=5, inserts={"three_el": {"modified_geometry": "simplified"}})
        self.assertEqual(s.inserts["three_el"]["modified_geometry"], "simplified")
        s = resolve(config=3, inserts={"three_el": {"liner_thickness_cm": 0.05}})
        self.assertEqual(s.inserts["three_el"], {**EL_T, "liner_thickness_cm": 0.05})

    def test_overrides_change_the_hash(self):
        self.assertNotEqual(resolve(config=3, inserts={"three_el": {"liner": "cadmium"}}).spec_hash(), resolve(config=3).spec_hash())

    def test_the_thickness_follows_the_liner(self):
        self.assertIsNone(resolve(config=4, inserts={"three_el": {"liner": "none"}}).inserts["three_el"]["liner_thickness_cm"])
        s = resolve(config=5, inserts={"three_el": {"design": "conventional", "liner": "lead"}})
        self.assertEqual(s.inserts["three_el"]["liner_thickness_cm"], 0.1016)

    def test_keyword_labels_replace_the_file_s_labels(self):
        saved = resolve(config=8).to_dict()
        self.assertEqual(resolve(spec=saved, pnt="epithermal").inserts["pnt"], PNT_E)
        self.assertEqual(resolve(spec=saved, pnt="epithermal").inserts["three_el"], EL_F)    # the file's 3-EL stays
        self.assertIsNone(resolve(spec=saved, pnt="epithermal").config)                      # e + f is not one of the ten
        with self.assertRaises(InvalidSpec):
            resolve(spec=saved, config=3, pnt="thermal")                                    # inside one layer they must agree

    def test_switching_design_drops_the_other_design_s_fields(self):
        s = resolve(config=5, inserts={"three_el": {"design": "conventional", "liner": "none"}})
        self.assertEqual(s.inserts["three_el"], {"design": "conventional", "liner": "none", "liner_thickness_cm": None, "modified_geometry": None})
        s = resolve(config=4, inserts={"three_el": {"design": "modified", "modified_geometry": "simplified"}})
        self.assertEqual(s.inserts["three_el"], {"design": "modified", "liner": None, "liner_thickness_cm": None, "modified_geometry": "simplified"})

    def test_empty_state(self):
        self.assertEqual(resolve(inserts={"three_el_empty_state": "empty_canister"}).inserts["three_el_empty_state"], "empty_canister")
        with self.assertRaises(InvalidSpec) as ctx:
            resolve(config=4, inserts={"three_el_empty_state": "water"})
        self.assertEqual(ctx.exception.field, "inserts.three_el_empty_state")

    def test_bad_values_name_the_field(self):
        cases = [
            ({"config": 1, "inserts": {"pnt": {"terminus": "gold"}}}, "inserts.pnt.terminus"),
            ({"config": 1, "inserts": {"pnt": {"cadmium_wrapper": "yes"}}}, "inserts.pnt.cadmium_wrapper"),
            ({"config": 1, "inserts": {"pnt": {"colour": "red"}}}, "inserts.pnt.colour"),
            ({"config": 4, "inserts": {"three_el": {"liner": "boron"}}}, "inserts.three_el.liner"),
            ({"config": 4, "inserts": {"three_el": {"liner_thickness_cm": 0.0}}}, "inserts.three_el.liner_thickness_cm"),
            ({"config": 4, "inserts": {"three_el": {"liner_thickness_cm": 0.2}}}, "inserts.three_el.liner_thickness_cm"),
            ({"config": 4, "inserts": {"three_el": {"liner_thickness_cm": float("nan")}}}, "inserts.three_el.liner_thickness_cm"),
            ({"config": 4, "inserts": {"three_el": {"modified_geometry": "detailed"}}}, "inserts.three_el.modified_geometry"),
            ({"config": 4, "inserts": {"three_el": {"liner": "none", "liner_thickness_cm": 0.1}}}, "inserts.three_el.liner_thickness_cm"),
            ({"config": 5, "inserts": {"three_el": {"liner": "cadmium"}}}, "inserts.three_el.liner"),
            ({"config": 5, "inserts": {"three_el": {"modified_geometry": "rough"}}}, "inserts.three_el.modified_geometry"),
            ({"config": 5, "inserts": {"three_el": {"design": "hybrid"}}}, "inserts.three_el.design"),
            ({"config": 0, "inserts": {"pnt": {"terminus": "unlined"}}}, "inserts.pnt"),
            ({"config": 0, "inserts": {"three_el": {"liner": "lead"}}}, "inserts.three_el"),
            ({"config": 0, "inserts": {"three_el_empty_state": "air"}}, "inserts.three_el_empty_state"),
            ({"inserts": {"unknown": 1}}, "inserts.unknown"),
            ({"inserts": "lead"}, "inserts"),
        ]
        for kw, field in cases:
            with self.subTest(kw=kw):
                with self.assertRaises(InvalidSpec) as ctx:
                    resolve(**kw)
                self.assertEqual(ctx.exception.field, field)

    def test_the_liner_limit_comes_from_the_library_geometry(self):
        self.assertAlmostEqual(netl.THREE_EL_MAX_LINER_THICKNESS_CM, 2.23393 - 2.06375, places=6)
        resolve(config=4, inserts={"three_el": {"liner_thickness_cm": 0.17}})


class SpecFilesAndLabelChanges(unittest.TestCase):
    """Hardware stored in a spec file belongs to that file's labels."""

    def test_a_saved_spec_rebuilds_the_same_hardware(self):
        saved = resolve(config=3, inserts={"three_el": {"liner_thickness_cm": 0.05}}).to_dict()
        again = resolve(spec=saved)
        self.assertEqual(again.inserts, saved["inserts"])
        self.assertEqual(again.spec_hash(), resolve(spec=saved).spec_hash())

    def test_changing_the_label_drops_the_saved_hardware(self):
        saved = resolve(config=8).to_dict()
        self.assertEqual(resolve(spec=saved, config=0).inserts, {"pnt": None, "three_el": None, "three_el_empty_state": "water"})
        self.assertEqual(resolve(spec=saved, config=2).inserts["pnt"], PNT_E)
        zero = resolve(config=0).to_dict()
        self.assertEqual(resolve(spec=zero, config=4).inserts["three_el"], EL_E)

    def test_a_spec_file_without_labels_follows_the_keyword_labels(self):
        self.assertEqual(resolve(spec={"inserts": {"three_el": {"liner": "none"}}}, config=4).inserts["three_el"]["liner"], "none")

    def test_keyword_overrides_win_over_the_file(self):
        saved = resolve(config=5).to_dict()
        s = resolve(spec=saved, inserts={"three_el": {"modified_geometry": "simplified"}})
        self.assertEqual(s.inserts["three_el"]["modified_geometry"], "simplified")

    def test_a_complete_dict_is_checked_on_its_own(self):
        d = resolve(config=4).to_dict()
        d["inserts"]["three_el"]["liner"] = "boron"
        with self.assertRaises(InvalidSpec) as ctx:
            Spec.from_dict(d)
        self.assertEqual(ctx.exception.field, "inserts.three_el.liner")
        d = resolve(config=0).to_dict()
        d["inserts"]["pnt"] = PNT_T
        with self.assertRaises(InvalidSpec):
            Spec.from_dict(d)

    def test_table_changes_do_not_touch_unrelated_hashes(self):
        key = ("netl_boc505", 1)
        table = presets.pack_for("netl_boc505@1").presets[key][1]["insert_labels"]["three_el"]["fast"]
        before = {n: resolve(config=n).spec_hash() for n in (0, 4, 5)}
        table["modified_geometry"] = "simplified"
        try:
            after = {n: resolve(config=n).spec_hash() for n in (0, 4, 5)}
        finally:
            table["modified_geometry"] = "detailed"
        self.assertEqual(before[0], after[0])
        self.assertEqual(before[4], after[4])
        self.assertNotEqual(before[5], after[5])


class AssumptionsWarningsAndCapabilities(unittest.TestCase):
    def ids(self, **kw):
        return {a["id"] for a in active_assumptions(resolve(**kw))}

    def test_each_label_brings_its_assumption(self):
        self.assertTrue({"A16"} <= self.ids(config=0) and not {"A7", "A12", "A13", "A14", "A15"} & self.ids(config=0))
        self.assertTrue({"A7"} <= self.ids(config=1))
        self.assertTrue({"A13", "A14"} <= self.ids(config=3) and "A16" not in self.ids(config=3))
        self.assertTrue({"A12", "A14"} <= self.ids(config=4))
        self.assertTrue({"A15"} <= self.ids(config=5) and "A14" not in self.ids(config=5))
        statuses = {a["id"]: a["status"] for a in active_assumptions(resolve(config=4))}
        self.assertTrue(statuses["A12"].startswith("confirmed"))

    def test_warnings_for_unconfirmed_and_custom_hardware(self):
        w = advice.for_spec(resolve(config=4), cpu_count=16)
        self.assertFalse(any("3-EL" in m for m in w), w)                        # e3L is confirmed
        w = advice.for_spec(resolve(config=8), cpu_count=16)
        self.assertTrue(any("thermal PNT" in m and "A7" in m for m in w), w)
        self.assertTrue(any("fast 3-EL" in m and "A15" in m for m in w), w)
        w = advice.for_spec(resolve(config=4, inserts={"three_el": {"liner": "lead"}}), cpu_count=16)
        self.assertTrue(any("different from the preset's" in m for m in w), w)
        self.assertEqual(advice.for_spec(resolve(), cpu_count=16), [])

    def test_every_configuration_is_buildable_since_b3_2(self):
        for n in range(10):
            check_supported(resolve(config=n))
        check_supported(resolve(inserts={"three_el_empty_state": "empty_canister"}))

    def test_combinations_outside_the_ten_are_flagged(self):
        s = resolve(pnt="epithermal", three_el="fast")
        self.assertIsNone(s.config)
        self.assertTrue(any("not one of the 10 numbered configurations" in m for m in advice.for_spec(s, cpu_count=16)))
        self.assertFalse(any("numbered configurations" in m for m in advice.for_spec(resolve(config=9), cpu_count=16)))


class CommandLine(unittest.TestCase):
    def test_spec_shows_the_hardware(self):
        code, out, err = cli("spec", "--config", "8")
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["inserts"]["three_el"], EL_F)

    def test_set_one_hardware_field(self):
        code, out, err = cli("spec", "--config", "5", "--set", "inserts.three_el.modified_geometry=simplified")
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["inserts"]["three_el"]["modified_geometry"], "simplified")

    def test_errors_name_the_field(self):
        code, out, _ = cli("spec", "--config", "0", "--set", "inserts.pnt.terminus=unlined", "--json")
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(out)["error"]["field"], "inserts.pnt")


if __name__ == "__main__":
    unittest.main()
