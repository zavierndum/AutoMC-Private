"""Name and path rules, and what Block 1 can build. No libraries needed."""
import os
import tempfile
import unittest
from pathlib import Path

from tools.reactors.triga import InvalidSpec, UnsupportedFeature, check_supported, resolve
from tools.reactors.triga.safety import confined_outdir, validate_name
from tests.reactors.triga._packs import require_restricted  # noqa: E402


def setUpModule():
    require_restricted()


class Names(unittest.TestCase):
    def test_good_names(self):
        for ok in ("a", "case1", "triga-netl-c0-1a2b3c4d", "My_case.v2", "9lives", "x" * 64, "study-8/c8", "a/b/c/d"):
            with self.subTest(name=ok):
                self.assertEqual(validate_name(ok), ok)

    def test_bad_names(self):
        bad = ["", ".hidden", "-lead", "_lead", "a/b/c/d/e", "a//b", "/a", "a/", "a/../b", ".automc/x", "a/.automc", "a\\b", "..",
               "../x", "a b", "naiveé", "x" * 65, "name.", "C:x", None, 5, ["a"]]
        for name in bad:
            with self.subTest(name=name):
                with self.assertRaises(InvalidSpec):
                    validate_name(name)


class Outdir(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self._tmp.name) / "workspace"
        self.ws.mkdir()
        self.outside = Path(self._tmp.name) / "outside"
        self.outside.mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def test_default_is_workspace_slash_name(self):
        self.assertEqual(confined_outdir("case1", workspace=self.ws), (self.ws / "case1").resolve())

    def test_explicit_inside_is_accepted(self):
        target = self.ws / "deeper" / "case"
        self.assertEqual(confined_outdir("case1", target, workspace=self.ws), target.resolve())

    def test_outside_is_refused_unless_a_trusted_caller_allows_it(self):
        with self.assertRaises(InvalidSpec):
            confined_outdir("case1", self.outside, workspace=self.ws)
        self.assertEqual(confined_outdir("case1", self.outside, workspace=self.ws, allow_outside=True), self.outside.resolve())

    def test_dot_dot_traversal_is_refused(self):
        with self.assertRaises(InvalidSpec):
            confined_outdir("case1", self.ws / ".." / "outside", workspace=self.ws)

    def test_workspace_itself_is_refused(self):
        with self.assertRaises(InvalidSpec):
            confined_outdir("case1", self.ws, workspace=self.ws)

    def test_symlink_pointing_outside_is_refused(self):
        link = self.ws / "link"
        try:
            os.symlink(self.outside, link, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks not available on this filesystem")
        with self.assertRaises(InvalidSpec):
            confined_outdir("case1", link / "case", workspace=self.ws)

    def test_bad_name_is_refused_before_any_path_work(self):
        with self.assertRaises(InvalidSpec):
            confined_outdir("../evil", workspace=self.ws)


class Block1Capabilities(unittest.TestCase):
    def test_default_config_zero_is_supported(self):
        check_supported(resolve())

    def test_every_configuration_is_supported_since_block_3(self):
        for kw in [{"config": n} for n in range(10)] + [{"pnt": "epithermal", "three_el": "fast"},
                                                         {"inserts": {"three_el_empty_state": "empty_canister"}}]:
            with self.subTest(kw=kw):
                check_supported(resolve(**kw))

    def test_tally_presets_are_supported_since_block_2(self):
        check_supported(resolve(tallies=["beam_port_2_detector_flux", "flux_mesh"]))

    def test_barite_and_detectors_go_together(self):
        for env in ({"barite": True, "detector_geometry": False}, {"barite": False, "detector_geometry": True}):
            with self.subTest(env=env):
                with self.assertRaises(UnsupportedFeature):
                    check_supported(resolve(environment=env))
        check_supported(resolve(environment={"barite": False, "detector_geometry": False}))

    def test_codes(self):
        with self.assertRaises(UnsupportedFeature) as ctx:
            check_supported(resolve(), code="mcnp")
        self.assertIn("Block 4", str(ctx.exception))
        with self.assertRaises(InvalidSpec):
            check_supported(resolve(), code="serpent")


if __name__ == "__main__":
    unittest.main()
