# Working examples

Complete, tested OpenMC models, each a folder with `README.md` (the problem, the physics, the results, how to run it with AutoMC),
`build.py` (the OpenMC Python API; an `OPTIONS` line at the top chooses the variant and the size; writes `model.xml`, runs no
transport), `post.py` (reads a run and prints the physical answer) and `expected.json` (the values the tests compare). Some also
have `run.py`: Python that runs transport itself (a search, a depletion, openmc.lib), sent to OpenMCRunStartTool with the case as a
script run; it runs in a copy of the case and imports its `build.py`. An analysis (nuclear data read and processed, no model) has
`analysis.py` instead of `build.py`, run with PythonRunTool. Read the one nearest to the user's problem with SkillLookupTool before
writing a build script, and adapt it; do not copy it blindly.

Some need input files AutoMC does not ship (CAD geometries, meshes, ENDF and multipole files). They are listed in `data.json` and
fetched by the user with `python -m automc data fetch EXAMPLE` into the data cache (AUTOMC_DATA_CACHE, else ~/.cache/automc/data);
the example's script copies what it needs from there into its case, checked against its SHA-256, and says how to fetch a file that
is missing.

Every example's results come from OpenMC 0.15.3 with ENDF/B-VIII.0 and were checked against an analytic value, a benchmark, or a
reference run made here (its README says which). The tests build every variant through OpenMCModelBuildTool, check it, run it through
the job service and compare (`tests/examples`, `AUTOMC_SLOW_TESTS=1`).

## Fixed source, shielding and dose

- `point-source-sphere-dose`: an isotropic point source and a water sphere (offset 15 cm, or around the source); flux and ICRP-116
  effective dose normalised to a source strength; an exact void check.
- `slab-fluence`: a 2 MeV beam through water and concrete slabs; a mesh tally of the fluence along the beam.
- `shielded-room-weight-windows`: 2.5 MeV neutrons in a concrete building; an analog run, then weight windows made by the MAGIC
  method (openmc.lib, a script run), compared by their figure of merit.
- `gamma-detector`: photon transport into a NaI detector from an 800 keV beam and a Cs-137 source; the pulse-height spectrum, its
  peaks and Compton edge, and the detector's resolution applied.

## Criticality

- `jezebel`: the bare plutonium sphere of the ICSBEP benchmark PU-MET-FAST-001; k-eff against the benchmark.
- `pincell`: a PWR fuel pin with reflective sides; k-infinity and U-235 reaction rates.
- `candu`: a CANDU 37-element bundle, alone (the notebook's geometry) or in its square lattice cell; flux per pin by ring.
- `criticality-search`: the critical boron concentration of a PWR pin found by `openmc.search_for_keff` (a script run); the boron
  worth.

## Depletion

- `depletion`: a PWR pin burned for 180 days at 174 W/cm with a coupled operator and the predictor integrator (a script run, a
  depletion chain from OPENMC_CHAIN_FILE); k, U-235 and Xe-135 against time, and the burnup checked against the energy.

## Tallies and post-processing

- `post-processing`: reading a statepoint: a mesh tally as NumPy arrays, relative errors, the source bank's sites and energies.
- `tally-arithmetic`: the six-factor formula from tallies combined by tally arithmetic, leakage as net current through a mesh's
  surfaces; a microscopic cross section.
- `pandas-dataframes`: a 17 x 17 assembly's pin map read as DataFrames, a distributed-cell tally, a precision trigger, symmetry.
- `expansion-filters`: flux shapes as Legendre (along z) and Zernike (over a disc) expansions, rebuilt and compared with bins.
- `flux-spectrum`: a pin cell's spectrum on equal-lethargy bins and on SHEM-361; per-lethargy plots, the thermal peak, a resonance.
- `power-normalization`: per-source tallies turned into per-second values at a given power; energy per fission.

## Multigroup cross sections and multigroup mode

- `mgxs-intro`: two-group total, absorption, scattering and nu-fission cross sections of an infinite mixture, made by openmc.mgxs;
  the sum rule, and k-infinity from one-group data.
- `mgxs-advanced`: eight-group data in each cell of a PWR pin, by nuclide, with the scattering matrix, chi and tally triggers;
  condensation to two groups checked against a direct tally.
- `delayed-neutron-data`: beta and the precursor decay constants in six delayed groups, the prompt and delayed fission spectra; why a
  mixture's beta is not the sum of its nuclides' betas.
- `mgxs-library-mg-mode`: a 17 x 17 assembly run in continuous energy with an MGXS library, written as an MG file and run again in
  multigroup mode (a script run); the multigroup bias.
- `mg-mode-advanced`: a BWR assembly beside a control blade; direction-averaged against angle-dependent libraries on a mesh, each
  against continuous energy (a script run).
- `mg-mode-intro`: multigroup mode from hand-entered data, the C5G7 benchmark's seven-group cross sections as XSdata. An infinite
  medium checked exactly, a pin cell, and the benchmark's 2-D quarter core against its reference k, assembly powers and pin powers.

## Nuclear data (analyses, run with PythonRunTool)

- `nuclear-data`: a nuclide of the HDF5 library, its ENDF file, data made with NJOY, and windowed multipole data; each checked
  against another source; Doppler broadening of a U-238 resonance keeps its area.
- `resonance-covariance`: the covariance matrix of Gd-157's resolved resonances (ENDF File 32), made whole, checked as a covariance,
  sampled, and cut to one spin.

## Coupling and the C API

- `c-api`: openmc.lib steps a 1 m pin of ten zones batch by batch, reads the heat from memory, changes fuel temperatures and water
  densities, and runs again (a script run); the reactivity change and the shift of the heat between the two states.

## CAD geometry and unstructured meshes

- `cad-geometry`: DAGMC models: a CAD pin against its exact CSG twin (a script run), an iron teapot with a source only in its water,
  and a published CAD tokamak whose cut leaks unless a CSG box mirrors it (a script run). The tokamak's touching volumes are not
  merged (duplicate faces), which the model check marks as overlaps; its README says what to trust and how to check a CAD file.
- `unstructured-mesh-tallies`: flux and heating on a tetrahedral mesh (MOAB) of four pin channels, checked against exact cylinder
  tallies; the data written to VTK.
- `cad-mesh-visualisation`: a CAD steel manifold with a tally on its own tetrahedral mesh in two energy groups; the flux falls and
  softens away from the source; VTK and a point cloud for viewing.

## Geometry building

- `hexagonal-lattice`: a hexagonal lattice of two pin types, x and y orientations, a hexagonal prism boundary.
- `triso`: TRISO particles packed at random into a graphite box, placed in a lattice for fast tracking.
