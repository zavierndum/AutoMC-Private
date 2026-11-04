"""
Nothing in AutoMC's skills or system prompt is copied from OpenMC's documentation, its script examples or the OpenMC notebooks: they
are adapted, in our own words and code, with the physics and the numbers kept. Every text file under src/skills and src/prompts is
compared with them:

    prose   a run of PROSE_WORDS (12) words in common fails, after lower-casing and dropping punctuation, markup and links
    code    a block of CODE_LINES (5) non-trivial lines in common fails, after normalising spacing. Imports, comments, docstrings, lone
            brackets, short one-call lines and data lines do not count. A data line only states facts of the problem, the same in any
            correct model: a material's nuclides, elements, densities and S(a,b) tables, a literal assigned, a row of numbers, a row
            of a lattice's layout (names repeated, nothing else)

The sources are the local clones next to src/ (openmc-reference: the 0.15.3 manual and examples, MIT; openmc-notebooks: no licence
file, so nothing may be copied from it at all). The notebooks are read from the working tree, which equals the tag v0.15.0 except for
shielded_room_weight_window, newer and also an example's source. Where the clones are missing the comparison skips, saying why;
the checker's own tests always run. A failure names our file and line, the source and the shared text, so it can be rewritten.
"""
import io
import json
import re
import tokenize
import unittest
from pathlib import Path

import automc_paths

PROSE_WORDS = 12
CODE_LINES = 5
REFERENCE = automc_paths.AUTOMC_ROOT / "openmc-reference"
NOTEBOOKS = automc_paths.AUTOMC_ROOT / "openmc-notebooks"
OURS = (automc_paths.SRC / "skills", automc_paths.SRC / "prompts")
OUR_SUFFIXES = {".md", ".py", ".yaml", ".yml"}

_URL = re.compile(r"https?://\S+")
_WORD = re.compile(r"[a-z0-9]+")
_TOKEN = re.compile(r"\w+|[^\w\s]")
_DATA_CALL = re.compile(r"\.(add_nuclide|add_element|add_s_alpha_beta|set_density|add_macroscopic|add_components|remove_nuclide)\(")
_LITERAL = re.compile(r"""[rbuf]*("([^"\\]|\\.)*"|'([^'\\]|\\.)*')|-?\d[\d_]*\.?[\d_]*(e[-+]?\d+)?|\b(True|False|None)\b""",
                      re.IGNORECASE)


# ----------------------------------------------------------------------------------------------------------------------
# units: a file, or one notebook cell, split into prose lines and code lines [(line number, text)]
# ----------------------------------------------------------------------------------------------------------------------
def split_python(text):
    """(prose, code): comments and triple-quoted strings are prose; the rest, with trailing comments cut, is code."""
    lines = text.splitlines()
    prose, skip, cut = [], set(), {}
    try:
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type == tokenize.COMMENT:
                prose.append((tok.start[0], tok.string.lstrip("#")))
                cut[tok.start[0]] = tok.start[1]
            elif tok.type == tokenize.STRING and tok.string.lstrip("rbufRBUF")[:3] in ('"""', "'''"):
                prose += [(tok.start[0] + k, part) for k, part in enumerate(tok.string.splitlines())]
                skip.update(range(tok.start[0], tok.end[0] + 1))
    except (tokenize.TokenError, SyntaxError):                   # a notebook cell with shell or magic lines: all of it is code
        return [], list(enumerate(lines, 1))
    return prose, [(n, line[:cut.get(n, len(line))]) for n, line in enumerate(lines, 1) if n not in skip]


def split_markdown(text):
    """(prose, code): fenced blocks are code, the rest prose."""
    prose, code, fenced = [], [], False
    for n, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith("```"):
            fenced = not fenced
        elif fenced:
            code.append((n, line))
        else:
            prose.append((n, line))
    return prose, code


def split_file(path, text):
    if path.suffix == ".py":
        return split_python(text)
    if path.suffix == ".md":
        return split_markdown(text)
    return list(enumerate(text.splitlines(), 1)), []             # YAML: prose


def our_units(roots=OURS):
    out = []
    for root in roots:
        for p in sorted(Path(root).rglob("*")):
            if p.is_file() and p.suffix in OUR_SUFFIXES and "__pycache__" not in p.parts:
                prose, code = split_file(p, p.read_text(encoding="utf-8", errors="replace"))
                out.append((p.relative_to(automc_paths.SRC).as_posix(), prose, code))
    return out


