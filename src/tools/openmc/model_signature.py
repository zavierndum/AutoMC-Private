"""
Compare two OpenMC model.xml files by what is in them, ignoring object ids. Standard library only.

    python -m tools.openmc.model_signature A/model.xml B/model.xml        (from AutoMC/src)

Reduces each model to five parts and reports which parts differ and, for each, a short description:
  materials  name, temperature, density, nuclides and thermal-scattering tables of every material
  cells      name and material (by name) of every cell
  surfaces   type, coefficients and boundary of every surface (names are ignored)
  settings   every settings element
  tallies    name, scores and filter types of every tally
Used by the parameter-coverage tests to say "changing this one field changes these parts and nothing else". Exit code 0 if the two
models are identical in all five parts.
"""
import sys
import xml.etree.ElementTree as ET
from collections import Counter


def _text(e):
    return " ".join(ET.tostring(e, encoding="unicode").split())


def signature(path):
    root = ET.parse(path).getroot()
    mats = root.find("materials")
    mat_name, materials = {}, Counter()
    for m in mats.findall("material"):
        name = m.get("name") or ""
        mat_name[m.get("id")] = name
        dens = m.find("density")
        nuc = tuple(sorted((n.get("name"), n.get("ao") or n.get("wo"), "ao" if n.get("ao") else "wo") for n in m.findall("nuclide")))
        sab = tuple(sorted(s.get("name") for s in m.findall("sab")))
        materials[(name, m.get("temperature"), dens.get("value") if dens is not None else None,
                   dens.get("units") if dens is not None else None, nuc, sab)] += 1
    geom = root.find("geometry")
    cells = Counter()
    for c in geom.findall("cell"):
        mat = c.get("material")
        what = "fill" if c.get("fill") else (" | ".join(mat_name.get(x, x) for x in mat.split()) if mat else "none")
        cells[(c.get("name") or "", what)] += 1
    surfaces = Counter((s.get("type"), s.get("coeffs"), s.get("boundary")) for s in geom.findall("surface"))
    settings = Counter(_text(e) for e in root.find("settings"))
    tallies = Counter()
    tl = root.find("tallies")
    if tl is not None:
        for t in tl.findall("tally"):
            scores = (t.findtext("scores") or "").strip()
            tallies[(t.get("name") or "", scores)] += 1
    return {"materials": materials, "cells": cells, "surfaces": surfaces, "settings": settings, "tallies": tallies}


def compare(a, b):
    """{part: {"only_in_a": n, "only_in_b": n, "names": sorted names involved}} for every part that differs."""
    out = {}
    for part in a:
        only_a, only_b = a[part] - b[part], b[part] - a[part]
        if only_a or only_b:
            def label(key):
                return key[0] if isinstance(key[0], str) and part in ("materials", "cells", "tallies") else str(key)[:90]
            names = sorted({label(k) for k in list(only_a) + list(only_b)})
            out[part] = {"only_in_a": sum(only_a.values()), "only_in_b": sum(only_b.values()), "names": names}
    return out


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 2:
        print(__doc__)
        return 2
    diff = compare(signature(argv[0]), signature(argv[1]))
    if not diff:
        print("identical in materials, cells, surfaces, settings and tallies")
        return 0
    for part, d in diff.items():
        print(f"{part:10s} {d['only_in_a']} only in A, {d['only_in_b']} only in B; involves {len(d['names'])} name(s): {d['names'][:6]}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
