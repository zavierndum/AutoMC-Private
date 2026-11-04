"""
The search of this example: the boron concentration (ppm by weight) at which the pin's k-infinity is 1.

A script run (it runs transport once for every guess), so the agent sends it to OpenMCRunStartTool with the case and this script, and
the user approves it; build.py may not run transport. It runs in a copy of the case folder, so it imports build.py from there and takes
the variant's OPTIONS with it. Each guess runs in search-runs/, so the frozen model.xml stays as built. Writes search.json for post.py.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))      # python -I leaves the script's own folder off the path

import openmc                                                          # noqa: E402

import build                                                           # noqa: E402

opts = build.OPTIONS


def pin_at(boron_ppm):
    return build.build(boron_ppm=boron_ppm, enrichment=opts["enrichment"], size=opts["size"])


# bisection on the bracket: robust against the statistical noise of each k; tol is relative, on the concentration
critical, guesses, keffs = openmc.search_for_keff(pin_at, bracket=opts["bracket_ppm"], tol=opts["tolerance"], bracketed_method="bisect",
                                                  print_iterations=True, run_args={"output": False, "cwd": "search-runs"})
with open("search.json", "w", encoding="utf-8") as fh:
    json.dump({"critical_ppm": float(critical), "guesses_ppm": [float(g) for g in guesses],
               "keff": [float(k.nominal_value) for k in keffs], "keff_sigma": [float(k.std_dev) for k in keffs], "options": opts},
              fh, indent=1)
print(f"critical boron concentration: {critical:.0f} ppm by weight ({len(guesses)} runs)")
