"""
One version for AutoMC (WD-1, devops/AutoMC_Workspace_Plan.md section 7): the package's names the code, the prompt, the skills and the
cases; a fingerprint computed from their files says exactly which ones a chat or an evaluation saw, and changes with any of them; the
TRIGA builder records the same version in every case.
"""
import contextlib
import io
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import automc
import automc_paths
import automc_version


class Version(unittest.TestCase):
    def test_one_version_everywhere(self):
        from tools.reactors import triga
        self.assertRegex(automc_version.VERSION, r"^\d+\.\d+\.\d+$")
        self.assertEqual(triga.__version__, automc_version.VERSION)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(automc.main(["--version"]), 0)
        self.assertRegex(out.getvalue().strip(), rf"^AutoMC {automc_version.VERSION} \(prompt, skills and cases [0-9a-f]{{12}}\)$")
        changelog = (automc_paths.AUTOMC_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        self.assertIn(f"## {automc_version.VERSION} ", changelog)
        for rel in ("prompts/system_prompt_automc.yml", "evals/cases.yml"):
            self.assertNotRegex((automc_paths.SRC / rel).read_text(encoding="utf-8"), r"(?m)^version:", rel)

    def test_the_fingerprint_changes_with_the_prompt_a_skill_or_the_cases(self):
        tmp = Path(tempfile.mkdtemp(prefix="automc_fp_"))
        try:
            for rel in ("prompts", "skills", "evals"):
                shutil.copytree(automc_paths.SRC / rel, tmp / rel)
            with mock.patch.object(automc_paths, "SRC", tmp):
                base, base_cases = automc_version.fingerprint(), automc_version.fingerprint(include_cases=True)
                self.assertRegex(base, r"^[0-9a-f]{12}$")
                self.assertEqual(automc_version.fingerprint(), base)                         # stable
                (tmp / "evals" / "cases.yml").write_text("cases: []\n", encoding="utf-8")
                self.assertEqual(automc_version.fingerprint(), base)                         # a chat's ignores the cases
                self.assertNotEqual(automc_version.fingerprint(include_cases=True), base_cases)
                skill = tmp / "skills" / "general-tools" / "SKILL.md"
                skill.write_text(skill.read_text(encoding="utf-8") + "\n- one more rule\n", encoding="utf-8")
                after_skill = automc_version.fingerprint()
                self.assertNotEqual(after_skill, base)
                yml = tmp / "prompts" / "system_prompt_automc.yml"
                yml.write_text(yml.read_text(encoding="utf-8").replace("You are AutoMC", "You are AutoMC,"), encoding="utf-8")
                self.assertNotEqual(automc_version.fingerprint(), after_skill)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_the_prompt_names_the_version_and_its_fingerprint(self):
        try:
            import yaml  # noqa: F401
        except ImportError:
            self.skipTest("needs PyYAML")
        from agent import prompt
        from agent.llm import Provider
        p = Provider(name="test", kind="local", backend="ollama", base_url="x", model="m")
        text, info = prompt.system_prompt(p)
        self.assertTrue(text.startswith(f"# AutoMC {automc_version.VERSION}\n"))
        self.assertEqual((info["version"], info["fingerprint"]), (automc_version.VERSION, automc_version.fingerprint()))
        self.assertIn("- Model: local-ollama/m (local:", text)                                # the label, never a raw model id


if __name__ == "__main__":
    unittest.main()
