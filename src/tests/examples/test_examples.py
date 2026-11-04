"""
The working examples (AU-2, devops/AutoMC_Authoring_Plan.md): every example is complete (README with its source, build.py with an
OPTIONS line and no transport, expected.json with its checks, post.py when it has results); and, in the slow tier
(AUTOMC_SLOW_TESTS=1, WSL with OpenMC), every variant is built through the build tool, passes the model check, runs through the job
service when it is a transport example, and gives the results its expected.json names. The harness is tests/examples/harness.py.
"""
import importlib.util
import json
import os
import re
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import automc_paths
import automc_settings
from tests.examples import harness

SLOW = os.environ.get("AUTOMC_SLOW_TESTS") == "1"
HAVE_OPENMC = importlib.util.find_spec("openmc") is not None and os.name == "posix" and shutil.which("openmc") is not None


class Structure(unittest.TestCase):
    def test_there_are_examples(self):
        self.assertGreaterEqual(len(harness.names()), 1)

    def test_every_example_is_complete(self):
        from tools.openmc import authoring
        for name in harness.names():
            with self.subTest(example=name):
                folder = harness.EXAMPLES / name
                readme = (folder / "README.md").read_text(encoding="utf-8")
                self.assertIn("Derived from", readme)
                exp = harness.expected(name)
                self.assertEqual(exp["example"], name)
                self.assertTrue(exp["variants"])
                analysis = harness.is_analysis(name)
                main = "analysis.py" if analysis else "build.py"
                text = (folder / main).read_text(encoding="utf-8")
                self.assertRegex(text, r"(?m)^OPTIONS = \{.*\}$")
                self.assertIn("Derived from", text)
                refused = [why for rx, why in authoring.FORBIDDEN if rx.search(text)]
                self.assertEqual(refused, [], f"{main} must not run transport or other programs")
                for v in exp["variants"]:
                    self.assertTrue({"name", "options", "checks"} <= set(v), v)
                    harness.script_for(name, v["options"])
                    if analysis:
                        self.assertFalse(v.get("transport") or v.get("script"), f"{name}: an analysis has no run")
                        continue
                    if v.get("script"):
                        self.assertTrue((folder / v["script"]).is_file(), f"{name}: {v['script']} is missing")
                    if v["checks"]:
                        self.assertTrue((folder / "post.py").is_file())

    def test_every_data_file_an_example_names_is_listed(self):
        """data.json names only real examples, and each example that reads the data cache names its files there."""
        from tools.openmc import example_data
        files = example_data.registry()
        used = {e for f in files.values() for e in f.get("examples", [])}
        self.assertEqual(used - set(harness.names()), set())
        for name in harness.names():
            main = harness.EXAMPLES / name / ("analysis.py" if harness.is_analysis(name) else "build.py")
            named = set(re.findall(r'"((?:cad|mesh|endf|wmp)/[^"]+)"', main.read_text(encoding="utf-8")))
            with self.subTest(example=name):
                self.assertEqual({rel for rel in named if name not in files.get(rel, {}).get("examples", [])}, set())

    def test_the_index_lists_every_example(self):
        index = (harness.EXAMPLES / "index.md").read_text(encoding="utf-8")
        for name in harness.names():
            self.assertIn(f"`{name}`", index)


@unittest.skipUnless(SLOW and HAVE_OPENMC, "the example runs: AUTOMC_SLOW_TESTS=1 in WSL with OpenMC")
class Runs(unittest.TestCase):
    def setUp(self):
        self.ws = Path(tempfile.mkdtemp(prefix="automc_examples_")).resolve()
        self.patches = [mock.patch.object(automc_paths, "WORKSPACE", self.ws), mock.patch.dict(os.environ)]
        for p in self.patches:
            p.start()
        os.environ[automc_settings.USER_ENV] = str(self.ws / "no-user-settings.yaml")
        if not os.environ.get("OPENMC_CROSS_SECTIONS"):
            from tests.reactors.triga.test_build_config0 import _XS_FALLBACK
            os.environ["OPENMC_CROSS_SECTIONS"] = str(_XS_FALLBACK)
        automc_settings._CACHE.clear()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        automc_settings._CACHE.clear()
        shutil.rmtree(self.ws, ignore_errors=True)


def _make(name, variant):
    def test(self):
        out = harness.run_variant(name, variant, threads=int(os.environ.get("AUTOMC_EXAMPLE_THREADS", "4")))
        errors = [f for f in out["check"]["findings"] if f["severity"] == "error"]
        # a variant that shows a flawed model (a published CAD file whose touching volumes are not merged) names the errors the check
        # must find
        self.assertEqual(sorted({f["check"] for f in errors}), sorted(variant.get("check_errors", [])),
                         f"{name}/{variant['name']}: the model check found {[f['message'] for f in errors]}")
        failed = [f"{c['result']}: {why}" for c, ok, why in out["verdicts"] if not ok]
        self.assertEqual(failed, [], f"{name}/{variant['name']}:\n{out['post_text']}")
    return test


for _name in harness.names():
    for _v in harness.expected(_name)["variants"]:
        setattr(Runs, f"test_{_name.replace('-', '_')}__{_v['name'].replace('-', '_')}", _make(_name, _v))


if __name__ == "__main__":
    unittest.main()
