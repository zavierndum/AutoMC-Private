"""
The skill files hold together: each skill's SKILL.md has its front matter, each rule file says what it was derived from, every
working example and rule file a skill names exists, every rule file is listed in its skill's table, and every OpenMC name a rule
writes in backticks (`openmc.Material.add_nuclide`) exists in the installed OpenMC, so that a rule cannot send the model to a class
or function that is not there. The skills name no internal planning document or label, which a reader of the published files cannot
open. The authoring template builds and passes the model check through the build tool.
"""
import importlib
import importlib.util
import inspect
import re
import unittest
from pathlib import Path

import automc_paths

SKILLS = automc_paths.SRC / "skills"
EXAMPLES = SKILLS / "working_examples"
HAVE_OPENMC = importlib.util.find_spec("openmc") is not None
_OPENMC_NAME = re.compile(r"`(openmc(?:\.[A-Za-z_]\w*)+)")
_EXAMPLE = re.compile(r"working_examples/([a-z0-9][a-z0-9-]*)")
_RULE = re.compile(r"\brules/([a-z0-9-]+\.md)")
# internal planning documents and their labels: devops/ is never published, so a published file must not send its reader there
_PLAN_LABEL = re.compile(r"devops/|ERKB_LESSONS|\b(?:decisions?|Block \d+) [A-Z]{1,2}\d+\b|\b(?:AU|WD|GT|PR|MS|SF)-\d\b")


def skill_texts():
    """{path: text} of every skill file that is not part of a working example."""
    return {p: p.read_text(encoding="utf-8") for p in sorted(SKILLS.rglob("*.md")) if EXAMPLES not in p.parents}


def rule_files():
    return sorted(SKILLS.glob("*/rules/*.md"))


def resolve(name):
    """Whether a dotted name starting with openmc exists: modules imported, attributes read without calling properties."""
    parts = name.split(".")
    obj = importlib.import_module(parts[0])
    for i, part in enumerate(parts[1:], 1):
        try:
            obj = inspect.getattr_static(obj, part)
        except AttributeError:
            if not inspect.ismodule(obj):
                return False
            try:
                obj = importlib.import_module(".".join(parts[:i + 1]))
            except ImportError:
                return False
        if isinstance(obj, (property, staticmethod, classmethod)):
            return True                                          # a property or a method: nothing further to look up
    return True


