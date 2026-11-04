"""
The ten configurations built for real (Block 3, B3-2). WSL automc environment only; about 15 builds, four at a time, then point queries.

What is checked:
- every configuration and five hardware variants build, and the summary says what sits in each slot;
- configuration 0 is byte-identical to a model built before Block 3 (when that reference build is in the workspace);
- against configuration 0, only insert materials change: no fuel, rod, beam-port or detector cell changes, no settings or tally changes;
- point queries in the slots find the right material: water, air, aluminum, cadmium, lead, boron, as the hardware fields say;
- two fresh builds of the same configuration are identical.
No transport is run.
"""
import json
import math
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from tools.reactors.triga import build_input

from tests.reactors.triga.test_build_config0 import SKIP, setUpModule  # noqa: F401  (same environment guard and cross-section fallback)

from automc_paths import SRC, WORKSPACE  # noqa: E402
TINY = {"run": {"particles": 1000, "batches": 3, "inactive": 1, "seed": 7}}

SLEEVE_OUTER_CM = 2.06375          # 3-EL inner sleeve outer radius = liner inner radius (library default)
CASING_MID_CM = 0.5 * (2.23393 + 2.38125)
PNT_TUBE_OUTER_CM = 0.5 * 0.875 * 2.54
PNT_WRAPPER_CD_OUTER_CM = 0.5 * 0.955 * 2.54

BUILDS = {f"c{n}": {"config": n} for n in range(10)}
BUILDS.update({
    "epnt-wrapper": {"config": 2, "inserts": {"pnt": {"cadmium_wrapper": True}}},
    "f3l-simplified": {"config": 5, "inserts": {"three_el": {"modified_geometry": "simplified"}}},
    "empty-canister": {"config": 0, "inserts": {"three_el_empty_state": "empty_canister"}},
    "t3l-thin": {"config": 3, "inserts": {"three_el": {"liner_thickness_cm": 0.05}}},
    "3el-no-liner": {"config": 4, "inserts": {"three_el": {"liner": "none"}}},
})

ALLOWED_NEW = {"Aluminum", "Air", "Water", "Cadmium", "Lead", "Natural_B4C", "Enriched_Boron", "Silver"}


def load_signature_module():
    from tools.openmc import model_signature
    return model_signature


def merged(base, extra):
    out = {k: (dict(v) if isinstance(v, dict) else v) for k, v in base.items()}
    for k, v in extra.items():
        out[k] = {**out.get(k, {}), **v} if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


