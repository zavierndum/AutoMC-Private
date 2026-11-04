"""
Nuclear data from Python with openmc.data: a nuclide of the HDF5 library OpenMC runs with, the same nuclide's ENDF evaluation, data
made from an ENDF file with NJOY, and windowed multipole data. Each part ends with a check that the data agree with themselves or
with each other:

1. the library's Gd-157: its temperatures and reactions; below every threshold, the total equals elastic plus capture; the
   unresolved-resonance tables cover the range the ENDF file gives; an export to HDF5 reads back unchanged;
2. the ENDF/B-VIII.0 file of Gd-157: its resonance ranges and the first resolved resonances;
3. H-2 processed by NJOY at three temperatures: its elastic cross section at 1 eV against the library's;
4. U-238's 6.67 eV resonance from the multipole file at 0, 294 and 900 K: the peak falls, the area stays.

Derived from: the OpenMC notebook "Nuclear Data" (openmc-notebooks, tag v0.15.0; the nuclides, the reactions looked at, the NJOY
temperatures and the multipole resonance). Its files came from servers that are gone (an ACE file, ENDF/B-VII.1 at LANL); this uses
the ENDF/B-VIII.0 library and files of the data cache. Written for AutoMC; README.md explains each part.

    python analysis.py                       run with PythonRunTool (the user approves); writes nothing it keeps

Needs OPENMC_CROSS_SECTIONS (the HDF5 library), NJOY, and the data cache: `python -m automc data fetch nuclear-data`.
Prints what it finds and one last line `RESULTS {json}` for scripts and tests. Nothing here runs transport.
"""
import hashlib
import json
import os
import tempfile
from pathlib import Path

import numpy as np
import openmc.data

# The choices of this example: edit them here.
OPTIONS = {"njoy_temperatures_K": [300.0, 400.0, 500.0]}

# The files this needs from the data cache (src/skills/working_examples/data.json), with their SHA-256
DATA = {"endf/n-064_Gd_157.endf": "3a365e591276d0304b0955b5ee07f65e335c80e73878174684b0927778c3f2bb",
        "endf/n-001_H_002.endf": "190fd04cebe2756a81302a176e83871be9d0f9f2a1d4e4134c828e8d9554bfca",
        "wmp/092238.h5": "f68cf33be50c915263795e9bc8f1b485c58d47d75d1d41d0c593dfa8a7c07a9a"}


def data_file(rel):
    """A file of the data cache (AUTOMC_DATA_CACHE, else ~/.cache/automc/data), checked against its SHA-256."""
    path = Path(os.environ.get("AUTOMC_DATA_CACHE") or Path.home() / ".cache" / "automc" / "data").expanduser() / rel
    if not path.is_file():
        raise SystemExit(f"{rel} is not in the data cache ({path.parent}); fetch it with `python -m automc data fetch nuclear-data`")
    if hashlib.sha256(path.read_bytes()).hexdigest() != DATA[rel]:
        raise SystemExit(f"{path} is not the file this example was checked with; fetch it again with `python -m automc data fetch "
                         f"{rel}`")
    return path


def library_file(nuclide):
    """The HDF5 file of `nuclide` in the library OPENMC_CROSS_SECTIONS names."""
    xs = os.environ.get("OPENMC_CROSS_SECTIONS")
    if not xs:
        raise SystemExit("OPENMC_CROSS_SECTIONS is not set: it names the HDF5 library (SETUP.md section 3)")
    lib = openmc.data.DataLibrary.from_xml(xs)
    entry = lib.get_by_material(nuclide)
    if entry is None:
        raise SystemExit(f"{nuclide} is not in {xs}")
    return entry["path"]


