"""
Read the tet mesh tally: the mesh's elements, volumes and centroids from the statepoint; the flux and heating in each element; the
elements sorted into the four channels by their centroids; each channel's flux and heating per unit volume against the exact cylinder
tallied over the same channel; and the data written to a VTK file for ParaView or VisIt.

    python post.py RUN_FOLDER

Prints them and one last line `RESULTS {json}` for scripts and tests.
"""
import argparse
import glob
import importlib.util
import json
import math
import os

import numpy as np
import openmc


def statepoint(folder):
    files = sorted(glob.glob(os.path.join(folder, "statepoint.*.h5")), key=lambda p: int(p.split(".")[-2]))
    if not files:
        raise SystemExit(f"no statepoint in {folder}")
    return files[-1]


def write_legacy_vtk(mesh, path, datasets):
    """The mesh and its data as a legacy-format VTK file (binary, big-endian), which ParaView and VisIt read; used when OpenMC's
    own writer cannot import the vtk package."""
    points = np.asarray(mesh.vertices, dtype=float)
    conn = np.asarray(mesh.connectivity)[:, :4]                       # tetrahedra: four vertices each (the rest is padding)
    n = len(conn)
    with open(path, "wb") as fh:
        fh.write(b"# vtk DataFile Version 3.0\nOpenMC unstructured mesh tally\nBINARY\nDATASET UNSTRUCTURED_GRID\n")
        fh.write(f"POINTS {len(points)} double\n".encode() + points.astype(">f8").tobytes() + b"\n")
        cells = np.hstack([np.full((n, 1), 4), conn]).astype(">i4")
        fh.write(f"CELLS {n} {cells.size}\n".encode() + cells.tobytes() + b"\n")
        fh.write(f"CELL_TYPES {n}\n".encode() + np.full(n, 10, dtype=">i4").tobytes() + b"\n")      # 10: a tetrahedron
        fh.write(f"CELL_DATA {n}\n".encode())
        for name, values in datasets.items():
            fh.write(f"SCALARS {name} double 1\nLOOKUP_TABLE default\n".encode() + np.asarray(values, dtype=">f8").tobytes() + b"\n")


def load_build(run_folder):
    here = os.path.join(run_folder, "..", "build.py")
    spec = importlib.util.spec_from_file_location("build", here)
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    return build


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("run_folder")
    a = parser.parse_args()
    build = load_build(a.run_folder)
    with openmc.StatePoint(statepoint(a.run_folder)) as sp:
        k = sp.keff
        t = sp.get_tally(name="tet mesh")
        mesh = t.find_filter(openmc.MeshFilter).mesh
        flux = t.get_values(scores=["flux"]).ravel()
        flux_sd = t.get_values(scores=["flux"], value="std_dev").ravel()
        heat = t.get_values(scores=["heating"]).ravel()
        vols, cents = np.asarray(mesh.volumes), np.asarray(mesh.centroids)
        cyl = []
        for i in range(len(build.CHANNELS)):
            c = sp.get_tally(name=f"cylinder {i}")
            cyl.append({s: (float(c.get_values(scores=[s]).ravel()[0]), float(c.get_values(scores=[s], value="std_dev").ravel()[0]))
                        for s in ("flux", "heating")})
        vtk = os.path.join(a.run_folder, "pin_channels.vtk")
        data = {"flux_per_cm3": flux / vols, "flux_std_dev_per_cm3": flux_sd / vols, "heating_per_cm3": heat / vols}
        try:
            mesh.write_data_to_vtk(vtk, datasets=data, volume_normalization=False)
            writer = "OpenMC's writer"
        except ImportError:                                          # it needs the vtk package; the legacy format needs nothing
            write_legacy_vtk(mesh, vtk, data)
            writer = "the legacy-format writer here (no vtk package)"
        vtk_ok = os.path.getsize(vtk) > 0
    print(f"k-eff {k.nominal_value:.5f} +/- {k.std_dev:.5f}")
    print(f"tet mesh: {len(vols):,} elements, {vols.sum():.3f} cm3; {np.mean(flux > 0):.1%} of them scored")
    cyl_vol = math.pi * build.R_CHANNEL ** 2 * 2 * build.HALF_HEIGHT
    print(f"each channel: an exact cylinder of {cyl_vol:.3f} cm3; flux per cm3 on the tets against the cylinder:")
    worst, rows = 0.0, []
    for i, ch in enumerate(build.CHANNELS):
        inside = np.hypot(cents[:, 0] - ch["x"], cents[:, 1] - ch["y"]) < build.PITCH / 2
        v = vols[inside].sum()
        f_tet, f_sd = flux[inside].sum() / v, math.sqrt((flux_sd[inside] ** 2).sum()) / v
        f_cyl, c_sd = cyl[i]["flux"][0] / cyl_vol, cyl[i]["flux"][1] / cyl_vol
        h_ratio = (heat[inside].sum() / v) / (cyl[i]["heating"][0] / cyl_vol)
        z = (f_tet - f_cyl) / math.hypot(f_sd, c_sd)                # the two share tracks, so this overstates the difference's error
        rel = f_tet / f_cyl - 1
        worst = max(worst, abs(rel))
        rows.append({"channel": ch["name"], "elements": int(inside.sum()), "volume": float(v), "flux_rel_diff": rel,
                     "heating_ratio": h_ratio, "heating": float(heat[inside].sum())})
        print(f"  {i} {ch['name']:20s} {inside.sum():7,d} tets, {v:.3f} cm3 ({v / cyl_vol - 1:+.2%}); flux {f_tet:.5g} against "
              f"{f_cyl:.5g} ({rel:+.2%}, {z:+.1f} sigma); heating ratio {h_ratio:.4f}")
    fuel_heat = sum(r["heating"] for r in rows if r["channel"] == "fuel pin")
    tube_heat = sum(r["heating"] for r in rows if r["channel"].startswith("guide tube"))
    print(f"VTK file: {vtk} ({os.path.getsize(vtk) / 1e6:.1f} MB, by {writer})")
    out = {"keff": float(k.nominal_value), "keff_sigma": float(k.std_dev), "elements": int(len(vols)), "mesh_volume": float(vols.sum()),
           "elements_in_channels": int(sum(r["elements"] for r in rows)), "scored_fraction": float(np.mean(flux > 0)),
           "worst_flux_rel_diff": worst, "worst_heating_ratio_off": max(abs(r["heating_ratio"] - 1) for r in rows),
           "fuel_heating_over_tube": fuel_heat / tube_heat, "vtk_written": bool(vtk_ok)}
    print(f"heating in the two fuel pins against the two guide tubes: {out['fuel_heating_over_tube']:.1f} times")
    print("RESULTS " + json.dumps(out))


if __name__ == "__main__":
    main()
