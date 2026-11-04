"""
Read the detector's pulse-height spectrum: the full-energy peaks at 662 and 800 keV, the Compton edge of the 662 keV line, how many
photons deposit anything, and the spectrum the detector would show once its resolution is applied (a Gaussian whose width grows with
energy; the notebook's resolution model, applied here by convolution rather than by sampling).

    python post.py RUN_FOLDER [--plot spectrum.png]

Prints them and one last line `RESULTS {json}` for scripts and tests.
"""
import argparse
import glob
import json
import os

import numpy as np
import openmc

ELECTRON_EV = 510_998.95


def statepoint(folder):
    files = sorted(glob.glob(os.path.join(folder, "statepoint.*.h5")), key=lambda p: int(p.split(".")[-2]))
    if not files:
        raise SystemExit(f"no statepoint in {folder}")
    return files[-1]


def compton_edge(e):
    """The largest energy a photon of energy e gives an electron in one scattering (straight back)."""
    return e * 2 * e / (ELECTRON_EV + 2 * e)


def fwhm(e, a=1000.0, b=4.0, c=0.0002):
    """Detector resolution, eV: full width at half maximum a + b sqrt(E + c E^2), the notebook's model of a NaI detector."""
    return a + b * np.sqrt(e + c * e ** 2)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("run_folder")
    parser.add_argument("--plot")
    a = parser.parse_args()
    with openmc.StatePoint(statepoint(a.run_folder)) as sp:
        t = sp.get_tally(name="pulse height")
        edges = t.find_filter(openmc.EnergyFilter).values
        per_second = t.get_values(scores=["pulse-height"]).ravel()       # pulses per second in each 1 keV bin (see below)
    # with source strengths given, OpenMC scales fixed-source tallies by the total strength (photons per second): divide it out
    strength = sum(s.strength for s in openmc.Model.from_model_xml(os.path.join(a.run_folder, "model.xml")).settings.source)
    counts = per_second / strength                                       # pulses per source photon
    centres = 0.5 * (edges[1:] + edges[:-1])
    at = lambda e: counts[int(np.searchsorted(edges, e) - 1)]
    band = lambda lo, hi: counts[(centres >= lo) & (centres < hi)].mean()
    peak662 = at(661_657.0) / band(600e3, 640e3)
    peak800 = at(800_000.0) / band(700e3, 780e3)
    edge = compton_edge(661_657.0)
    below, above = band(edge - 40e3, edge - 10e3), band(edge + 15e3, edge + 45e3)
    sigma = fwhm(centres) / (2 * np.sqrt(2 * np.log(2)))
    # broaden: each bin's pulses spread over a Gaussian of its own width (weights normalized per source bin)
    kernel = np.exp(-0.5 * ((centres[None, :] - centres[:, None]) / sigma[:, None]) ** 2)
    kernel /= kernel.sum(axis=1, keepdims=True)
    blurred = counts[1:] @ kernel[1:]                                    # the zero-energy bin (no deposit) is not a pulse
    resolution_662 = fwhm(661_657.0) / 661_657.0
    out = {"detected_fraction": float(counts[1:].sum()), "pulses_per_second": float(per_second[1:].sum()),
           "peak_662_over_continuum": float(peak662), "peak_800_over_continuum": float(peak800), "compton_edge_keV": edge / 1e3, "below_edge_over_above": float(below / above),
           "resolution_at_662": float(resolution_662), "blurred_total_over_sharp": float(blurred.sum() / counts[1:].sum())}
    print(f"photons that deposit energy in the crystal: {out['detected_fraction']:.4f} per source photon "
          f"({out['pulses_per_second']:.3e} pulses per second from {strength:.3e} photons per second)")
    print(f"full-energy peaks over the continuum just below them: 662 keV x{peak662:.1f}, 800 keV x{peak800:.1f}")
    print(f"Compton edge of the 662 keV line at {edge / 1e3:.1f} keV: just below it {below / above:.1f} times the counts just above")
    print(f"resolution at 662 keV: {100 * resolution_662:.1f}% FWHM (a NaI detector: about 7%)")
    if a.plot:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(8, 4.5))
        ax.semilogy(centres[1:] / 1e3, counts[1:], lw=0.6, label="deposited (pulse height)")
        ax.semilogy(centres / 1e3, blurred, label="with the detector's resolution")
        for e in (661.657, 800.0, edge / 1e3):
            ax.axvline(e, ls=":", color="grey")
        ax.set_xlabel("energy (keV)")
        ax.set_ylabel("pulses per source photon per keV")
        ax.legend()
        fig.tight_layout()
        fig.savefig(a.plot, dpi=120)
        print(f"plot written to {a.plot}")
    print("RESULTS " + json.dumps(out))


if __name__ == "__main__":
    main()
