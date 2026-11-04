# A gamma detector's pulse-height spectrum

Derived from: the OpenMC notebook "Simulating the Signal of a Gamma Detector" (openmc-notebooks, tag v0.15.0; the detector's geometry
and materials, the two sources and their strengths, the tally and the resolution model). Written for AutoMC.

## The problem

What does a 3-inch sodium iodide scintillator record when photons hit it? A photon may deposit all its energy (photoelectric
absorption, or a Compton scatter followed by absorption), part of it (Compton scattering out), or nothing. The **pulse-height** score
records the total energy each source photon's history leaves in the crystal, so the tally over energy bins is the spectrum the
detector would count, before its own resolution blurs it.

## The model

| Part | Size | Material |
|---|---|---|
| crystal | r = 3.81 cm, z from 0.10 to 7.72 cm | NaI, 3.667 g/cm3 |
| reflector | 0.05 cm around and in front | Al2O3, 3.97 g/cm3 |
| casing | 0.05 cm around and in front | aluminium, 2.7 g/cm3 |
| back | z from 7.72 to 8.00 cm | iron, 7.874 g/cm3 |
| world | a sphere of r = 11 cm, vacuum outside | void |

**Sources** (`settings.photon_transport = True`, `particle="photon"`):

- **The beam:** 800 keV photons along the axis from z = -10 cm, 1e12 per second.
- **The Cs-137 point:** 1 g of Cs-137, 1 cm in front of the face, isotropic. With Ba-137m in equilibrium it gives 0.851 photons of
  661.657 keV per Cs-137 decay: 2.7e12 per second.
  - The notebook placed this source on the casing's front plane and took its photon spectra from `openmc.data.decay_photon_energy`.
  - That needs a depletion chain with decay photon data, which is not configured here. So the main line is given directly, and the
    barium X-rays near 32 keV are left out.

The strengths only weight the sources against each other, but **OpenMC then scales fixed-source tallies by their total**, so the
tally is in pulses per second. `post.py` divides by the total strength to get pulses per source photon.

## Results (OpenMC 0.15.3, ENDF/B-VIII.0 with photon data)

| | quick (200,000 photons, 5 s) | reference (4 million photons, 9 s) |
|---|---|---|
| photons that deposit energy | 0.3754 per source photon | 0.3754 |
| 662 keV peak over the continuum below it | x 836 | x 843 |
| 800 keV peak over the continuum below it | x 10,250 | x 10,365 |
| counts below / above the 662 keV Compton edge (477.3 keV) | 1.5 | 1.6 |
| resolution at 662 keV (the notebook's model) | 5.8% FWHM | 5.8% |

- **Full-energy peaks** stand at the source lines. The 800 keV peak stands higher above its surroundings because little else
  deposits 700 to 780 keV, while the band below 662 keV also holds the beam's Compton continuum.
- **The Compton edge**, E · 2E / (m_e c^2 + 2E) = 477 keV for the 662 keV line, ends that line's continuum. The beam's continuum,
  which runs to its own edge at 606 keV, softens the drop.
- **Resolution.** `post.py` blurs the spectrum with a Gaussian whose width is a + b·sqrt(E + c·E^2) (a = 1 keV, b = 4, c = 2e-4: the
  notebook's model of a NaI detector). It applies the blur by convolution rather than by sampling.

## Running it with AutoMC

1. **Build:** `OpenMCModelBuildTool` with `build.py`.
2. **Plot:** `OpenMCGeometryPlotTool`, an xz view of about 24 cm.
3. **Run:** `OpenMCRunStartTool`, approved by the user.
4. **Read:** `python post.py <run folder> --plot spectrum.png`.

## What it teaches

- Photon transport and photon sources.
- Several sources with strengths, and how they scale tallies.
- The pulse-height score.
- Peaks, Compton edges and resolution.