def main():
    out = {}
    # physical data that ship with OpenMC
    out.update(fe54_mass_u=openmc.data.atomic_mass("Fe54"), h2_abundance=openmc.data.NATURAL_ABUNDANCE["H2"],
               carbon_weight_u=openmc.data.atomic_weight("C"))
    print(f"Fe-54 mass {out['fe54_mass_u']:.6f} u; H-2 natural abundance {out['h2_abundance']:.6g}; carbon's atomic weight "
          f"{out['carbon_weight_u']:.5f}")

    # 1. the library's Gd-157
    gd = openmc.data.IncidentNeutron.from_hdf5(library_file("Gd157"))
    T = "294K"
    temps = sorted(float(t[:-1]) for t in gd.temperatures)
    print(f"\nGd-157 in the library: {len(gd.reactions)} reactions, temperatures {', '.join(f'{t:g}' for t in temps)} K")
    capture = float(gd[102].xs[T](0.0253))
    threshold = float(gd[16].xs[T].x[0])
    print(f"  capture at 0.0253 eV, {T}: {capture:,.0f} b; (n,2n) opens at {threshold / 1e6:.3f} MeV and emits "
          + ", ".join(p.particle for p in gd[16].products))
    worst = 0.0
    for e in (0.0253, 1.0, 10.0):                                # only elastic and capture are open this low
        total, parts = float(gd[1].xs[T](e)), float(gd[2].xs[T](e) + gd[102].xs[T](e))
        worst = max(worst, abs(total - parts) / total)
    print(f"  total against elastic + capture at 0.0253, 1 and 10 eV: worst relative difference {worst:.1e}")
    urr = gd.urr[T]
    print(f"  unresolved-resonance probability tables: {urr.table.shape[0]} energies from {urr.energy[0]:.1f} to "
          f"{urr.energy[-1]:,.1f} eV, {urr.table.shape[2]} bands")
    with tempfile.TemporaryDirectory() as tmp:
        gd.export_to_hdf5(Path(tmp) / "Gd157.h5", "w")
        back = openmc.data.IncidentNeutron.from_hdf5(Path(tmp) / "Gd157.h5")
        same = all(np.array_equal(back[mt].xs[T].y, gd[mt].xs[T].y) for mt in (2, 16, 102))
    print(f"  exported to HDF5 and read back: {'identical' if same else 'DIFFERENT'}")
    out.update(gd157_temperatures=len(temps), gd157_capture_thermal_b=capture, n2n_threshold_MeV=threshold / 1e6,
               total_minus_partials_rel=worst, hdf5_roundtrip_equal=same)

    # 2. the ENDF/B-VIII.0 evaluation of Gd-157
    endf = openmc.data.IncidentNeutron.from_endf(data_file("endf/n-064_Gd_157.endf"))
    ranges = [(type(r).__name__, r.energy_min, r.energy_max) for r in endf.resonances.ranges]
    params = endf.resonances.resolved.parameters
    print(f"\nGd-157 from its ENDF file: resonance ranges " + "; ".join(f"{n} {lo:g} to {hi:,.1f} eV" for n, lo, hi in ranges))
    print(f"  {len(params)} resolved resonances; the first five:")
    for _, r in params.head(5).iterrows():
        print(f"    {r['energy']:9.4f} eV  J = {r['J']:.0f}  neutron width {r['neutronWidth']:.3e} eV  capture width "
              f"{r['captureWidth']:.4f} eV")
    unresolved = next(hi for n, lo, hi in ranges if n == "Unresolved")
    urr_rel = max(abs(urr.energy[0] - ranges[0][2]) / ranges[0][2], abs(urr.energy[-1] - unresolved) / unresolved)
    print(f"  the library's probability tables span the ENDF file's unresolved range to {urr_rel:.1e}")
    out.update(resolved_resonances=len(params), resolved_top_eV=ranges[0][2], urr_range_rel_diff=urr_rel)

    # 3. H-2 made with NJOY from its ENDF file, against the library
    temps_njoy = OPTIONS["njoy_temperatures_K"]
    h2 = openmc.data.IncidentNeutron.from_njoy(str(data_file("endf/n-001_H_002.endf")), temperatures=temps_njoy)
    lib_h2 = openmc.data.IncidentNeutron.from_hdf5(library_file("H2"))
    made, ref = float(h2[2].xs[f"{temps_njoy[0]:g}K"](1.0)), float(lib_h2[2].xs["294K"](1.0))
    print(f"\nH-2 processed by NJOY: temperatures {', '.join(h2[2].xs)}; elastic at 1 eV {made:.5f} b at {temps_njoy[0]:g} K, "
          f"the library's {ref:.5f} b at 294 K ({(made / ref - 1) * 100:+.3f}%)")
    out.update(njoy_temperatures=list(h2[2].xs), njoy_vs_library_rel=abs(made / ref - 1))

    # 4. U-238's first big resonance from windowed multipole data, at three temperatures
    wmp = openmc.data.WindowedMultipole.from_hdf5(data_file("wmp/092238.h5"))
    e = np.linspace(4.0, 10.0, 60001)
    peaks, areas = {}, {}
    print("\nU-238 capture near 6.67 eV from the multipole file:")
    for t in (0.0, 294.0, 900.0):
        c = wmp(e, t)[1]
        peaks[t], areas[t] = float(c.max()), float(np.trapezoid(c, e))
        print(f"  {t:5.0f} K: peak {peaks[t]:8,.0f} b at {e[c.argmax()]:.4f} eV; area from 4 to 10 eV {areas[t]:.3f} b eV")
    lib_u = openmc.data.IncidentNeutron.from_hdf5(library_file("U238"))
    lib_peak = float(lib_u[102].xs["294K"](e).max())
    print(f"  the HDF5 library's 294 K peak: {lib_peak:,.0f} b ({(lib_peak / peaks[294.0] - 1) * 100:+.1f}% from the multipole file's)")
    out.update(doppler_peak_ratio_900_0=peaks[900.0] / peaks[0.0], doppler_area_rel=abs(areas[900.0] / areas[0.0] - 1),
               multipole_vs_library_peak_rel=lib_peak / peaks[294.0] - 1)
    print("RESULTS " + json.dumps(out))


if __name__ == "__main__":
    main()
