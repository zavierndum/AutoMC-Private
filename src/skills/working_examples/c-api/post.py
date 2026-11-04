"""
Compare the pin's two states (comparison.json, written by run.py): k and the reactivity change between them; the heat by zone,
normalised to an average of 1; the height of the heat's centre (its centroid) and how far the feedback moved it; and whether OpenMC
reports the temperatures and densities that were set.

    python post.py RUN_FOLDER

Prints them and one last line `RESULTS {json}` for scripts and tests.
"""
import argparse
import json
import math
import os


def centroid(heat, sigma, zones):
    """The heat-weighted mean height and its standard deviation (zones treated as independent)."""
    mids = [(b + t) / 2 for b, t in zones]
    total = sum(heat)
    z = sum(h * m for h, m in zip(heat, mids)) / total
    var = sum((s * (m - z) / total) ** 2 for s, m in zip(sigma, mids))
    return z, math.sqrt(var)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("run_folder")
    a = parser.parse_args()
    with open(os.path.join(a.run_folder, "comparison.json"), encoding="utf-8") as fh:
        c = json.load(fh)
    n, f, zones = c["nominal"], c["feedback"], c["zones"]
    k1, s1, k2, s2 = n["keff"], n["keff_sigma"], f["keff"], f["keff_sigma"]
    drho = (k2 - k1) / (k1 * k2)
    drho_sigma = math.hypot(s1, s2) / (k1 * k2)
    print(f"k as built {k1:.5f} +/- {s1:.5f}; after the feedback {k2:.5f} +/- {s2:.5f}")
    print(f"reactivity change (k2 - k1) / (k1 k2): {drho * 1e5:+.0f} +/- {drho_sigma * 1e5:.0f} pcm "
          f"({drho / drho_sigma:+.1f} standard deviations)")
    print("heat by zone, bottom to top, each state normalised to an average of 1:")
    for name, s in (("as built", n), ("feedback", f)):
        mean = sum(s["heat"]) / len(s["heat"])
        print(f"  {name:9s} " + " ".join(f"{h / mean:5.3f}" for h in s["heat"]))
    z1, dz1 = centroid(n["heat"], n["heat_sigma"], zones)
    z2, dz2 = centroid(f["heat"], f["heat_sigma"], zones)
    shift, shift_sigma = z2 - z1, math.hypot(dz1, dz2)
    print(f"heat centroid: {z1:.1f} +/- {dz1:.1f} cm as built, {z2:.1f} +/- {dz2:.1f} cm after; moved {shift:+.1f} cm "
          f"({shift / shift_sigma:+.1f} standard deviations)")
    h = n["heat"]
    end_over_middle = (h[0] + h[-1]) / (h[len(h) // 2 - 1] + h[len(h) // 2])
    symmetry = h[0] / h[-1] - 1
    asked = c["feedback_asked"]
    worst = max([abs(t - asked["fuel_K"]) / asked["fuel_K"] for t in c["readback"]["fuel_K"]] +
                [abs(r - w) / w for r, w in zip(c["readback"]["water_g_cm3"], asked["water_g_cm3"])])
    print(f"as built: end zones / middle zones {end_over_middle:.3f}; bottom / top {symmetry + 1:.3f}")
    print(f"temperatures and densities read back from OpenMC: worst relative difference {worst:.1e}")
    out = {"keff_nominal": k1, "keff_nominal_sigma": s1, "keff_feedback": k2, "keff_feedback_sigma": s2,
           "drho_pcm": drho * 1e5, "drho_sigma": drho / drho_sigma, "centroid_nominal_cm": z1, "centroid_feedback_cm": z2,
           "centroid_shift_cm": shift, "centroid_shift_sigma": shift / shift_sigma, "end_over_middle": end_over_middle,
           "bottom_over_top_minus_1": symmetry, "readback_worst": worst}
    print("RESULTS " + json.dumps(out))


if __name__ == "__main__":
    main()
