# A flux spectrum: per unit energy, per unit lethargy

Derived from: the OpenMC notebook "Tally a Flux Spectrum" (openmc-notebooks, tag v0.15.0; its model, OpenMC's built-in PWR pin cell,
and its two energy grids). Written for AutoMC.

## The problem

What does the neutron spectrum of a PWR pin cell look like, in the fuel and in the water? The model is OpenMC's own
`openmc.examples.pwr_pin_cell()`: a 2.4% UO2 pin, zircaloy, hot borated water, mirrored sides. Two flux tallies on a cell filter
(fuel, water) use two energy grids:

| Grid | Bins | Use |
|---|---|---|
| equal lethargy | 500 from 10 µeV to 20 MeV (`numpy.logspace`) | a smooth picture over all energies |
| SHEM-361 | 361, from `openmc.mgxs.GROUP_STRUCTURES` | fine around the low resonances; made for light-water lattices |

## Per energy, per lethargy

A bin's tally is the flux integrated over the bin, so wider bins hold more. To compare bins, or two grids, divide by the width:

- **by ΔE**, the flux per unit energy: the thermal peak dwarfs everything;
- **by Δu = ln(E_high / E_low)**, the flux per unit lethargy: what reactor physicists plot on a log energy axis. The area under the
  curve is then the flux.

## Results (OpenMC 0.15.3, ENDF/B-VIII.0)

| | quick (5,000 x 30 active, 9 s) | reference (50,000 x 100 active, 85 s) |
|---|---|---|
| k-infinity | 1.16055 +/- 0.00244 | 1.15994 +/- 0.00039 |
| thermal peak of the water's flux per lethargy | 0.063 eV | 0.063 eV |
| fuel flux at 6.67 eV / at 5 and 9 eV (SHEM-361) | 0.009 | 0.008 |
| thermal share (below 0.625 eV) of the fuel's flux | 0.147 | 0.146 |
| thermal share of the water's flux | 0.164 | 0.164 |
| SHEM-361 total / equal-lethargy total | 1.000000 | 1.000000 |

- **The thermal peak.** A Maxwellian at 293.6 K peaks per unit lethargy at 2kT = 0.051 eV. Absorption removes the slowest neutrons
  first, so the peak sits a little higher.
- **The resonance dip.** U-238's resonance at 6.67 eV is so strong that the fuel's flux there falls to about 1% of its surroundings:
  self-shielding. SHEM-361 resolves it; 500 even bins smear it.
- **The two grids** cover the same energies, so their totals agree.

## Running it with AutoMC

1. **Build:** `OpenMCModelBuildTool` with `build.py` (OPTIONS: bins, size).
2. **Run:** `OpenMCRunStartTool`, approved by the user.
3. **Read:** `python post.py <run folder> --plot spectra.png`.

## What it teaches

- Energy filters on grids of your own or on named group structures.
- Dividing by bin width or lethargy.
- The shape of a thermal-reactor spectrum.
- OpenMC's built-in example models.
