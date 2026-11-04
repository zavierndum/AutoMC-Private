"""
The process that builds, plots or checks a script-built OpenMC model (tools/openmc/authoring.py). Run isolated, in the case's folder:

    python -I _author_child.py build '{"script": "build.py"}'
    python -I _author_child.py plot  '{"views": [...], "pixels": 800, "color_by": "material", "out": "plots"}'
    python -I _author_child.py check '{}'

It imports only the standard library, OpenMC, NumPy and matplotlib (never AutoMC), and ends with one line,
`AUTOMC_REPORT {json}`, which authoring.py reads. Nothing here runs transport: before a build script runs, every OpenMC function that
would is replaced by one that refuses.
"""
from __future__ import annotations

import json
import os
import random
import runpy
import shutil
import sys
import traceback
import xml.etree.ElementTree as ET
from pathlib import Path

REFUSAL = ("AutoMC: a build script only writes the model; transport is started by OpenMCRunStartTool after the user's approval")
OVERLAP_RGB = (255, 0, 0)


def report(**fields):
    print("AUTOMC_REPORT " + json.dumps(fields, default=str), flush=True)


def _refuse(*args, **kwargs):
    raise RuntimeError(REFUSAL)


def disarm(openmc):
    """Replace what runs transport with a refusal (the script text was already checked; this catches other routes)."""
    openmc.run = _refuse
    openmc.Model.run = _refuse
    for name in ("search_for_keff",):
        if hasattr(openmc, name):
            setattr(openmc, name, _refuse)
        if hasattr(openmc.model, name):
            setattr(openmc.model, name, _refuse)
    try:
        import openmc.lib as lib
        for name in ("run", "run_in_memory", "simulation_init", "next_batch", "iter_batches", "init"):
            if hasattr(lib, name):
                setattr(lib, name, _refuse)
    except Exception:
        pass
    try:
        import openmc.deplete.abc as dabc
        for cls in (getattr(dabc, "Integrator", None), getattr(dabc, "SIIntegrator", None)):
            if cls is not None:
                cls.integrate = _refuse
    except Exception:
        pass


def summary(model):
    g = model.geometry
    cells = g.get_all_cells()
    s = model.settings
    return {"materials": len(model.materials), "cells": len(cells), "surfaces": len(g.get_all_surfaces()),
            "universes": len(g.get_all_universes()), "lattices": len(g.get_all_lattices()), "run_mode": s.run_mode,
            "particles": s.particles, "batches": s.batches, "inactive": s.inactive, "tallies": len(model.tallies),
            "dagmc": any(type(u).__name__ == "DAGMCUniverse" for u in g.get_all_universes().values())}


# ----------------------------------------------------------------------------------------------------------------------
def build(args):
    import openmc
    disarm(openmc)
    openmc.config["resolve_paths"] = False            # a file the script names (an MG library) stays relative to the case, not to
                                                      # this staging folder, which is gone once the case is moved in
    sys.path.insert(0, os.getcwd())                   # helper modules written next to build.py
    sys.argv = [args["script"]]                       # the script sees no arguments of ours: its defaults (the quick size) apply
    try:
        runpy.run_path(args["script"], run_name="__main__")
    except SystemExit as e:
        if e.code not in (0, None):
            report(ok=False, error=f"build.py exited with code {e.code}")
            return
    except Exception as e:
        traceback.print_exc()
        report(ok=False, error=f"{type(e).__name__}: {str(e)[:400]}")
        return
    here = Path(".")
    if not (here / "model.xml").is_file() and all((here / f).is_file() for f in ("materials.xml", "geometry.xml", "settings.xml")):
        openmc.Model.from_xml().export_to_model_xml()  # separate files: joined into model.xml, which the job service runs
    if not (here / "model.xml").is_file():
        report(ok=False, error="the script did not write model.xml (end it with model.export_to_model_xml())")
        return
    try:
        model = openmc.Model.from_model_xml("model.xml")
    except Exception as e:
        report(ok=False, error=f"model.xml does not read back: {type(e).__name__}: {str(e)[:300]}")
        return
    report(ok=True, openmc_version=openmc.__version__, model=summary(model))


