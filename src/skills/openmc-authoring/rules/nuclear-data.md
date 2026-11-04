# Nuclear data

Derived from: the OpenMC 0.15.3 user's guide, data page, and the nuclear data format pages (MIT). Written for AutoMC; classes and
functions checked in the installed package (2026-11-04).

## The library a model uses

1. **The continuous-energy library** is found through `openmc.config["cross_sections"]` (the variable OPENMC_CROSS_SECTIONS), or a
   model's `openmc.Materials.cross_sections`. Here it is ENDF/B-VIII.0 in OpenMC's HDF5 format; every result should say so.
2. **Names follow GNDS:** `U235`, `Pu239`, `Am242_m1` (a metastable state); elements by symbol; thermal tables `c_...`
   (`rules/materials.md`). The model check names any nuclide or table the library lacks.
3. **What is in the library:** `openmc.data.DataLibrary.from_xml(path)` lists every entry; nuclide masses are
   `openmc.data.atomic_mass`, abundances `openmc.data.NATURAL_ABUNDANCE`, Avogadro's number `openmc.data.AVOGADRO`.

## Reading data

4. **`openmc.data.IncidentNeutron.from_hdf5`** reads one nuclide: reactions by MT number, cross sections by temperature as
   functions of energy in eV (`working_examples/nuclear-data`).
5. **From an evaluation:** `openmc.data.IncidentNeutron.from_endf` reads an ENDF file; `openmc.data.IncidentNeutron.from_njoy`
   processes one with NJOY (installed here) into data at chosen temperatures. `openmc.data.Evaluation` gives the raw sections.
6. **Resonance parameters and their covariance** come from an ENDF file's resolved resonance range (`working_examples/resonance-covariance`).
   Reconstructing cross sections from parameters needs a compiled extension this OpenMC build lacks ("Resonance reconstruction not
   available."); use NJOY for that.
7. **Windowed multipole data** (Doppler broadening on the fly, `openmc.data.WindowedMultipole`) are not part of the HDF5 library:
   turning `multipole` on in `openmc.Settings.temperature` needs the multipole files, which `python -m automc data fetch` can get
   for the examples.

## Using data in an analysis

8. An analysis that reads data and builds no model is `analysis.py`, run with PythonRunTool (`working_examples/nuclear-data`). Check
   what you compute against the library itself (a sum rule, a value at a tabulated energy) before reporting it.
