"""
The uncertainties of Gd-157's resolved resonances (ENDF File 32) from Python: read the covariance matrix of the resonance parameters,
make it whole (the file stores only its upper triangle), turn it into relative uncertainties and correlations, draw samples of the
parameters from it, and take the subset of one spin. Checks: the matrix is a valid covariance (symmetric, no meaningfully negative
eigenvalue); the samples reproduce the evaluated values and uncertainties within their statistics; the subset's matrix is exactly the
matching block of the whole one.

Derived from: the OpenMC notebook "Nuclear Data: Resonance Covariance" (openmc-notebooks, tag v0.15.0; the nuclide, the covariance
object, sampling and the subset by spin). Its ENDF/B-VII.1 file came from a server that is gone; this uses ENDF/B-VIII.0 from the
data cache. Written for AutoMC; README.md explains the quantities.

    python analysis.py                       run with PythonRunTool (the user approves); writes nothing it keeps

Needs the data cache: `python -m automc data fetch resonance-covariance`.
Prints what it finds and one last line `RESULTS {json}` for scripts and tests. Nothing here runs transport.
"""
import hashlib
import json
import os
import warnings
from pathlib import Path

import numpy as np
import openmc.data

# The choices of this example: edit them here.
OPTIONS = {"samples": 2000, "seed": 20261119, "spin": 2.0}

# The file this needs from the data cache (src/skills/working_examples/data.json), with its SHA-256
DATA = {"endf/n-064_Gd_157.endf": "3a365e591276d0304b0955b5ee07f65e335c80e73878174684b0927778c3f2bb"}
PARAMETERS = ["energy", "neutronWidth", "captureWidth"]        # the order of each resonance's three entries in the matrix


def data_file(rel):
    """A file of the data cache (AUTOMC_DATA_CACHE, else ~/.cache/automc/data), checked against its SHA-256."""
    path = Path(os.environ.get("AUTOMC_DATA_CACHE") or Path.home() / ".cache" / "automc" / "data").expanduser() / rel
    if not path.is_file():
        raise SystemExit(f"{rel} is not in the data cache ({path.parent}); fetch it with "
                         "`python -m automc data fetch resonance-covariance`")
    if hashlib.sha256(path.read_bytes()).hexdigest() != DATA[rel]:
        raise SystemExit(f"{path} is not the file this example was checked with; fetch it again with `python -m automc data fetch "
                         f"{rel}`")
    return path


def whole(upper):
    """The full symmetric matrix from its upper triangle."""
    return upper + upper.T - np.diag(np.diag(upper))


def main():
    gd = openmc.data.IncidentNeutron.from_endf(data_file("endf/n-064_Gd_157.endf"), covariance=True)
    rng = gd.resonance_covariance.ranges[0]
    params = rng.parameters
    n = len(params)
    stored = np.array(rng.covariance)
    cov = whole(stored)
    values = params[PARAMETERS].to_numpy().ravel()                 # energy, neutron width, capture width, resonance by resonance
    sigma = np.sqrt(np.diag(cov))
    print(f"Gd-157, {type(rng).__name__}: {n} resolved resonances from {rng.energy_min:g} to {rng.energy_max:g} eV, "
          f"{rng.mpar} parameters each; covariance {cov.shape[0]} x {cov.shape[1]}")
    print(f"  stored as its upper triangle: {bool(np.allclose(np.tril(stored, -1), 0))}")
    eig = np.linalg.eigvalsh(cov)
    neg = float(-eig.min() / eig.max()) if eig.min() < 0 else 0.0
    print(f"  eigenvalues from {eig.min():.3e} to {eig.max():.3e}: the most negative is {neg:.1e} of the largest")
    rel = sigma / np.abs(values)
    print("  relative uncertainties of the first resonances (energy, neutron width, capture width):")
    for i in range(5):
        e, nw, cw = rel[3 * i:3 * i + 3]
        print(f"    {params['energy'][i]:9.4f} eV  J = {params['J'][i]:.0f}:  {e:7.2%}  {nw:7.2%}  {cw:7.2%}")
    corr = cov / np.outer(sigma, sigma)
    off = corr[~np.eye(len(corr), dtype=bool)]
    print(f"  correlations: diagonal 1, off-diagonal from {off.min():+.3f} to {off.max():+.3f}; "
          f"{np.mean(np.abs(off) > 0.1):.1%} of them beyond 0.1 in size")

    np.random.seed(OPTIONS["seed"])                                  # the sampler draws from NumPy's global generator
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)                 # its warning: a sampled width may come out negative
        samples = rng.sample(OPTIONS["samples"])
    drawn = np.array([s.parameters[PARAMETERS].to_numpy().ravel() for s in samples])
    m = len(drawn)
    z_mean = (drawn.mean(axis=0) - values) / (sigma / np.sqrt(m))
    sd_ratio = drawn.std(axis=0, ddof=1) / sigma
    widths = drawn.reshape(m, n, 3)[:, :, 1:]
    negative = float(np.mean(widths < 0))
    print(f"\n{m} samples (seed {OPTIONS['seed']}):")
    print(f"  sample means against the evaluated values: worst {np.abs(z_mean).max():.2f} standard errors off")
    print(f"  sample standard deviations against the evaluated ones: ratio from {sd_ratio.min():.3f} to {sd_ratio.max():.3f}")
    print(f"  sampled widths below zero: {negative:.2%} (the sampler does not keep widths positive)")

    sub = rng.subset("J", [OPTIONS["spin"], OPTIONS["spin"]])
    keep = np.flatnonzero(params["J"].to_numpy() == OPTIONS["spin"])
    idx = np.ravel([[3 * i, 3 * i + 1, 3 * i + 2] for i in keep])
    block_diff = float(np.abs(np.array(sub.covariance) - stored[np.ix_(idx, idx)]).max())
    print(f"\nsubset with J = {OPTIONS['spin']:g}: {len(sub.file2res.parameters)} of {n} resonances, covariance "
          f"{np.array(sub.covariance).shape}; against the matching block of the whole matrix: largest difference {block_diff:.1e}")
    out = {"resonances": n, "covariance_size": int(cov.shape[0]), "stored_upper": bool(np.allclose(np.tril(stored, -1), 0)),
           "negative_eigen_rel": neg, "rel_uncertainty_first_energy": float(rel[0]), "max_abs_correlation": float(np.abs(off).max()),
           "samples": m, "worst_mean_z": float(np.abs(z_mean).max()), "sd_ratio_min": float(sd_ratio.min()),
           "sd_ratio_max": float(sd_ratio.max()), "negative_width_fraction": negative, "subset_resonances": int(len(keep)),
           "subset_block_diff": block_diff}
    print("RESULTS " + json.dumps(out))


if __name__ == "__main__":
    main()
