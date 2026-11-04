"""
The system prompt file (prompts/system_prompt_automc.yml) and its loader (agent/prompt.py): the shipped file loads under its closed
schema; every authored string reaches the model exactly as written, and the evaluation fields never do; a provider sees only its
channel's sections and examples; each example's answer holds the phrases it must and none it must not, and calls only registered
tools; a bad file (an unknown key, a key given twice, an unquoted colon in an item, an unknown channel, a missing answer) is refused
with the node at fault.
"""
import re
import shutil
import tempfile
import unittest
from pathlib import Path

from agent import prompt


def _norm(s):
    return re.sub(r"\s+", " ", s).strip()


def _authored(doc):
    """Every string the model is meant to see, with the channels it is for."""
    for s in doc.sections:
        for t in (s.title, s.text, *s.items):
            yield t, s.channels
        for sub in s.subsections:
            for t in (sub.title, sub.text, *sub.items):
                yield t, s.channels
    for f in doc.few_shots:
        for t in (f.title, f.user, f.answer, f.teaches):
            yield t, f.channels


class ShippedFile(unittest.TestCase):
    def setUp(self):
        self.doc = prompt.load_prompt()

    def test_it_loads_and_starts_with_who_the_agent_is(self):
        self.assertEqual(self.doc.sections[0].id, "identity")
        self.assertIn("You are AutoMC", self.doc.sections[0].text)

    def test_every_authored_string_reaches_its_channel_exactly(self):
        for channel, cloud in (("local", False), ("cloud", True)):
            text = _norm(prompt.render(self.doc, cloud))
            meant = " ".join(_norm(s) for s, c in _authored(self.doc) if s and channel in c)
            for s, channels in _authored(self.doc):
                if not s:
                    continue
                if channel in channels:
                    self.assertIn(_norm(s), text, f"{channel}: {s[:80]!r}")
                elif _norm(s) not in meant:                             # text for the other channel only
                    self.assertNotIn(_norm(s), text, f"{channel} sees {s[:80]!r}")

    def test_the_evaluation_fields_are_never_rendered(self):
        text = prompt.render(self.doc, False) + prompt.render(self.doc, True)
        self.assertNotIn("must_include", text)
        self.assertNotIn("must_not_include", text)

    def test_each_example_answer_holds_what_it_must_and_nothing_it_must_not(self):
        answers = [f.answer.lower() for f in self.doc.few_shots]
        for f in self.doc.few_shots:
            with self.subTest(example=f.id):
                for alts in f.must_include:
                    self.assertTrue(any(a.lower() in f.answer.lower() for a in alts), f"{f.id} lacks one of {alts}")
                for bad in f.must_not_include:
                    for a in answers:                                   # no example shows what another warns against
                        self.assertNotIn(bad.lower(), a, f"an example answer holds {bad!r}, which {f.id} warns against")

    def test_examples_call_only_registered_tools(self):
        from tools import registry
        names = set(registry.names())
        for f in self.doc.few_shots:
            self.assertEqual([t for t in f.tools if t not in names], [], f.id)

    def test_the_routing_section_names_every_tool_and_the_prompt_no_other(self):
        from tools import registry
        names = set(registry.names())
        routing = prompt._render_section(next(s for s in self.doc.sections if s.id == "routing"))
        self.assertEqual(sorted(n for n in names if n not in routing), [], "tools the routing section leaves out")
        text = prompt.render(self.doc, False) + prompt.render(self.doc, True)
        self.assertEqual(sorted(set(re.findall(r"\b[A-Z]\w+Tool\b", text)) - names), [], "tool names that are not registered")


class Channels(unittest.TestCase):
    def test_a_section_and_an_example_reach_only_their_channel(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "p.yml"
            path.write_text(
                "sections:\n"
                "  a: {title: Both, channels: [local, cloud], text: seen by every model}\n"
                "  b: {title: Away, channels: [cloud], items: [only outside this machine]}\n"
                "few_shots:\n"
                "  - {id: x, title: Here, channels: [local], user: hi, answer: hello from here, teaches: local only}\n", encoding="utf-8")
            doc = prompt.load_prompt(path)
            local, cloud = prompt.render(doc, False), prompt.render(doc, True)
        self.assertIn("seen by every model", local)
        self.assertIn("seen by every model", cloud)
        self.assertNotIn("only outside this machine", local)
        self.assertIn("- only outside this machine", cloud)
        self.assertIn("### Example 1: Here\nUser: hi\nTools a good answer calls: none\nA good answer:\nhello from here", local)
        self.assertNotIn("## Examples", cloud)


class BadFiles(unittest.TestCase):
    GOOD = ("sections:\n"
            "  identity:\n"
            "    title: Who you are\n"
            "    channels: [local, cloud]\n"
            "    text: You are AutoMC.\n"
            "    items:\n"
            "      - one rule\n"
            "few_shots:\n"
            "  - id: one\n"
            "    title: an example\n"
            "    channels: [local]\n"
            "    user: hello\n"
            "    answer: hi\n"
            "    teaches: greeting\n")

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="automc_prompt_file_"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def load(self, text):
        path = self.tmp / "p.yml"
        path.write_text(text, encoding="utf-8")
        return prompt.load_prompt(path)

    def test_the_good_file_loads(self):
        doc = self.load(self.GOOD)
        self.assertEqual((doc.sections[0].items, doc.few_shots[0].id), (("one rule",), "one"))

    def test_each_fault_is_refused_with_its_node(self):
        cases = [
            (self.GOOD.replace("    text: You are AutoMC.\n", "    text: You are AutoMC.\n    colour: red\n"), r"sections\.identity: unknown key"),
            (self.GOOD.replace("    text: You are AutoMC.\n", "    text: You are AutoMC.\n    text: Second.\n"), r"'text' is given twice"),
            (self.GOOD + "sections: {}\n", r"'sections' is given twice"),
            (self.GOOD.replace("      - one rule\n", "      - Rods: an unquoted colon\n"), r"sections\.identity\.items: expected a list"),
            (self.GOOD.replace("[local, cloud]", "[web]"), r"sections\.identity\.channels"),
            (self.GOOD.replace("    answer: hi\n", ""), r"few_shots\[0\]\.answer"),
            (self.GOOD.replace("few_shots:", "examples:"), r"unknown key\(s\) \['examples'\]"),
            (self.GOOD + self.GOOD[self.GOOD.index("  - id: one"):], r"ids must be unique"),
            (self.GOOD.replace("    text: You are AutoMC.\n    items:\n      - one rule\n", ""), r"needs text, items or subsections"),
            ("sections:\n  - {title: a list, channels: [local], text: x}\n", r"sections must be a non-empty mapping"),
        ]
        for text, message in cases:
            with self.subTest(message=message):
                with self.assertRaisesRegex(prompt.PromptError, message):
                    self.load(text)


if __name__ == "__main__":
    unittest.main()
