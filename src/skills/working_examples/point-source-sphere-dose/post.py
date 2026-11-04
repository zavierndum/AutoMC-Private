"""
Read a run of the point-source sphere example and turn its tallies into physical answers.

    python post.py RUN_FOLDER [--strength 1e8]

Prints the answer for a source of the given strength (neutrons per second), and one last line `RESULTS {json}` for scripts and tests.
For the offset layout it also prints the exact track length a void sphere would get (the analytic check, README.md).
"""
import argparse
import glob
import json
import math
import os

import openmc


def statepoint(folder):
    files = sorted(glob.glob(os.path.join(folder, "statepoint.*.h5")), key=lambda p: int(p.split(".")[-2]))
    if not files:
        raise SystemExit(f"no statepoint in {folder}")
    return files[-1]


def chord_track_length(a, d):
    """Expected track length (cm per source particle) in a void sphere of radius a whose centre is d > a from an isotropic point
    source: the chord through the sphere averaged over all directions, in closed form."""
    c2 = d * d - a * a
    return a / 2.0 - c2 / (2.0 * d) * math.log((d + a) / math.sqrt(c2))


def target_shape(model):
    """(radius, distance of its centre from the source) of the target sphere, read back from the model."""
    cell = next(c for c in model.geometry.get_all_cells().values() if c.name == "target sphere")
    sphere = next(iter(cell.region.get_surfaces().values()))
    src = model.settings.source[0].space.xyz
    d = math.dist((sphere.x0, sphere.y0, sphere.z0), tuple(src))
    return sphere.r, d, cell.fill is None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("run_folder")
    parser.add_argument("--strength", type=float, default=1.0e8, help="source strength in neutrons per second (default 1e8)")
    a = parser.parse_args()
    model = openmc.Model.from_model_xml(os.path.join(a.run_folder, "model.xml"))
    radius, distance, void = target_shape(model)
    volume = 4.0 / 3.0 * math.pi * radius ** 3

    with openmc.StatePoint(statepoint(a.run_folder)) as sp:
        tl = sp.get_tally(name="track length")
        dose = sp.get_tally(name="effective dose")
        tl_mean, tl_sd = float(tl.mean.ravel()[0]), float(tl.std_dev.ravel()[0])
        dose_mean, dose_sd = float(dose.mean.ravel()[0]), float(dose.std_dev.ravel()[0])

    # per source neutron -> per second: multiply by the strength. A volume-averaged quantity: divide by the volume.
    flux = tl_mean / volume * a.strength                       # n / cm2 / s
    flux_sd = tl_sd / volume * a.strength
    dose_rate = dose_mean / volume * a.strength * 3600.0 * 1.0e-6     # pSv/s -> uSv/h
    dose_rate_sd = dose_sd / volume * a.strength * 3600.0 * 1.0e-6
    out = {"track_length": tl_mean, "track_length_sigma": tl_sd, "flux": flux, "flux_sigma": flux_sd,
           "dose_rate_uSv_per_h": dose_rate, "dose_rate_uSv_per_h_sigma": dose_rate_sd, "radius_cm": radius, "distance_cm": distance,
           "strength_per_s": a.strength}
    print(f"target sphere: radius {radius:g} cm, centre {distance:g} cm from the source, {'void' if void else 'water'}")
    print(f"track length per source neutron: {tl_mean:.5e} +/- {tl_sd:.2e} cm")
    print(f"average flux at {a.strength:.3g} n/s: {flux:.4e} +/- {flux_sd:.2e} n/cm2/s")
    print(f"effective dose rate (ICRP-116, AP): {dose_rate:.4e} +/- {dose_rate_sd:.2e} uSv/h")
    if distance > radius:
        exact = chord_track_length(radius, distance)
        out["track_length_void_exact"] = exact
        print(f"a void sphere here would get exactly {exact:.5e} cm per source neutron"
              + (f" (this run: {(tl_mean - exact) / tl_sd:+.2f} standard deviations from it)" if void and tl_sd > 0 else ""))
    print("RESULTS " + json.dumps(out))


if __name__ == "__main__":
    main()