def source_units(reference=REFERENCE, notebooks=NOTEBOOKS):
    """The manual's pages (every line is prose, and also code, for its code blocks), the script examples and the notebooks' cells."""
    out = []
    for p in sorted((Path(reference) / "docs" / "source").rglob("*.rst")):
        lines = list(enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1))
        out.append((f"openmc-reference/{p.relative_to(reference).as_posix()}", lines, lines))
    for p in sorted((Path(reference) / "examples").rglob("*.py")):
        out.append((f"openmc-reference/{p.relative_to(reference).as_posix()}",) + split_python(p.read_text(encoding="utf-8")))
    for p in sorted(Path(notebooks).glob("*.ipynb")):
        for i, cell in enumerate(json.loads(p.read_text(encoding="utf-8")).get("cells", [])):
            text = "".join(cell.get("source", []))
            name = f"openmc-notebooks/{p.name} cell {i}"
            if cell.get("cell_type") == "markdown":
                out.append((name, list(enumerate(text.splitlines(), 1)), []))
            elif cell.get("cell_type") == "code":
                out.append((name,) + split_python(text))
    return out


# ----------------------------------------------------------------------------------------------------------------------
# normalising and comparing
# ----------------------------------------------------------------------------------------------------------------------
def words(lines):
    """[(line number, word)]: lower case, links dropped, letters and digits only."""
    return [(n, w) for n, line in lines for w in _WORD.findall(_URL.sub(" ", line.lower()))]


def is_data(line):
    """A line that only states facts: a nuclide or density call, a literal assigned, a row of literals, a row of a lattice layout."""
    s = line.strip()
    if _DATA_CALL.search(s):
        return True
    row = re.sub(r"^[\w.]+\s*=\s*", "", s)                         # a layout's first row comes after "lattice.universes ="
    names = re.findall(r"\w+", row)
    if names and not re.sub(r"[\w\s,\[\]()]", "", row) and len(set(names)) < len(names):
        return True
    rest = s.split("=", 1)[1] if re.match(r"^[\w.\[\]'\"]+\s*=[^=]", s) else s
    return not re.sub(r"[\s,()\[\]{}:+-]", "", _LITERAL.sub("", rest))


def code_lines(lines):
    """[(line number, normalised line)] of the lines that count: spacing normalised, the trivial and the data lines left out."""
    out = []
    for n, line in lines:
        s = line.strip()
        if s.startswith((">>> ", "... ")):                         # the manual's interactive examples
            s = s[4:].strip()
        toks = _TOKEN.findall(s)
        names = [t for t in toks if re.match(r"\w", t)]
        if not s or s.startswith("#") or len(names) < 3 or re.match(r"^(import|from)\s", s) or is_data(s):
            continue
        out.append((n, " ".join(toks)))
    return out


def index(units):
    """The sources' prose runs and code blocks: {text: source name}."""
    prose, code = {}, {}
    for name, p_lines, c_lines in units:
        ws = [w for _, w in words(p_lines)]
        for i in range(len(ws) - PROSE_WORDS + 1):
            prose.setdefault(" ".join(ws[i:i + PROSE_WORDS]), name)
        cs = [c for _, c in code_lines(c_lines)]
        for i in range(len(cs) - CODE_LINES + 1):
            code.setdefault("\n".join(cs[i:i + CODE_LINES]), name)
    return prose, code


def compare(ours, idx):
    """Findings, one per shared passage: "file:line: prose|code shared with SOURCE: text"."""
    prose, code = idx
    found = []
    for name, p_lines, c_lines in ours:
        ws = words(p_lines)
        last = -10
        for i in range(len(ws) - PROSE_WORDS + 1):
            key = " ".join(w for _, w in ws[i:i + PROSE_WORDS])
            if key in prose:
                if i > last + 1:                                   # a new passage, not the same one a word further on
                    found.append(f"{name}:{ws[i][0]}: prose shared with {prose[key]}: '{key}'")
                last = i
        cs = code_lines(c_lines)
        last = -10
        for i in range(len(cs) - CODE_LINES + 1):
            key = "\n".join(c for _, c in cs[i:i + CODE_LINES])
            if key in code:
                if i > last + 1:
                    found.append(f"{name}:{cs[i][0]}: code shared with {code[key]}: {key.splitlines()[0]!r} ...")
                last = i
    return found


