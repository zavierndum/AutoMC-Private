"""
`read-run --compare`: k by batch and every tally, bin by bin. Real, tiny runs of the test sphere with the openmc executable: the same
model and seed agree, another seed differs in both, a run without the tally does not match, and a statepoint whose tally sums were
changed by one part in a million is caught although its k is identical. Needs the WSL automc environment; skips elsewhere.
"""
import contextlib
import io
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests.openmc.test_runs import SKIP, _XS_FALLBACK, write_sphere
from tools.openmc import results


@unittest.skipIf(SKIP, SKIP or "")
class Compare(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="automc_results_"))
        env = dict(os.environ)
        env.setdefault("OPENMC_CROSS_SECTIONS", str(_XS_FALLBACK))
        for name, kw in (("a", {}), ("b", {}), ("seed2", {"seed": 2}), ("no-tally", {"tally": False})):
            write_sphere(cls.tmp / name, **kw)
            subprocess.run(["openmc", "-s", "2"], cwd=cls.tmp / name, env=env, capture_output=True, check=True, timeout=300)
        shutil.copytree(cls.tmp / "a", cls.tmp / "changed")
        import h5py
        with h5py.File(results.latest_statepoint(cls.tmp / "changed"), "r+") as f:
            for key in f["tallies"]:
                if key.startswith("tally "):
                    f["tallies"][key]["results"][...] *= 1.000001

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def compare(self, a, b):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = results.compare(self.tmp / a, self.tmp / b)
        return rc, out.getvalue()

    def test_the_same_model_and_seed_agree_in_k_and_tallies(self):
        rc, text = self.compare("a", "b")
        self.assertEqual(rc, 0, text)
        self.assertIn("1 in both", text)
        self.assertRegex(text, r"verdict +(bit-for-bit identical|reproducible)")

    def test_another_seed_differs_in_both(self):
        rc, text = self.compare("a", "seed2")
        self.assertEqual(rc, 1)
        self.assertIn("DIFFERENT: the k values and the tallies", text)

    def test_a_missing_tally_does_not_match(self):
        rc, text = self.compare("a", "no-tally")
        self.assertEqual(rc, 1)
        self.assertIn("DO NOT MATCH: only in A ['flux_mesh']", text)

    def test_changed_tallies_are_caught_when_k_is_identical(self):
        rc, text = self.compare("a", "changed")
        self.assertEqual(rc, 1)
        self.assertIn("DIFFERENT: the tallies,", text)
        t = results.tally_differences(self.tmp / "a", self.tmp / "changed")
        self.assertGreater(t["mean"], 1e-7)

    def test_the_command_line(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = results.main(["--compare", str(self.tmp / "a"), str(self.tmp / "b")])
        self.assertEqual(rc, 0, out.getvalue())


if __name__ == "__main__":
    unittest.main()
