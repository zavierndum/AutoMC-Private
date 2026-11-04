"""
Read the manifold's mesh tally: the elements' volumes and centroids from the statepoint, the flux per cm3 in each element and energy
group, and two checks of the physics, both as rank correlations over the elements that scored: the flux falls with distance from the
source, and the share below 1 MeV rises with it (neutrons slow down in the steel). Writes the results for viewing: a VTK file
(manifold_flux.vtk) and a point cloud of the element centroids coloured by flux (manifold_point_cloud.png).

    python post.py RUN_FOLDER

Prints them and one last line `RESULTS {json}` for scripts and tests.
"""
import argparse
import glob
import importlib.util
import json
import os

import numpy as np
import openmc


def statepoint(folder):
    files = sorted(glob.glob(os.path.join(folder, "statepoint.*.h5")), key=lambda p: int(p.split(".")[-2]))
    if not files:
        raise SystemExit(f"no statepoint in {folder}")
    return files[-1]


def load_build(run_folder):
    spec = importlib.util.spec_from_file_location("build", os.path.join(run_folder, "..", "build.py"))
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    return build


def rank_correlation(a, b):
    """Spearman's rank correlation (ties are rare here and broken by order)."""
    ra, rb = np.argsort(np.argsort(a)), np.argsort(np.argsort(b))
    return float(np.corrcoef(ra, rb)[0, 1])


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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("run_folder")
    a = parser.parse_args()
    build = load_build(a.run_folder)
    with openmc.StatePoint(statepoint(a.run_folder)) as sp:
        t = sp.get_tally(name="flux on the mesh")
        mesh = t.find_filter(openmc.MeshFilter).mesh
        vols, cents = np.asarray(mesh.volumes), np.asarray(mesh.centroids)
        flux = t.mean.reshape(len(vols), len(build.GROUPS) - 1) / vols[:, None]       # per cm3; energy groups vary fastest
        leak = next(float(r["mean"]) for r in sp.global_tallies
                    if (r["name"].decode() if isinstance(r["name"], bytes) else str(r["name"])) == "leakage")
        slow, fast = flux[:, 0], flux[:, 1]
        total = slow + fast
        vtk = os.path.join(a.run_folder, "manifold_flux.vtk")
        data = {"flux_below_1MeV_per_cm3": slow, "flux_above_1MeV_per_cm3": fast}
        try:
            mesh.write_data_to_vtk(vtk, datasets=data, volume_normalization=False)
            writer = "OpenMC's writer"
        except ImportError:                                          # it needs the vtk package; the legacy format needs nothing
            write_legacy_vtk(mesh, vtk, data)
            writer = "the legacy-format writer here (no vtk package)"
    scored = total > 0
    dist = np.linalg.norm(cents - np.asarray(build.SOURCE_XYZ), axis=1)
    falls = rank_correlation(dist[fast > 0], fast[fast > 0])
    share = slow[scored] / total[scored]
    softens = rank_correlation(dist[scored], share)
    print(f"mesh: {len(vols):,} elements, {vols.sum() / 1e6:.3f} m3; {scored.mean():.1%} scored; leakage {leak:.4f}")
    print(f"flux above 1 MeV against distance from the source: rank correlation {falls:+.3f}")
    print(f"share of the flux below 1 MeV against distance: rank correlation {softens:+.3f}")
    for lo, hi in ((0, 25), (25, 50), (50, 100), (100, 250)):
        near = scored & (dist >= lo) & (dist < hi)
        if near.any():
            print(f"  {lo:3d} to {hi:3d} cm: {near.sum():6,d} elements, mean flux {total[near].mean():.3e} /cm2 per source neutron, "
                  f"{slow[near].sum() / total[near].sum():.1%} below 1 MeV")

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig = plt.figure(figsize=(9, 7))
    ax = fig.add_subplot(projection="3d")
    p = ax.scatter(*cents[scored].T, c=np.log10(total[scored]), s=1, cmap="viridis")
    fig.colorbar(p, ax=ax, label="log10 flux per cm2 per source neutron", shrink=0.7)
    ax.scatter(*build.SOURCE_XYZ, c="red", s=40, marker="*")
    ax.set_xlabel("x (cm)")
    ax.set_ylabel("y (cm)")
    ax.set_zlabel("z (cm)")
    ax.set_title("Manifold: flux at the element centroids (the source in red)")
    png = os.path.join(a.run_folder, "manifold_point_cloud.png")
    fig.savefig(png, dpi=120, bbox_inches="tight")
    plt.close(fig)
    print(f"VTK file: {vtk} ({os.path.getsize(vtk) / 1e6:.1f} MB, by {writer}); point cloud: {png}")
    out = {"elements": int(len(vols)), "scored_fraction": float(scored.mean()), "leakage": leak, "fast_falls_with_distance": falls,
           "slow_share_rises_with_distance": softens, "vtk_written": os.path.getsize(vtk) > 0, "picture_written": os.path.getsize(png) > 0}
    print("RESULTS " + json.dumps(out))


if __name__ == "__main__":
    main()
