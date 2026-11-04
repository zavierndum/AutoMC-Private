# Nuclear data from Python: the HDF5 library, ENDF files, NJOY and multipole data

Derived from: the OpenMC notebook "Nuclear Data" (openmc-notebooks, tag v0.15.0; the nuclides, the reactions looked at, the NJOY
temperatures and the multipole resonance). Written for AutoMC.

## The problem

Before trusting a cross section, or to explain a result, you often need to look at the data themselves:
- what a nuclide's cross section is at some energy;
- which reactions it has and where they open;
- how its resonances are described;
- how its data change with temperature.

`openmc.data` reads the formats involved: the HDF5 library OpenMC runs with, evaluated ENDF files, ACE files, and windowed multipole
data. It also drives NJOY to make new data from ENDF files. This example uses each of them once, and checks each against another
source.

**An analysis, not a model.** It has `analysis.py` instead of `build.py`. It reads and processes data, builds no model and runs no
transport, so the agent runs it with `PythonRunTool` (the user approves).

## What it reads

| Data | From |
|---|---|
| Gd-157, U-238 and H-2 in HDF5 | the library OPENMC_CROSS_SECTIONS names (ENDF/B-VIII.0) |
| `endf/n-064_Gd_157.endf`, `endf/n-001_H_002.endf` | ENDF/B-VIII.0 from NNDC, in the data cache |
| `wmp/092238.h5` | U-238 in the windowed multipole library of MIT CRPG (release v1.1), in the data cache |

Fetch the cached files once with `python -m automc data fetch nuclear-data`. The notebook's own files are gone: its ACE file returns
404, and the LANL server with its ENDF/B-VII.1 files no longer answers. This example uses the ENDF/B-VIII.0 files instead, the same
evaluation as the library.

## What it does, and what it checks

1. **The library's Gd-157:**
   - its six temperatures (250 to 2,500 K) and 92 reactions;
   - capture at 0.0253 eV;
   - where (n,2n) opens, and what it emits.
   - **Checks:**
     - below every threshold the total equals elastic plus capture, the only channels open there;
     - the unresolved-resonance probability tables are read;
     - an export to HDF5 reads back identical.
2. **Its ENDF file:**
   - the resonance ranges, resolved (Reich-Moore) and unresolved;
   - the first resolved resonances, with their energies, spins and widths.
   - **Check:** the library's probability tables span exactly the file's unresolved range.
3. **H-2 made with NJOY:**
   - `IncidentNeutron.from_njoy` at 300, 400 and 500 K (`OPTIONS`), with 0 K elastic data added for resonance scattering.
   - **Check:** its elastic cross section at 1 eV against the library's at 294 K.
4. **U-238 from multipole data:** the capture cross section around its 6.67 eV resonance at 0, 294 and 900 K.
   - **Checks:** the peak falls with temperature while the area stays; the 294 K peak is compared with the HDF5 library's.

## Results (OpenMC 0.15.3; 5.5 s)

| Quantity | Value |
|---|---|
| Gd-157 capture at 0.0253 eV, 294 K | 252,892 b |
| Gd-157 (n,2n) threshold | 6.401 MeV |
| total against elastic + capture, below thresholds | equal to 2e-9 |
| unresolved range, library tables against the ENDF file | 306.6 to 54,881.1 eV, equal to 3e-7 |
| HDF5 export, read back | identical |
| resolved resonances in the ENDF file | 60, from 1e-5 to 306.6 eV; the first at 0.0314 eV |
| H-2 elastic at 1 eV: NJOY at 300 K against the library at 294 K | 3.41698 against 3.41651 b, +0.014% |
| U-238 6.67 eV resonance, peak at 0 / 294 / 900 K | 22,296 / 7,106 / 4,483 b |
| its area from 4 to 10 eV at 0 and 900 K | 856.005 and 856.028 b eV, equal to 3e-5 |
| the HDF5 library's peak at 294 K | 7,196 b, 1.3% above the multipole file's |

- **Gd-157's huge thermal capture comes from a resonance at 0.0314 eV,** almost at thermal energy. That is why gadolinium is a
  burnable absorber.
- **NJOY run here on the same ENDF/B-VIII.0 file reproduces the library to 0.014%.** So data made this way, for example at a
  temperature the library lacks, can be trusted to the same degree.
- **Doppler broadening:** heating U-238 from 0 to 900 K spreads the 6.67 eV resonance and lowers its peak five-fold, but keeps its
  area. A wider, lower resonance shields itself less, absorbs more neutrons and lowers k. That is the fuel's negative temperature
  coefficient.
- **The multipole file and the HDF5 library** were made separately. Their 294 K peaks agree to 1.3%.
- **Not shown:** evaluating 0 K cross sections directly from the ENDF resonance parameters needs a compiled extension this OpenMC
  build lacks. NJOY does that reconstruction instead, as in part 3.

## Running it with AutoMC

1. `python -m automc data fetch nuclear-data`, once (the user runs it; it needs the network).
2. `PythonRunTool` with the text of `analysis.py`, approved by the user. It writes nothing it keeps.

To look at another nuclide, use its library name (`library_file("Fe56")`) or another ENDF file. A new file needs an entry in
`data.json` and in `DATA`.

## What it teaches

- `openmc.data.IncidentNeutron` from HDF5, ENDF and NJOY: temperatures, reactions by MT, cross sections as functions of energy, products.
- Resonance ranges and parameters from an ENDF file; unresolved probability tables.
- Exporting data to HDF5 and the library's own structure.
- `openmc.data.WindowedMultipole`: cross sections at any temperature.
- Doppler broadening, and why it keeps a resonance's area.
- Checking data against a second source before relying on them.