# ----------------------------------------------------------------------------------------------------------------------
class Checker(unittest.TestCase):
    """The checker itself, on made-up text: what it catches and what it lets through."""
    SENTENCE = "The quick brown fox jumps over the lazy dog while seven tired geese watch from the riverbank"

    def check(self, source_prose="", source_code="", our_prose="", our_code=""):
        src = [("src", list(enumerate(source_prose.splitlines(), 1)), list(enumerate(source_code.splitlines(), 1)))]
        ours = [("ours", list(enumerate(our_prose.splitlines(), 1)), list(enumerate(our_code.splitlines(), 1)))]
        return compare(ours, index(src))

    def test_twelve_words_in_common_are_caught_eleven_are_not(self):
        words12 = " ".join(self.SENTENCE.split()[:12])
        self.assertEqual(len(self.check(source_prose=self.SENTENCE, our_prose=f"Before. {words12.upper()}, after.")), 1)
        words11 = " ".join(self.SENTENCE.split()[:11])
        self.assertEqual(self.check(source_prose=self.SENTENCE, our_prose=f"Before {words11} after"), [])

    def test_case_punctuation_markup_and_line_breaks_do_not_hide_a_copy(self):
        ours = "The *quick* brown fox, jumps\nover the `lazy` dog. While seven tired geese"
        self.assertEqual(len(self.check(source_prose=self.SENTENCE, our_prose=ours)), 1)

    def test_links_are_not_prose(self):
        url = "https://docs.openmc.org/en/stable/usersguide/geometry.html#using-cad-based-geometry-in-openmc-models-today"
        self.assertEqual(self.check(source_prose=f"See {url}.", our_prose=f"Derived from {url}"), [])

    def test_five_lines_of_code_in_common_are_caught_four_are_not(self):
        block = "\n".join(f"cell_{i} = openmc.Cell(fill=water, region=-sphere_{i})" for i in range(5))
        spaced = block.replace(" = ", "=").replace(", ", ",   ")
        self.assertEqual(len(self.check(source_code=block, our_code=spaced)), 1)
        four = "\n".join(block.splitlines()[:4])
        self.assertEqual(self.check(source_code=block, our_code=four), [])

    def test_imports_comments_brackets_and_data_lines_do_not_count(self):
        block = ("import openmc\nfuel = openmc.Material(name='uo2')\nfuel.add_nuclide('U235', 3.7e-4)\nfuel.add_nuclide('U238', 2.2e-2)\n"
                 "fuel.set_density('g/cm3', 10.4)\n# a comment\nsettings.batches = 100\n[1.0, 2.0,\n 3.0]\n)\n"
                 "clad = openmc.Material(name='zirc')\nwater = openmc.Material(name='h2o')")
        self.assertEqual(self.check(source_code=block, our_code=block), [])          # three lines that count, not five
        self.assertTrue(is_data("fuel.add_s_alpha_beta('c_H_in_H2O')"))
        self.assertTrue(is_data("settings.particles = 1_000"))
        self.assertTrue(is_data("'U235': 3.7e-4,"))
        self.assertTrue(is_data("lattice.universes = [[f, f, g, h,"))           # a layout row: names repeated
        self.assertTrue(is_data("[f, g, h, h, f],"))
        self.assertFalse(is_data("cells = [fuel_cell, clad_cell, water_cell]"))
        self.assertFalse(is_data("model.settings = openmc.Settings()"))

    def test_python_comments_and_docstrings_are_prose_and_markdown_fences_code(self):
        prose, code = split_python('"""Doc\nstring."""\nx = f(y)  # a note\n')
        self.assertEqual([t for _, t in prose], ['"""Doc', 'string."""', " a note"])
        self.assertEqual(code, [(3, "x = f(y)  ")])
        prose, code = split_markdown("Text\n```python\nx = 1\n```\nMore")
        self.assertEqual(([n for n, _ in prose], code), ([1, 5], [(3, "x = 1")]))


@unittest.skipUnless((REFERENCE / "docs" / "source").is_dir() and NOTEBOOKS.is_dir(),
                     f"needs the OpenMC sources next to src/ ({REFERENCE.name}/ and {NOTEBOOKS.name}/), which are not in the repository")
class Originality(unittest.TestCase):
    """Every skill file, example and the system prompt against the manual, the script examples and the notebooks."""

    @classmethod
    def setUpClass(cls):
        cls.sources = source_units()
        cls.idx = index(cls.sources)

    def test_the_sources_were_read(self):
        names = {name.split(" cell ")[0] for name, _, _ in self.sources}
        self.assertGreater(sum(n.endswith(".rst") for n in names), 100)
        self.assertGreaterEqual(sum(n.endswith(".ipynb") for n in names), 28)
        self.assertGreaterEqual(sum(n.startswith("openmc-reference/examples") for n in names), 10)
        self.assertGreater(len(self.idx[0]), 100_000)

    def test_nothing_in_the_skills_or_the_prompt_is_copied(self):
        ours = our_units()
        self.assertGreater(len(ours), 100)
        found = compare(ours, self.idx)
        self.assertEqual(found, [], f"{len(found)} shared passage(s); rewrite them in our own words and code:\n" + "\n".join(found[:60]))


if __name__ == "__main__":
    unittest.main()
