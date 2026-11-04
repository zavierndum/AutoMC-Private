"""
How AutoMC's command lines print, in the style of the TRIGA Control Room agent's scripts (scripts/_common.sh, banner): a title between
two rules of '=', then aligned fields and tables, and text wrapped to the terminal. Plain Python. Every function returns a string; colour
is added only by paint(), on a terminal, and never when NO_COLOR is set, so a plan's text stays plain wherever it is shown.
"""
from __future__ import annotations

import os
import re
import shutil
import sys
import textwrap

RULE = 78
CYAN, DIM, BOLD, YELLOW, RED, GREEN, RESET = "\033[36m", "\033[2m", "\033[1m", "\033[33m", "\033[31m", "\033[32m", "\033[0m"


def color_on(stream=None):
    stream = stream or sys.stdout
    return not os.environ.get("NO_COLOR") and hasattr(stream, "isatty") and stream.isatty()


def paint(text, code, stream=None):
    return f"{code}{text}{RESET}" if color_on(stream) else text


def width():
    """The terminal's width, between 60 and 120 columns."""
    return min(max(shutil.get_terminal_size((100, 24)).columns, 60), 120)


def banner(title, rule=RULE):
    """A title between two rules of '=', as the TRIGA agent's banner()."""
    line = "=" * rule
    return f"{line}\n {title}\n{line}"


def fields(pairs, indent=2):
    """Aligned 'Name  value' lines; a long value wraps under itself. Pairs whose value is empty are left out."""
    pairs = [(k, str(v)) for k, v in pairs if v not in (None, "", [])]
    if not pairs:
        return ""
    key = max(len(k) for k, _ in pairs)
    lead = " " * indent
    room = max(width() - indent - key - 2, 30)
    out = []
    for k, v in pairs:
        wrapped = textwrap.wrap(v, room, break_long_words=False, break_on_hyphens=False) or [""]
        out.append(f"{lead}{k:<{key}}  {wrapped[0]}")
        out += [f"{lead}{'':<{key}}  {w}" for w in wrapped[1:]]
    return "\n".join(out)


def _cut(text, n):
    text = str(text)
    return text if len(text) <= n else text[:max(n - 3, 1)] + "..."


def table(headers, rows, right=(), indent=2, limits=None):
    """An aligned table with a rule under the header. `right`: the columns aligned right (numbers); `limits`: {column: most
    characters}, longer cells end in '...'. The last column is never padded."""
    limits = limits or {}
    rows = [[_cut("" if c is None else c, limits.get(i, 10_000)) for i, c in enumerate(r)] for r in rows]
    widths = [max([len(h)] + [len(r[i]) for r in rows]) for i, h in enumerate(headers)]

    def line(cells):
        out = []
        for i, c in enumerate(cells):
            last = i == len(cells) - 1
            out.append(c.rjust(widths[i]) if i in right else (c if last else c.ljust(widths[i])))
        return (" " * indent + "  ".join(out)).rstrip()

    return "\n".join([line(headers), line(["-" * w for w in widths])] + [line(r) for r in rows])


def wrap(text, indent=4, limit=None):
    """Text wrapped to the terminal under an indent, paragraph by paragraph; at most `limit` characters (then '...')."""
    text = str(text or "")
    cut = limit is not None and len(text) > limit
    if cut:
        text = text[:limit].rstrip()
    lead = " " * indent
    out = []
    for para in text.splitlines():
        if not para.strip():
            continue
        sub = lead + " " * (len(para) - len(para.lstrip())) if para.lstrip().startswith(("-", "*")) else lead
        out += textwrap.wrap(para.strip(), width() - indent, initial_indent=lead, subsequent_indent=sub + "  " if sub != lead else lead,
                             break_long_words=False, break_on_hyphens=False) or [lead]
    if cut:
        out[-1] += " ..." if out else lead + "..."
    return "\n".join(out)


def plain_markdown(text):
    """A Visual-Parser description without its **bold** markers, for reading in a terminal."""
    return re.sub(r"\*\*([^*\n]+)\*\*", r"\1", str(text or ""))