# ----------------------------------------------------------------------------------------------------------------------
def _finite_box(model):
    """The geometry's bounding box, made finite: an infinite side is replaced by the extent of the source points, or 100 cm."""
    import numpy as np
    ll, ur = model.geometry.bounding_box
    ll, ur = np.array(ll, dtype=float), np.array(ur, dtype=float)
    pts = source_points(model, 50)
    for i in range(3):
        if not (np.isfinite(ll[i]) and np.isfinite(ur[i])):
            vals = [p[i] for p in pts] or [0.0]
            ll[i] = min(vals) - 50.0 if not np.isfinite(ll[i]) else ll[i]
            ur[i] = max(vals) + 50.0 if not np.isfinite(ur[i]) else ur[i]
    return ll, ur


def default_views(model):
    ll, ur = _finite_box(model)
    c = (ll + ur) / 2.0
    span = (ur - ll) * 1.04
    return [{"basis": "xy", "origin": list(c), "width": [span[0], span[1]]},
            {"basis": "xz", "origin": list(c), "width": [span[0], span[2]]},
            {"basis": "yz", "origin": list(c), "width": [span[1], span[2]]}]


def _exe():
    return shutil.which("openmc") or str(Path(sys.executable).parent / "openmc")


PALETTE = [(31, 119, 180), (255, 127, 14), (44, 160, 44), (148, 103, 189), (140, 86, 75), (227, 119, 194), (127, 127, 127),
           (188, 189, 34), (23, 190, 207), (174, 199, 232), (255, 187, 120), (152, 223, 138), (197, 176, 213), (196, 156, 148),
           (247, 182, 210), (199, 199, 199), (219, 219, 141), (158, 218, 229), (57, 59, 121), (82, 84, 163)]   # no pure red: overlaps


def colors_for(model, color_by):
    """A fixed colour for each material or cell (the same from plot to plot); a legend needs them."""
    items = list(model.materials) if color_by == "material" else list(model.geometry.get_all_cells().values())
    items.sort(key=lambda x: x.id)
    return {x: PALETTE[i % len(PALETTE)] for i, x in enumerate(items)}           # 0 to 255, as OpenMC's plot mode takes them


def draw(model, view, pixels, color_by, show_overlaps):
    """One slice through OpenMC's plot mode; returns the matplotlib axes."""
    w, h = view["width"]
    nx = pixels
    ny = max(50, round(pixels * h / w)) if w > 0 else pixels
    colors = colors_for(model, color_by)
    kw = dict(origin=tuple(view["origin"]), width=(w, h), pixels=(nx, ny), basis=view["basis"], color_by=color_by,
              openmc_exec=_exe(), colors=colors, legend=bool(colors) and len(colors) <= 30, axis_units="cm")
    try:
        return model.plot(show_overlaps=show_overlaps, overlap_color=OVERLAP_RGB, **kw)
    except TypeError:                                   # an OpenMC without overlap marking
        return model.plot(**kw)


def overlap_pixels(ax):
    import numpy as np
    if not ax.images:
        return 0
    img = np.asarray(ax.images[0].get_array())
    if img.ndim != 3:
        return 0
    rgb = img[..., :3]
    scale = 255.0 if rgb.max() <= 1.0 else 1.0
    return int(np.all(np.abs(rgb * scale - np.array(OVERLAP_RGB)) < 1.5, axis=-1).sum())


AXES = {"xy": (0, 1, 2), "xz": (0, 2, 1), "yz": (1, 2, 0)}       # (horizontal, vertical, normal) of each basis


def mark_sources(ax, model, view):
    """Mark the source points that lie in the slice (within 2% of its width of the plane) with a black star."""
    h, v, normal = AXES[view["basis"]]
    tol = 0.02 * max(view["width"])
    pts = [p for p in source_points(model, 200) if abs(p[normal] - view["origin"][normal]) <= tol]
    if pts:
        old = ax.get_legend()                              # OpenMC's legend of materials or cells: kept, the source added to it
        handles = list(getattr(old, "legend_handles", None) or getattr(old, "legendHandles", None) or []) if old else []
        labels = [t.get_text() for t in old.get_texts()] if old else []
        star, = ax.plot([p[h] for p in pts], [p[v] for p in pts], marker="*", color="black", markersize=10 if len(pts) < 5 else 3,
                        linestyle="none")
        ax.legend(handles + [star], labels + ["source"], loc="upper left", bbox_to_anchor=(1.02, 1.0))