class SkillFiles(unittest.TestCase):
    def test_every_skill_has_its_front_matter(self):
        for p in sorted(SKILLS.rglob("SKILL.md")):
            with self.subTest(skill=p.relative_to(SKILLS).as_posix()):
                head = p.read_text(encoding="utf-8").split("---")
                self.assertEqual(head[0], "", "SKILL.md starts with ---")
                self.assertRegex(head[1], r"(?m)^name: \S")
                self.assertRegex(head[1], r"(?m)^description: \S")

    def test_every_rule_file_has_a_title_and_says_what_it_was_derived_from(self):
        self.assertGreaterEqual(len(rule_files()), 18)
        for p in rule_files():
            with self.subTest(rule=p.relative_to(SKILLS).as_posix()):
                lines = p.read_text(encoding="utf-8").splitlines()
                self.assertTrue(lines[0].startswith("# "), "the first line is the title")
                self.assertTrue(any(l.startswith("Derived from:") for l in lines[:4]), "a Derived from line under the title")

    def test_every_example_and_rule_file_named_exists(self):
        for p, text in skill_texts().items():
            for name in set(_EXAMPLE.findall(text)) - {"index"}:
                with self.subTest(file=p.relative_to(SKILLS).as_posix(), example=name):
                    self.assertTrue((EXAMPLES / name).is_dir() or (EXAMPLES / f"{name}.md").is_file())
            skill = p.relative_to(SKILLS).parts[0]
            for rule in set(_RULE.findall(text)):
                with self.subTest(file=p.relative_to(SKILLS).as_posix(), rule=rule):
                    self.assertTrue((SKILLS / skill / "rules" / rule).is_file())

    def test_the_skills_and_the_prompt_name_no_planning_document_or_label(self):
        """What the model reads and the repository publishes stands on its own: no devops/ path, no decision or phase label
        (2026-11-04: two rule files said "decision A13")."""
        for p in sorted(SKILLS.rglob("*")):
            if p.is_file() and p.suffix in {".md", ".yaml", ".yml", ".py", ".json"} and "__pycache__" not in p.parts:
                found = _PLAN_LABEL.findall(p.read_text(encoding="utf-8", errors="replace"))
                with self.subTest(file=p.relative_to(SKILLS).as_posix()):
                    self.assertEqual(found, [])
        for bad in ("see devops/AutoMC_Plan.md", "decision A13", "Block 5 decision D7", "built in AU-2"):
            self.assertTrue(_PLAN_LABEL.search(bad), bad)
        for fine in ("the C5G7 benchmark", "a P0 correction", "U-235 and Pu-239", "ICRP-116", "SHEM-361"):
            self.assertFalse(_PLAN_LABEL.search(fine), fine)
        try:
            from agent import prompt
            from agent.llm import Provider
        except ImportError:                                      # the prompt's renderer needs the agent's dependencies
            return
        for kind, account in (("local", "self_hosted"), ("cloud", "personal")):
            text, _ = prompt.system_prompt(Provider(name="t", kind=kind, account=account, backend="openai_compatible", base_url="x",
                                                    model="m"))
            with self.subTest(prompt=kind):
                self.assertEqual(_PLAN_LABEL.findall(text), [])

    def test_every_rule_file_is_listed_in_its_skill(self):
        for folder in sorted({p.parent.parent for p in rule_files()}):
            listed = set(re.findall(r"`([a-z0-9-]+\.md)`", (folder / "SKILL.md").read_text(encoding="utf-8")))
            present = {p.name for p in (folder / "rules").glob("*.md")}
            with self.subTest(skill=folder.name):
                self.assertEqual(listed & present, present, f"rule files not in {folder.name}/SKILL.md: {sorted(present - listed)}")
                self.assertEqual(listed - present, set(), "listed rule files that do not exist")


@unittest.skipUnless(HAVE_OPENMC, "needs OpenMC (WSL, the automc environment)")
class OpenMCNames(unittest.TestCase):
    def test_the_resolver_tells_real_names_from_made_up_ones(self):
        for name in ("openmc.Material.add_nuclide", "openmc.Settings.temperature", "openmc.deplete.CoupledOperator",
                     "openmc.lib.settings.weight_windows_on", "openmc.mgxs.GROUP_STRUCTURES"):
            self.assertTrue(resolve(name), name)
        for name in ("openmc.Material.add_isotope", "openmc.Settings.histories", "openmc.deplete.Burner", "openmc.nosuch"):
            self.assertFalse(resolve(name), name)

    def test_every_openmc_name_in_the_skills_exists(self):
        names = {(p.relative_to(SKILLS).as_posix(), n) for p, text in skill_texts().items() for n in _OPENMC_NAME.findall(text)}
        self.assertGreater(len(names), 150)
        missing = sorted(f"{f}: {n}" for f, n in names if not resolve(n))
        self.assertEqual(missing, [], "names that are not in the installed OpenMC:\n" + "\n".join(missing))


try:
    from tests.openmc.test_authoring import HAVE_OPENMC as HAVE_OPENMC_RUN, Workspace
except ImportError:                                              # an environment without the tools' dependencies
    HAVE_OPENMC_RUN, Workspace = False, unittest.TestCase


@unittest.skipUnless(HAVE_OPENMC_RUN, "needs OpenMC (WSL, the automc environment)")
class Template(Workspace):
    def test_the_template_builds_and_passes_the_check(self):
        from tools import registry
        _, built = self.build("template", (SKILLS / "openmc-authoring" / "templates" / "build.py").read_text(encoding="utf-8"))
        self.assertTrue(built["built"], built)
        found = registry.call("OpenMCModelCheckTool", {"case": "template"})
        self.assertTrue(found["passed"], found["findings"])
