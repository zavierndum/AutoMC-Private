"""
Geometry preview worker: reads a case's model.xml and draws coloured slices with a legend. Needs OpenMC and matplotlib only (no
CoreForge), runs no transport, and runs in its own process like the build worker.

    python -m tools.reactors.triga._preview --case DIR [--which core_xy,world_xy,...] [--pixels 1000]

Writes DIR/preview/<name>.png and DIR/preview/previews.json.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from .facilities import netl

PREVIEWS = {
    # name: (basis, origin, (width, height) in cm). The slice through the core midplane, and a wide view that shows the pool,
    # the beam ports and the barite region.
    "core_xy": ("xy", (0.0, 0.0, 0.0), (70.0, 70.0)),
    "world_xy": ("xy", (0.0, 0.0, netl.BEAM_PORT_AXIAL_OFFSET_CM), (620.0, 620.0)),
    "core_xz": ("xz", (0.0, 0.0, 0.0), (70.0, 170.0)),
    "world_xz": ("xz", (0.0, 0.0, 0.0), (620.0, 320.0)),
}

# material name fragment -> (category, colour); first match wins, so order matters. The insert filters come before the generic "b4c" and
# "absorber" entries, so the 3-EL's boron carbide is not drawn as a control rod.
_CATEGORIES = (
    ("natural_b4c", "boron carbide filter", "darkviolet"), ("enriched_boron", "boron-10 filter", "magenta"),
    ("silver", "silver", "lightsteelblue"), ("lead", "lead", "darkslategray"),
    ("depleted_fuel", "depleted fuel", "orange"), ("fuel_follower", "fuel follower", "darkorange"), ("uzrh", "fresh fuel", "gold"),
    ("barite", "barite concrete", "tan"), ("water", "water", "lightblue"), ("graphite", "graphite", "gray"),
    ("zirc", "zirconium", "seagreen"), ("zr", "zirconium", "seagreen"), ("stainless", "stainless steel", "black"),
    ("ss304", "stainless steel", "black"), ("aluminum", "aluminum", "silver"), ("air", "air", "white"),
    ("absorber", "control absorber", "red"), ("b4c", "control absorber", "red"), ("cadmium", "cadmium", "crimson"),
    ("mo", "molybdenum", "purple"),
)
_OTHER = ("other", "lightgray")


def category_for(name):
    n = (name or "").lower()
    for fragment, category, colour in _CATEGORIES:
        if fragment in n:
            return category, colour
    return _OTHER


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", required=True)
    ap.add_argument("--which", default=",".join(PREVIEWS))
    ap.add_argument("--pixels", type=int, default=1000)
    a = ap.parse_args(argv)

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Patch
    import openmc

    case = Path(a.case)
    out = case / "preview"
    out.mkdir(exist_ok=True)
    model = openmc.Model.from_model_xml(str(case / "model.xml"))
    colors, used = {}, {}
    for m in model.materials:
        category, colour = category_for(m.name)
        colors[m] = colour
        used[category] = colour
    exe = shutil.which("openmc") or str(Path(sys.executable).parent / "openmc")

    written = []
    for name in [w for w in a.which.split(",") if w]:
        basis, origin, (w, h) = PREVIEWS[name]
        nx, ny = a.pixels, max(50, round(a.pixels * h / w))
        ax = model.plot(origin=origin, width=(w, h), pixels=(nx, ny), basis=basis, color_by="material", colors=colors,
                        openmc_exec=exe, axis_units="cm")
        ax.set_title(f"{case.name}: {name} (slice at {'z' if basis == 'xy' else 'y'} = "
                     f"{origin[2] if basis == 'xy' else origin[1]:g} cm)", fontsize=9)
        ax.legend(handles=[Patch(facecolor=c, edgecolor="black", linewidth=0.3, label=cat) for cat, c in sorted(used.items())],
                  loc="center left", bbox_to_anchor=(1.01, 0.5), fontsize=7, frameon=False)
        path = out / f"{name}.png"
        ax.figure.savefig(path, dpi=150, bbox_inches="tight")
        plt.close(ax.figure)
        written.append({"name": name, "path": str(path), "bytes": path.stat().st_size})
    (out / "previews.json").write_text(json.dumps(written, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
