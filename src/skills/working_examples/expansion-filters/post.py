"""
Rebuild the flux shape from expansion tallies and compare it with the binned tally of the same run:

- column: phi(z) from the Legendre moments (openmc.legendre_from_expcoef, divided by the column's cross-section), against the z mesh;
- pellet: phi(r) from the radial Zernike moments (openmc.ZernikeRadial, divided by the height), against the ring mesh; and the full
  Zernike expansion evaluated along a diameter.

The zeroth moment of an expansion is the flux integrated over the region, so it equals the sum of the binned tally exactly (the same
tracks are scored). Prints the comparison and one last line `RESULTS {json}` for scripts and tests.

    python post.py RUN_FOLDER [--plot shape.png]
"""
import argparse
import glob
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


def column(sp, out):
    leg = sp.get_tally(name="axial flux, Legendre")
    f = leg.find_filter(openmc.SpatialLegendreFilter)
    moments = leg.mean.ravel()
    mesh_t = sp.get_tally(name="axial flux, mesh")
    mesh = mesh_t.find_filter(openmc.MeshFilter).mesh
    side = mesh.upper_right[0] - mesh.lower_left[0]
    area = side * (mesh.upper_right[1] - mesh.lower_left[1])
    edges = np.linspace(f.minimum, f.maximum, mesh.dimension[2] + 1)
    dz = np.diff(edges)
    binned = mesh_t.mean.ravel() / (area * dz)                       # flux per cm3 per source neutron in each slice
    series = openmc.legendre_from_expcoef(moments, domain=(f.minimum, f.maximum))
    integral = series.integ()
    rebuilt = (integral(edges[1:]) - integral(edges[:-1])) / dz / area   # the expansion averaged over each slice
    zc = 0.5 * (edges[1:] + edges[:-1])
    at = lambda z: binned[np.argmin(np.abs(zc - z))]
    centre, beside = at(0.0), 0.5 * (at(1.5) + at(-1.5))
    quarter = 0.5 * (at(5.0) + at(-5.0))
    out.update(order=int(f.order), zeroth_over_sum=float(moments[0] / mesh_t.mean.sum()),
               rms_rel_diff=float(np.sqrt(np.mean(((rebuilt - binned) / binned) ** 2))),
               dip_at_absorber=bool(centre < beside), absorber_over_beside=float(centre / beside),
               centre_over_quarter=float(centre / quarter))
    print(f"Legendre order {f.order} along z: zeroth moment / sum of the {len(binned)} slices = {out['zeroth_over_sum']:.9f}")
    print(f"expansion against the slices: RMS relative difference {out['rms_rel_diff']:.3f}")
    print(f"flux in the absorber / in the fuel 1.5 cm away: {out['absorber_over_beside']:.3f}; / 5 cm away: "
          f"{out['centre_over_quarter']:.3f}")
    return zc, binned, rebuilt, "z (cm)"


def pellet(sp, out):
    rad = sp.get_tally(name="radial flux, Zernike")
    f = rad.find_filter(openmc.ZernikeRadialFilter)
    moments = rad.mean.ravel()
    disc = sp.get_tally(name="flux over the disc, Zernike")
    mesh_t = sp.get_tally(name="radial flux, mesh")
    mesh = mesh_t.find_filter(openmc.MeshFilter).mesh
    r_edges, z_edges = np.asarray(mesh.r_grid), np.asarray(mesh.z_grid)
    height = z_edges[-1] - z_edges[0]
    binned = mesh_t.mean.ravel() / (math.pi * np.diff(r_edges ** 2) * height)
    rc = 0.5 * (r_edges[1:] + r_edges[:-1])
    radial = openmc.ZernikeRadial(moments, radius=f.r)                  # per unit area, integrated over the height
    rebuilt = np.array(radial(list(rc))) / height
    full = openmc.Zernike(disc.mean.ravel(), radius=f.r)
    along = np.array([float(np.ravel(full(float(r), 0.0))[0]) for r in rc]) / height   # the full expansion along the +x radius
    out.update(order=int(f.order), zeroth_over_sum=float(moments[0] / mesh_t.mean.sum()),
               rms_rel_diff=float(np.sqrt(np.mean(((rebuilt - binned) / binned) ** 2))),
               full_vs_radial_rms=float(np.sqrt(np.mean(((along - rebuilt) / rebuilt) ** 2))),
               centre_over_edge=float(binned[0] / binned[-1]))
    print(f"radial Zernike order {f.order}: zeroth moment / sum of the {len(binned)} rings = {out['zeroth_over_sum']:.9f}")
    print(f"expansion against the rings: RMS relative difference {out['rms_rel_diff']:.3f}")
    print(f"full Zernike along a radius against the radial one: RMS relative difference {out['full_vs_radial_rms']:.3f}")
    print(f"flux at the centre / at the edge: {out['centre_over_edge']:.3f} (a bare pellet: neutrons leak from its surface)")
    return rc, binned, rebuilt, "r (cm)"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("run_folder")
    parser.add_argument("--plot")
    a = parser.parse_args()
    out = {}
    with openmc.StatePoint(statepoint(a.run_folder)) as sp:
        out.update(keff=float(sp.keff.nominal_value), keff_sigma=float(sp.keff.std_dev))
        names = {t.name for t in sp.tallies.values()}
        out["layout"] = "column" if "axial flux, Legendre" in names else "pellet"
        x, binned, rebuilt, label = (column if out["layout"] == "column" else pellet)(sp, out)
    print(f"k-eff = {out['keff']:.5f} +/- {out['keff_sigma']:.5f}")
    if a.plot:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(7, 4))
        ax.step(x, binned, where="mid", label="binned tally")
        ax.plot(x, rebuilt, label=f"expansion, order {out['order']}")
        ax.set_xlabel(label)
        ax.set_ylabel("flux per cm3 per source neutron")
        ax.legend()
        fig.tight_layout()
        fig.savefig(a.plot, dpi=120)
        print(f"plot written to {a.plot}")
    print("RESULTS " + json.dumps(out))


if __name__ == "__main__":
    main()