def plot(args):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import openmc
    model = openmc.Model.from_model_xml("model.xml")
    views = args.get("views") or default_views(model)
    out = Path(args["out"])
    out.mkdir(exist_ok=True)
    written, overlaps = [], 0
    for i, v in enumerate(views, 1):
        v = {"basis": v.get("basis", "xy"), "origin": v.get("origin") or [0.0, 0.0, 0.0], "width": v.get("width") or [100.0, 100.0]}
        ax = draw(model, v, int(args.get("pixels", 800)), args.get("color_by", "material"), True)
        n = overlap_pixels(ax)
        overlaps += n
        mark_sources(ax, model, v)
        axis = {"xy": "z", "xz": "y", "yz": "x"}[v["basis"]]
        at = v["origin"][{"xy": 2, "xz": 1, "yz": 0}[v["basis"]]]
        ax.set_title(f"{Path.cwd().name}: {v['basis']} slice at {axis} = {at:g} cm" + (f" ({n} overlap pixels, red)" if n else ""),
                     fontsize=9)
        path = out / f"{i:02d}-{v['basis']}.png"
        ax.figure.savefig(path, dpi=130, bbox_inches="tight")
        plt.close(ax.figure)
        written.append({"basis": v["basis"], "origin": [round(x, 4) for x in v["origin"]], "width": [round(x, 4) for x in v["width"]],
                        "path": str(path.resolve()), "overlap_pixels": n})
    (out / "plots.json").write_text(json.dumps(written, indent=2), encoding="utf-8")
    report(ok=True, plots=written, overlaps_seen=overlaps)


# ----------------------------------------------------------------------------------------------------------------------
def source_points(model, n):
    """Up to n points the sources start from (Point exactly; Box, CartesianIndependent and spheres and cylinders by sampling)."""
    import numpy as np
    import openmc.stats as st
    rng = random.Random(7)
    out = []
    for src in model.settings.source if isinstance(model.settings.source, list) else [model.settings.source]:
        space = getattr(src, "space", None)
        if space is None:
            continue
        if isinstance(space, st.Point):
            out.append(tuple(space.xyz))
        elif isinstance(space, st.Box):
            ll, ur = space.lower_left, space.upper_right
            out += [tuple(rng.uniform(ll[i], ur[i]) for i in range(3)) for _ in range(n)]
        elif hasattr(space, "sample"):
            try:
                out += [tuple(p) for p in np.asarray(space.sample(n, seed=7))]
            except Exception:
                pass
    return out[: max(n, 1)]


def _available(xs_path):
    names = set()
    try:
        for lib in ET.parse(xs_path).getroot().iter("library"):
            names.update((lib.get("materials") or "").split())
    except (OSError, ET.ParseError):
        pass
    return names