@unittest.skipIf(SKIP, SKIP or "")
class TenConfigurations(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import openmc
        cls.openmc = openmc
        cls.ms = load_signature_module()
        cls.ws = Path(tempfile.mkdtemp(prefix="triga_configs_"))

        def one(item):
            name, fields = item
            return name, build_input("openmc", "netl", name=name, workspace=cls.ws, **merged(TINY, fields))

        with ThreadPoolExecutor(max_workers=4) as pool:
            cls.r = dict(pool.map(one, BUILDS.items()))
        cls.sig = {k: cls.ms.signature(Path(v.outdir) / "model.xml") for k, v in cls.r.items()}
        cls.geom = {}
        cls._find_slot_centres()

    # ---- helpers ----------------------------------------------------------------------------------------------------
    @classmethod
    def geometry(cls, name):
        if name not in cls.geom:
            cls.geom[name] = cls.openmc.Model.from_model_xml(str(Path(cls.r[name].outdir) / "model.xml")).geometry
        return cls.geom[name]

    @classmethod
    def material_at(cls, name, pt):
        try:
            found = cls.geometry(name).find(pt)
        except Exception:
            return None
        cell = next((o for o in reversed(found) if isinstance(o, cls.openmc.Cell)), None)
        if cell is None:
            return None
        fill = cell.fill
        return getattr(fill, "name", None) if isinstance(fill, cls.openmc.Material) else ("void" if fill is None else "fill")

    @classmethod
    def _find_slot_centres(cls):
        """
        Find each slot's axis at the core midplane: first the centre of all grid points where the model differs from configuration 0
        (the 3-EL from configuration 4, the PNT from configuration 1), then refined by bisecting the outer edge of the aluminum casing or
        tube in +x, -x, +y and -y. A grid alone is not enough: the cadmium liner is 0.1 cm thick and a 0.5 cm grid misses it unevenly.
        """
        pts = [(x * 0.5, y * 0.5, 0.0) for x in range(-60, 61) for y in range(-60, 61) if (x * x + y * y) * 0.25 <= 30.0 ** 2]
        cls.el_xy = cls._refine("c4", cls._centre_of_difference("c4", pts), 2.2, 2.9)
        cls.pnt_xy = cls._refine("c1", cls._centre_of_difference("c1", pts), 0.95, 1.6)

    @classmethod
    def _centre_of_difference(cls, build, pts):
        diff = [p for p in pts if cls.material_at(build, p) != cls.material_at("c0", p)]
        assert diff, f"{build} does not differ from configuration 0 at the core midplane"
        return sum(p[0] for p in diff) / len(diff), sum(p[1] for p in diff) / len(diff)

    @classmethod
    def _refine(cls, build, centre, r_in, r_out, rounds=3):
        """Move `centre` until the aluminum outer edge is equally far in +x/-x and +y/-y (bisection to 1e-6 cm)."""
        cx, cy = centre

        def edge(dx, dy):
            lo, hi = r_in, r_out                     # aluminum at lo, not aluminum at hi
            for _ in range(40):
                mid = 0.5 * (lo + hi)
                if cls.material_at(build, (cx + dx * mid, cy + dy * mid, 0.0)) == "Aluminum":
                    lo = mid
                else:
                    hi = mid
            return lo

        for _ in range(rounds):
            cx += 0.5 * (edge(1, 0) - edge(-1, 0))
            cy += 0.5 * (edge(0, 1) - edge(0, -1))
        return cx, cy

    def test_the_slot_centres_are_found_precisely(self):
        for build, centre, radius in (("c4", self.el_xy, 2.38125), ("c1", self.pnt_xy, PNT_TUBE_OUTER_CM)):
            with self.subTest(build=build):
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    inside = (centre[0] + dx * (radius - 0.001), centre[1] + dy * (radius - 0.001), 0.0)
                    outside = (centre[0] + dx * (radius + 0.001), centre[1] + dy * (radius + 0.001), 0.0)
                    self.assertEqual(self.material_at(build, inside), "Aluminum")
                    self.assertNotEqual(self.material_at(build, outside), "Aluminum")

    def ring(self, name, centre, radius, z=0.0, n=8):
        return {self.material_at(name, (centre[0] + radius * math.cos(2 * math.pi * i / n),
                                        centre[1] + radius * math.sin(2 * math.pi * i / n), z)) for i in range(n)}

    # ---- the builds -------------------------------------------------------------------------------------------------
    def test_every_build_says_what_is_in_each_slot(self):
        slots = {n: self.r[f"c{n}"].summary["slots"] for n in range(10)}
        for n in range(10):
            with self.subTest(config=n):
                pnt, el = {0: (0, 0), 1: (1, 0), 2: (1, 0), 3: (0, 1), 4: (0, 1), 5: (0, 2), 6: (1, 1), 7: (1, 1), 8: (1, 2), 9: (1, 1)}[n]
                self.assertEqual(slots[n]["G-34"], "PNT" if pnt else "NoneType")
                self.assertEqual(slots[n]["E-11"], {0: "NoneType", 1: "ThreeElementIrradiator", 2: "ModifiedThreeElementIrradiator"}[el])
                self.assertEqual(self.r[f"c{n}"].summary["inserts"]["hardware"], self.r[f"c{n}"].spec["inserts"])
        self.assertEqual(self.r["empty-canister"].summary["slots"]["E-11"], "ThreeElementIrradiator")

    def test_configuration_0_is_unchanged_by_block_3_except_the_reflector_fix(self):
        """
        workspace/c0-critical-bank was built before Block 3 and before the reflector fix (2026-10-23). The same spec built now must differ in
        exactly one line: the reflector cell, whose region gains the exclusion of the core shroud.
        """
        import difflib
        ref = WORKSPACE / "c0-critical-bank"
        if not (ref / "model.xml").is_file() or not (ref / "result.json").is_file():
            self.skipTest("reference build workspace/c0-critical-bank (made before B3-2) is not present")
        spec = json.loads((ref / "result.json").read_text())["spec"]
        r = build_input("openmc", "netl", spec=spec, name="c0-regression", workspace=self.ws)
        old = (ref / "model.xml").read_text().splitlines()
        new = (Path(r.outdir) / "model.xml").read_text().splitlines()
        changed = [l for l in difflib.ndiff(old, new) if l.startswith(("- ", "+ "))]
        self.assertEqual(len(changed), 2, changed[:6])
        self.assertTrue(all('name="reflector"' in l for l in changed), changed)
        import re
        region = lambda line: re.search(r'region="([^"]*)"', line).group(1)
        removed = region(next(l for l in changed if l.startswith("- ")))
        added = region(next(l for l in changed if l.startswith("+ ")))
        # OpenMC writes the complement of the shroud as the union of its outside half-spaces, the same clause the pool cell uses
        pool = region(next(l for l in new if 'name="pool"' in l))
        outside_shroud = re.search(r"\((-?\d+(?: \| -?\d+){11})\)", pool).group(0)
        self.assertEqual(added, f"{removed} {outside_shroud}")

    def test_a_second_fresh_build_is_identical(self):
        again = build_input("openmc", "netl", name="c8-again", workspace=self.ws, **merged(TINY, {"config": 8}))
        self.assertEqual((Path(again.outdir) / "model.xml").read_bytes(), (Path(self.r["c8"].outdir) / "model.xml").read_bytes())

    # ---- only the slots change ----------------------------------------------------------------------------------------
    def test_only_insert_materials_change(self):
        for name in BUILDS:
            if name == "c0":
                continue
            with self.subTest(build=name):
                d = self.ms.compare(self.sig["c0"], self.sig[name])
                self.assertNotIn("settings", d)
                self.assertNotIn("tallies", d)
                self.assertTrue(set(d) <= {"materials", "cells", "surfaces"}, d)
                if "materials" in d:
                    self.assertTrue(set(d["materials"]["names"]) <= ALLOWED_NEW, d["materials"]["names"])
                if "cells" in d:
                    touched = [c for c in d["cells"]["names"] if any(k in c for k in ("beam_port", "detector", "outside_pool", "fuel", "rod"))]
                    self.assertEqual(touched, [], touched)

    def test_the_insert_materials_are_there(self):
        def names(build):
            return {k[0] for k in self.sig[build]["materials"]}
        self.assertNotIn("Lead", names("c0"))
        self.assertIn("Cadmium", names("c4"))
        self.assertIn("Lead", names("c3"))
        self.assertTrue({"Natural_B4C", "Enriched_Boron", "Silver"} <= names("c5"))
        self.assertNotIn("Natural_B4C", names("c4"))

    # ---- point queries: 3-EL ------------------------------------------------------------------------------------------
    def test_3el_slots_are_water_without_a_3el(self):
        for n in (0, 1, 2):
            self.assertEqual(self.material_at(f"c{n}", (*self.el_xy, 0.0)), "Water", n)
            self.assertEqual(self.ring(f"c{n}", self.el_xy, SLEEVE_OUTER_CM + 0.05), {"Water"}, n)

    def test_conventional_3el_liner_material_follows_the_hardware(self):
        r_liner = SLEEVE_OUTER_CM + 0.0508
        for build, liner in (("c3", "Lead"), ("c4", "Cadmium"), ("c6", "Lead"), ("c7", "Cadmium"), ("c9", "Lead"),
                             ("3el-no-liner", "Air"), ("empty-canister", "Air")):
            with self.subTest(build=build):
                self.assertEqual(self.ring(build, self.el_xy, r_liner), {liner})
                self.assertEqual(self.material_at(build, (*self.el_xy, 0.0)), "Air")
                self.assertEqual(self.ring(build, self.el_xy, CASING_MID_CM), {"Aluminum"})

    def test_liner_thickness_is_honoured(self):
        self.assertEqual(self.ring("t3l-thin", self.el_xy, SLEEVE_OUTER_CM + 0.025), {"Lead"})
        self.assertNotIn("Lead", self.ring("t3l-thin", self.el_xy, SLEEVE_OUTER_CM + 0.08))
        self.assertEqual(self.ring("c3", self.el_xy, SLEEVE_OUTER_CM + 0.08), {"Lead"})

    def test_modified_3el_has_its_boron_filters(self):
        for build in ("c5", "c8", "f3l-simplified"):
            with self.subTest(build=build):
                seen = set()
                for zi in range(-70, 71):
                    for r in (1.2, 1.5, 1.9, 2.0):
                        seen.add(self.material_at(build, (self.el_xy[0] + r, self.el_xy[1], float(zi))))
                self.assertTrue({"Natural_B4C", "Enriched_Boron"} & seen, seen)
                self.assertNotIn("Lead", seen)
        self.assertLess(self.r["f3l-simplified"].summary["cells"], self.r["c5"].summary["cells"])

    # ---- point queries: PNT -------------------------------------------------------------------------------------------
    def test_pnt_slot(self):
        self.assertEqual(self.material_at("c0", (*self.pnt_xy, 0.0)), "Water")
        self.assertEqual(self.material_at("c3", (*self.pnt_xy, 0.0)), "Water")
        for build in ("c1", "c2", "c6", "c8", "c9"):
            with self.subTest(build=build):
                self.assertEqual(self.material_at(build, (*self.pnt_xy, 0.0)), "Air")

    def test_cadmium_terminus_only_where_the_hardware_says(self):
        def cadmium_on_axis(build):
            return any(self.material_at(build, (*self.pnt_xy, -60.0 + 0.01 * i)) == "Cadmium" for i in range(6000))
        self.assertTrue(cadmium_on_axis("c2"))
        self.assertTrue(cadmium_on_axis("c9"))
        self.assertFalse(cadmium_on_axis("c1"))
        self.assertFalse(cadmium_on_axis("c8"))

    def test_cadmium_wrapper_only_when_asked(self):
        r = 0.5 * (PNT_TUBE_OUTER_CM + PNT_WRAPPER_CD_OUTER_CM)
        self.assertEqual(self.ring("epnt-wrapper", self.pnt_xy, r), {"Cadmium"})
        self.assertNotIn("Cadmium", self.ring("c2", self.pnt_xy, r))


if __name__ == "__main__":
    unittest.main()
