"""
Facility packs: the format, the labelling guards, the condition language of the assumptions, the public pack on its own, and the
facility data every result records. Runs with or without the restricted pack; part of it builds a model in the WSL automc environment.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import automc_paths
from automc_paths import SRC  # noqa: E402
from tools.reactors.triga import MissingLibrary, InvalidSpec, build_input, facility_data, packs, presets, resolve

from tests.reactors.triga._packs import PUBLIC_PRESET, canaries, restricted  # noqa: E402
from tests.reactors.triga.test_build_config0 import _XS_FALLBACK  # noqa: E402

PUBLIC_PACK_DIR = automc_paths.PUBLIC_PACKS_DIR / "netl_public"

MINIMAL_SPEC = {"spec_version": 1, "facility": "netl"}


def write_pack(root, name="demo", distribution="public", items=None, files=None, extra=None, sources=None):
    """A small pack folder for the format tests: pack.yaml plus the given files."""
    folder = Path(root) / name
    folder.mkdir(parents=True)
    for rel, text in (files or {}).items():
        (folder / rel).parent.mkdir(parents=True, exist_ok=True)
        (folder / rel).write_text(text, encoding="utf-8")
    meta = {"format": 1, "pack": name, "facility": "netl", "title": "demo pack", "distribution": distribution,
            "sources": sources or {"own": {"what": "made up for a test", "distribution": "public"}},
            "items": items if items is not None else {rel: {"what": rel, "sources": ["own"], "distribution": "public"} for rel in files or {}}}
    meta.update(extra or {})
    (folder / "pack.yaml").write_text(json.dumps(meta), encoding="utf-8")        # JSON is valid YAML
    return folder


class Format(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="automc_packs_"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_the_shipped_public_pack_loads_and_is_public_throughout(self):
        p = packs.load_pack(PUBLIC_PACK_DIR, "public")
        self.assertEqual((p.id, p.facility, p.distribution), ("netl_public", "netl", "public"))
        self.assertTrue(all(it["distribution"] == "public" for it in p.items.values()))
        self.assertIn((PUBLIC_PRESET, 1), p.presets)
        self.assertEqual(p.default_preset, PUBLIC_PRESET)

    def test_a_file_without_a_label_stops_the_pack(self):
        folder = write_pack(self.tmp, files={"data.yaml": "a: 1\n"}, items={})
        with self.assertRaises(MissingLibrary) as ctx:
            packs.load_pack(folder, "public")
        self.assertIn("not listed in pack.yaml items", str(ctx.exception))

    def test_a_listed_item_must_exist(self):
        folder = write_pack(self.tmp, items={"gone.yaml": {"what": "x", "sources": ["own"], "distribution": "public"}})
        with self.assertRaises(MissingLibrary) as ctx:
            packs.load_pack(folder, "public")
        self.assertIn("does not exist", str(ctx.exception))

    def test_a_public_item_cannot_use_a_restricted_source(self):
        folder = write_pack(self.tmp, files={"x.yaml": "a: 1\n"},
                            sources={"own": {"what": "secret", "distribution": "restricted"}},
                            items={"x.yaml": {"what": "x", "sources": ["own"], "distribution": "public"}})
        with self.assertRaises(MissingLibrary) as ctx:
            packs.load_pack(folder, "public")
        self.assertIn("uses a restricted source", str(ctx.exception))

    def test_a_pack_with_restricted_items_must_say_so(self):
        folder = write_pack(self.tmp, files={"x.yaml": "a: 1\n"},
                            items={"x.yaml": {"what": "x", "sources": ["own"], "distribution": "restricted"}})
        with self.assertRaises(MissingLibrary) as ctx:
            packs.load_pack(folder, "restricted")
        self.assertIn("holds restricted items", str(ctx.exception))

    def test_restricted_packs_are_refused_where_they_could_be_published(self):
        folder = write_pack(self.tmp, distribution="restricted")
        with self.assertRaises(MissingLibrary) as ctx:                  # shipped with AutoMC
            packs.load_pack(folder, "public")
        self.assertIn("must be public", str(ctx.exception))
        with mock.patch.object(automc_paths, "AUTOMC_ROOT", self.tmp):    # anywhere inside the AutoMC folder
            with self.assertRaises(MissingLibrary) as ctx:
                packs.load_pack(folder, "restricted")
        self.assertIn("must not sit inside the AutoMC folder", str(ctx.exception))

    def test_unknown_keys_and_formats_are_refused(self):
        folder = write_pack(self.tmp / "a", extra={"colour": "blue"})
        with self.assertRaises(MissingLibrary):
            packs.load_pack(folder, "public")
        folder = write_pack(self.tmp / "b", extra={"format": 2})
        with self.assertRaises(MissingLibrary) as ctx:
            packs.load_pack(folder, "public")
        self.assertIn("format 2", str(ctx.exception))

    def test_presets_based_on_another_merge_its_changes(self):
        presets_yaml = json.dumps({
            "base@1": {"description": "a base", "spec": {**MINIMAL_SPEC, "rods": {"transient": 0.1}, "tallies": []}},
            "more@1": {"description": "with a tally", "based_on": "base@1", "changes": {"tallies": ["flux_mesh"]}},
        })
        folder = write_pack(self.tmp, files={"presets.yaml": presets_yaml})
        p = packs.load_pack(folder, "public")
        self.assertEqual(p.presets[("more", 1)][1], {**MINIMAL_SPEC, "rods": {"transient": 0.1}, "tallies": ["flux_mesh"]})
        self.assertEqual(p.presets[("base", 1)][1]["tallies"], [])        # the base is not changed

    def test_two_packs_cannot_define_the_same_preset(self):
        presets_yaml = json.dumps({"same@1": {"description": "d", "spec": dict(MINIMAL_SPEC)}})
        a = packs.load_pack(write_pack(self.tmp / "a", name="pa", files={"presets.yaml": presets_yaml}), "public")
        b = packs.load_pack(write_pack(self.tmp / "b", name="pb", files={"presets.yaml": presets_yaml}), "public")
        with self.assertRaises(MissingLibrary) as ctx:
            packs.Registry([a, b])
        self.assertIn("is also in pack", str(ctx.exception))

    def test_a_restricted_default_wins_over_a_public_one(self):
        presets_yaml = lambda n: json.dumps({f"{n}@1": {"description": "d", "spec": dict(MINIMAL_SPEC)}})   # noqa: E731
        pub = packs.load_pack(write_pack(self.tmp / "a", name="pub", files={"presets.yaml": presets_yaml("p")},
                                         extra={"default_preset": "p"}), "public")
        res = packs.load_pack(write_pack(self.tmp / "b", name="res", files={"presets.yaml": presets_yaml("r")},
                                         extra={"default_preset": "r"}), "restricted")
        self.assertEqual(packs.Registry([res, pub]).default_preset("netl"), "r")
        self.assertEqual(packs.Registry([pub]).default_preset("netl"), "p")


class Conditions(unittest.TestCase):
    spec = {"pnt": "none", "flag": True, "count": 1, "tallies": [], "inserts": {"three_el": None}, "fuel": {"source": "fresh"}}

    def holds(self, raw):
        return packs.compile_condition(raw, "test")(self.spec)

    def test_the_forms(self):
        self.assertTrue(self.holds("always"))
        self.assertTrue(self.holds({"pnt": "none", "fuel.source": "fresh"}))
        self.assertFalse(self.holds({"pnt": {"not": "none"}}))
        self.assertTrue(self.holds({"fuel.source": {"in": ["fresh", "x"]}}))
        self.assertFalse(self.holds({"tallies": {"not_empty": True}}))
        self.assertTrue(self.holds({"any": [{"pnt": "thermal"}, {"flag": True}]}))

    def test_true_matches_only_true(self):
        self.assertTrue(self.holds({"flag": True}))
        self.assertFalse(self.holds({"count": True}))              # 1 is not true

    def test_a_path_through_null_is_null_and_a_typo_is_an_error(self):
        self.assertFalse(self.holds({"inserts.three_el.liner": {"in": ["cadmium"]}}))
        with self.assertRaises(MissingLibrary):
            self.holds({"fuel.sorce": "fresh"})

    def test_malformed_conditions_are_refused(self):
        for raw in ("sometimes", {}, {"pnt": {"maybe": 1}}, {"tallies": {"not_empty": False}}, {"any": []}):
            with self.subTest(raw=raw):
                with self.assertRaises(MissingLibrary):
                    packs.compile_condition(raw, "test")


class PublicPack(unittest.TestCase):
    """The public preset, whether or not the restricted pack is installed."""

    def test_it_resolves_from_public_sources_only(self):
        s = resolve(preset=PUBLIC_PRESET)
        self.assertEqual((s.fuel.source, s.environment.barite, s.environment.detector_geometry), ("fresh", False, False))
        self.assertIsNone(s.environment.world_radius_cm)
        fd = facility_data(s)
        self.assertEqual(fd["distribution"], "public")
        self.assertEqual({p["pack"] for p in fd["packs"]}, {"netl_public"})

    def test_its_register_covers_what_applies_and_names_the_pack(self):
        ids = {a["id"] for a in presets.active_assumptions(resolve(preset=PUBLIC_PRESET))}
        self.assertTrue({"A2", "A6", "A16", "A17", "A18"} <= ids, ids)
        for a in presets.active_assumptions(resolve(preset=PUBLIC_PRESET, pnt="thermal", three_el="fast")):
            self.assertEqual(a["pack"], "netl_public")

    def test_world_fields_are_required_with_barite_and_optional_without(self):
        with self.assertRaises(InvalidSpec) as ctx:
            resolve(preset=PUBLIC_PRESET, environment={"barite": True, "detector_geometry": True})
        self.assertEqual(ctx.exception.field, "environment.world_radius_cm")

    def test_insert_labels_never_pull_in_another_pack_s_numbering(self):
        from tools.reactors.triga import advice
        s = resolve(preset=PUBLIC_PRESET, pnt="thermal", three_el="fast")
        self.assertIsNone(s.config)
        self.assertEqual(facility_data(s)["distribution"], "public")
        self.assertFalse(any("numbered configurations" in w for w in advice.for_spec(s, cpu_count=16)))

    @unittest.skipUnless(restricted() is not None, "needs the restricted NETL facility pack")
    def test_restricted_data_used_by_a_public_preset_is_labelled(self):
        s = resolve(preset=PUBLIC_PRESET, config=8)                    # the configuration table is restricted
        self.assertEqual((s.pnt, s.three_el), ("thermal", "fast"))
        fd = facility_data(s)
        self.assertEqual(fd["distribution"], "restricted")
        used = {(p["pack"], it["item"]) for p in fd["packs"] for it in p["items"]}
        self.assertIn(("netl_restricted", "configurations.yaml"), used)

    @unittest.skipUnless(restricted() is not None, "needs the restricted NETL facility pack")
    def test_the_reference_preset_is_labelled_restricted(self):
        fd = facility_data(resolve())
        self.assertEqual(fd["distribution"], "restricted")
        items = {it["item"]: it for p in fd["packs"] for it in p["items"]}
        for item in ("presets.yaml", "configurations.yaml", "data/depleted_fuel_boc505.py", "data/barite.yaml"):
            self.assertIn(item, items)
            self.assertEqual(len(items[item]["sha256"]), 64)
        self.assertNotIn(str(automc_paths.AUTOMC_ROOT), json.dumps(fd))       # no local paths


def cli_without_restricted_packs(*args):
    empty = tempfile.mkdtemp(prefix="automc_no_packs_")
    try:
        env = dict(os.environ, AUTOMC_FACILITY_PACKS=empty)
        p = subprocess.run([sys.executable, "-m", "automc", "triga", *args], cwd=SRC, env=env, capture_output=True, text=True, timeout=120)
        return p.returncode, p.stdout, p.stderr
    finally:
        shutil.rmtree(empty, ignore_errors=True)


class PublicOnlyCommandLine(unittest.TestCase):
    """AutoMC as a public user gets it: no restricted pack anywhere."""

    def test_only_public_presets_are_listed_and_the_public_one_is_the_default(self):
        code, out, err = cli_without_restricted_packs("presets")
        self.assertEqual(code, 0, err)
        self.assertEqual({p["distribution"] for p in json.loads(out)}, {"public"})
        code, out, err = cli_without_restricted_packs("spec")
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["preset"], f"{PUBLIC_PRESET}@1")

    def test_restricted_presets_and_tables_are_explained_not_crashed(self):
        code, _, err = cli_without_restricted_packs("spec", "--preset", "netl_boc505")
        self.assertEqual(code, 2)
        self.assertIn("No restricted facility pack is installed", err)
        code, _, err = cli_without_restricted_packs("spec", "--config", "8")
        self.assertEqual(code, 2)
        self.assertIn("no installed facility pack numbers the configurations", err)
        code, out, err = cli_without_restricted_packs("spec", "--pnt", "thermal", "--3L", "fast")
        self.assertEqual(code, 0, err)
        self.assertIsNone(json.loads(out)["config"])


def _why_no_build():
    if os.name != "posix":
        return "needs Linux or WSL"
    try:
        import openmc  # noqa: F401
    except ImportError:
        return "OpenMC is not importable in this interpreter"
    if not os.environ.get("OPENMC_CROSS_SECTIONS") and not _XS_FALLBACK.is_file():
        return "no cross-section library"
    return None


@unittest.skipIf(_why_no_build(), _why_no_build() or "")
class PublicBuild(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not os.environ.get("OPENMC_CROSS_SECTIONS"):
            os.environ["OPENMC_CROSS_SECTIONS"] = str(_XS_FALLBACK)
        cls.ws = Path(tempfile.mkdtemp(prefix="automc_public_"))
        cls.r = build_input("openmc", "netl", preset=PUBLIC_PRESET, workspace=cls.ws, run={"particles": 1000, "batches": 3, "inactive": 1})

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.ws, ignore_errors=True)

    def test_it_builds_from_the_public_profile_with_fresh_fuel(self):
        self.assertEqual(self.r.profile["name"], "pnt_3el")
        self.assertEqual(self.r.facility_data["distribution"], "public")
        f = self.r.summary["fuel"]
        self.assertEqual((f["mapped_locations"], f["depleted_elements"]), (0, 0))
        self.assertGreater(f["elements"], 80)
        self.assertEqual(self.r.summary["detector_cells"], [])
        self.assertEqual({k: p["length_cm"] for k, p in self.r.summary["beam_ports"].items()}, {k: 180.0 for k in "1234"})

    def test_the_result_records_the_pack(self):
        saved = json.loads((Path(self.r.outdir) / "result.json").read_text())
        self.assertEqual(saved["facility_data"], self.r.facility_data)
        self.assertEqual([p["pack"] for p in saved["facility_data"]["packs"]], ["netl_public"])


class PublicFiles(unittest.TestCase):
    """No public file of AutoMC holds a restricted value: what the repository and the image carry is searched for the restricted pack's
    canaries (2026-11-03, before the first commit, when an evaluation case and a test were found holding the reference model's bias).
    A failure names the file and the canary's place in the list, never the value."""
    TOP_LEVEL = ("README.md", "CHANGELOG.md", "DESIGN.md", "SETUP.md", "TOOLS.md", "Dockerfile", "docker-compose.yml",
                 "docker-compose.netl.yml", ".dockerignore", ".gitignore", ".gitattributes")     # the top-level files .gitignore lets in

    @unittest.skipUnless(restricted() is not None, "needs the restricted NETL facility pack (its canaries)")
    def test_no_public_file_holds_a_canary(self):
        values = canaries()
        self.assertTrue(values, "the restricted pack provides no canaries")
        root = automc_paths.AUTOMC_ROOT
        files = [f for d in ("src", "library_snapshots", "docker") for f in (root / d).rglob("*")
                 if f.is_file() and "__pycache__" not in f.parts and f.suffix != ".pyc"]
        files += [root / name for name in self.TOP_LEVEL if (root / name).is_file()]
        found = []
        for f in files:
            text = f.read_text(encoding="utf-8", errors="ignore")
            found += [f"{f.relative_to(root).as_posix()}: canary {i}" for i, v in enumerate(values) if v in text]
        self.assertGreater(len(files), 100)
        self.assertEqual(found, [])