def check(args):
    import numpy as np
    import openmc
    findings = []

    def add(severity, check, message, fix=None):
        findings.append({"severity": severity, "check": check, "message": message, "fix": fix})

    try:
        model = openmc.Model.from_model_xml("model.xml")
    except Exception as e:
        add("error", "model reads back", f"model.xml does not load: {type(e).__name__}: {str(e)[:300]}", "fix build.py and build again")
        report(ok=False, findings=findings)
        return
    s, g = model.settings, model.geometry
    info = summary(model)
    # nuclear data
    mg = getattr(s, "energy_mode", "continuous-energy") == "multi-group"
    xs = model.materials.cross_sections or os.environ.get("OPENMC_CROSS_SECTIONS")
    if mg:
        if not xs:
            add("error", "multigroup data", "the model runs in multigroup mode but no multigroup library is named",
                "set materials.cross_sections to the MGXS library (an .h5 file)")
        elif not Path(xs).is_file():
            add("error", "multigroup data", f"the multigroup library {xs} does not exist",
                "write the library in build.py next to model.xml and name it by a relative path (materials.cross_sections = "
                "'mgxs.h5'); a run folder gets a copy of it")
    elif not xs or not Path(xs).is_file():
        add("error", "nuclear data", "no cross_sections.xml: OPENMC_CROSS_SECTIONS is not set and the model names none",
            "set OPENMC_CROSS_SECTIONS (SETUP.md section 3)")
    else:
        have = _available(xs)
        missing = sorted({n for m in model.materials for n in m.get_nuclides() if n not in have})
        sab = sorted({t[0] if isinstance(t, tuple) else t for m in model.materials for t in getattr(m, "_sab", [])} - have)
        if missing:
            add("error", "nuclear data", f"nuclides not in the library {xs}: {', '.join(missing[:20])}",
                "use nuclides the library has (add_element picks the natural isotopes it can), or another library")
        if sab:
            add("error", "nuclear data", f"thermal scattering tables not in the library: {', '.join(sab)}", "check the S(a,b) names")
    # Python writes a material whose fractions mix atom and weight percent, but OpenMC refuses it when it loads the model
    for m in model.materials:
        if len({n.percent_type for n in m.nuclides}) > 1:
            add("error", "materials", f"material {m.name or m.id} gives some fractions as atom percent and some as weight percent; "
                "OpenMC stops on it (Cannot mix atom and weight percents)", "give all of its fractions as atom (ao) or all as weight (wo)")
    # settings
    mode = s.run_mode
    if mode == "eigenvalue":
        if not s.batches or not s.inactive or s.batches <= s.inactive:
            add("error", "settings", f"eigenvalue run with batches {s.batches} and inactive {s.inactive}: there must be inactive batches "
                "and more batches than inactive ones", "for example batches 50, inactive 10")
        elif s.inactive < 10:
            add("warning", "settings", f"only {s.inactive} inactive batches: the fission source may not have converged",
                "check the Shannon entropy (an entropy mesh) and use more inactive batches if it still changes")
        fissile = ("U2", "Pu2", "Np2", "Am2", "Th2", "Cm2", "Cf2")
        if not mg and not any(any(n.startswith(fissile) for n in m.get_nuclides()) for m in model.materials):
            add("error", "settings", "an eigenvalue run with no fissionable material", "use run_mode 'fixed source' with a source, "
                "or add the fuel")
    elif mode == "fixed source":
        if not s.source:
            add("error", "settings", "a fixed-source run without a source", "define settings.source (an IndependentSource)")
    elif mode == "plot":
        add("info", "settings", "a geometry-only model (run_mode plot): it is plotted and checked, never run", None)
    if mode != "plot" and (s.particles or 0) * (s.batches or 0) < 1000:
        add("warning", "settings", f"{s.particles} particles x {s.batches} batches: too few for meaningful statistics",
            "use more particles or batches for a real answer")
    # boundaries
    if info["dagmc"]:
        add("info", "boundaries", "a DAGMC (CAD) geometry: its graveyard and boundaries are in the CAD file and not checked here", None)
    else:
        kinds = {getattr(sf, "boundary_type", "transmission") for sf in g.get_all_surfaces().values()}
        if "vacuum" not in kinds:
            add("warning" if mode == "fixed source" else "info", "boundaries",
                "no vacuum boundary: particles leave the problem only by absorption" +
                ("" if mode == "fixed source" else " (fine for an infinite lattice)"),
                "put a vacuum boundary on the outermost surface unless the problem is meant to be infinite")
    # source points inside the geometry
    pts = source_points(model, 200)
    if not pts and mode == "fixed source":
        add("warning", "source", "the source points could not be sampled here; they are not checked", None)
    elif not info["dagmc"]:
        outside = []
        for p in pts:
            try:
                path = g.find(p)
            except Exception:
                path = []
            if not any(isinstance(x, openmc.Cell) for x in path):
                outside.append(p)
        # OpenMC rejects a source point in no cell and samples again; it stops when fewer than 1 in 20 points are accepted
        # (Settings.source_rejection_fraction, 0.05). A box around the fuel with a fissionable or domain constraint is the usual idiom.
        sources = model.settings.source if isinstance(model.settings.source, list) else [model.settings.source]
        constrained = any((getattr(src, "constraints", None) or {}).get(k) for src in sources for k in ("fissionable", "domains"))
        where = f"{len(outside)} of {len(pts)} source points are in no cell, for example {tuple(round(x, 3) for x in outside[0])}" if outside else ""
        if outside and len(outside) > 0.95 * len(pts):
            add("error", "source", f"{where}: OpenMC will stop, as too few source points can be accepted",
                "move the source inside the geometry, or extend the geometry around it")
        elif outside:
            add("info" if constrained else "warning", "source",
                f"{where}; OpenMC rejects such points and samples again, so the source is cut to the geometry",
                None if constrained else "if the source should not be cut, move it inside the geometry or extend the geometry around it")
    # overlaps, by OpenMC's own plot mode through the source and the middle of the geometry
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        views = default_views(model)
        for p in pts[:2]:
            views.append({**views[0], "origin": list(p)})

        def hits_in(v):
            ax = draw(model, v, 300, "cell", True)
            n = overlap_pixels(ax)
            plt.close(ax.figure)
            return n

        hits, on_surfaces = 0, []
        for v in views:
            n = hits_in(v)
            if not n:
                continue
            # A slice lying exactly on a surface of the model (a CAD model's flat face at z = 0, say) reads points on it as in
            # two cells. A real overlap fills a volume, so it is still there a little to either side of the slice.
            axis = {"xy": 2, "xz": 1, "yz": 0}[v["basis"]]
            step = max(1e-4 * max(v["width"]), 1e-3)
            moved = [hits_in({**v, "origin": [o + (s if i == axis else 0.0) for i, o in enumerate(v["origin"])]}) for s in (step, -step)]
            if any(moved):
                hits += n
            else:
                on_surfaces.append(f"{v['basis']} at {'xyz'[axis]} = {v['origin'][axis]:g} cm")
        if hits:
            add("error", "overlaps", f"{hits} pixels of {len(views)} slices fall in two cells at once", "find them with "
                "OpenMCGeometryPlotTool (drawn red) and fix the regions in build.py")
        if on_surfaces:
            add("info", "overlaps", f"the slice{'s' if len(on_surfaces) > 1 else ''} {'; '.join(on_surfaces)} lie{'' if len(on_surfaces) > 1 else 's'} "
                "on a surface of the model: points exactly on it read as in two cells, but no overlap is there a little to either "
                "side, so the cells do not overlap", None)
    except Exception as e:
        add("warning", "overlaps", f"the overlap check could not run: {type(e).__name__}: {str(e)[:200]}", None)
    # tallies
    ll, ur = _finite_box(model)
    for t in model.tallies:
        for f in t.filters:
            mesh = getattr(f, "mesh", None)
            lo, hi = getattr(mesh, "lower_left", None), getattr(mesh, "upper_right", None)
            if lo is not None and hi is not None and len(lo) == 3 and (np.any(np.array(hi) < ll) or np.any(np.array(lo) > ur)):
                add("warning", "tallies", f"tally {t.name or t.id}: its mesh lies outside the geometry", "move the mesh over the region")
    if mode == "fixed source" and model.tallies:
        sources = model.settings.source if isinstance(model.settings.source, list) else [model.settings.source]
        total = sum(float(getattr(s, "strength", 1.0) or 0.0) for s in sources if s is not None)
        if sources and abs(total - 1.0) > 1e-9:            # OpenMC scales fixed-source tallies by the total strength
            add("info", "normalisation", f"the source strengths add up to {total:.6g}, and OpenMC multiplies fixed-source tallies by "
                f"that total: with strengths in particles per second the tallies are per second; divide by {total:.6g} for values "
                f"per source particle", None)
        else:
            add("info", "normalisation", "fixed-source tallies are per source particle; multiply by the source strength (particles "
                "per second) for rates", None)
    report(ok=not any(f["severity"] == "error" for f in findings), findings=findings, summary=info)


def main():
    task, args = sys.argv[1], json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
    try:
        {"build": build, "plot": plot, "check": check}[task](args)
    except Exception as e:
        traceback.print_exc()
        report(ok=False, error=f"{type(e).__name__}: {str(e)[:400]}")


if __name__ == "__main__":
    main()
