"""console.py: the banner, fields, tables and wrapped text AutoMC's command lines print, and colour only on a terminal."""
import io
import os
import unittest
from unittest import mock

import console


class Console(unittest.TestCase):
    def test_a_banner_is_a_title_between_two_rules(self):
        lines = console.banner("AutoMC chat   local").split("\n")
        self.assertEqual(lines, ["=" * 78, " AutoMC chat   local", "=" * 78])

    def test_fields_align_and_skip_empty_values(self):
        out = console.fields([("Folder", "/tmp/a"), ("Label", "public"), ("Error", None), ("Kept", "")])
        self.assertEqual(out.split("\n"), ["  Folder  /tmp/a", "  Label   public"])

    def test_a_long_field_wraps_under_itself(self):
        with mock.patch.object(console, "width", return_value=60):
            out = console.fields([("Vision", "word " * 40)]).split("\n")
        self.assertGreater(len(out), 2)
        self.assertTrue(all(line.startswith("          ") for line in out[1:]))     # under the value, not under the name
        self.assertTrue(all(len(line) <= 60 for line in out))

    def test_a_table_aligns_cuts_and_puts_numbers_right(self):
        out = console.table(["#", "File", "Note"], [[1, "a.pdf", "x"], [12, "b" * 80, "y"]], right={0}, limits={1: 20}).split("\n")
        self.assertEqual(out[0], "   #  File                  Note")
        self.assertEqual(out[1], "  --  --------------------  ----")
        self.assertEqual(out[2], "   1  a.pdf                 x")
        self.assertTrue(out[3].startswith("  12  " + "b" * 17 + "..."))

    def test_wrapping_and_its_limit(self):
        text = "first paragraph " * 20 + "\n\n- a list item that goes on " * 2
        out = console.wrap(text, indent=4, limit=150)
        self.assertTrue(out.endswith("..."))
        self.assertTrue(all(line.startswith("    ") for line in out.split("\n")))
        self.assertEqual(console.plain_markdown("**Subject:** a map"), "Subject: a map")

    def test_colour_only_on_a_terminal_and_never_with_no_color(self):
        tty = io.StringIO()
        tty.isatty = lambda: True
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("NO_COLOR", None)
            self.assertEqual(console.paint("x", console.CYAN, io.StringIO()), "x")
            self.assertIn("\033[36m", console.paint("x", console.CYAN, tty))
            os.environ["NO_COLOR"] = "1"
            self.assertEqual(console.paint("x", console.CYAN, tty), "x")


if __name__ == "__main__":
    unittest.main()
