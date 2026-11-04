# Point source and a water sphere: flux and effective dose

Derived from: the AutoMC developers' toy problem (a point source in water) and a user's question of 2026-11-01. Written for AutoMC.

## The problem

An isotropic point source of neutrons and a sphere of water. What neutron flux and what effective dose rate does the water receive
for a given source strength? Two layouts:

| Layout | Geometry | Asked as |
|---|---|---|
| `offset` (default) | a 2.5 cm water sphere whose centre is 15 cm from the source, vacuum everywhere else (a 30 cm vacuum boundary closes the problem) | "dose on a water sphere of radius 2.5 cm, 15 cm from a 1e8 n/s point source" |
| `centre` | the source at the centre of a 50 cm water sphere with a vacuum boundary | the toy problem |

The source spectrum is not given in the question, so the example assumes a U-235 thermal-fission (Watt) spectrum and says so; any
single energy can be chosen instead (`energy: 2.0` for 2 MeV). Change it before trusting a dose for a real source (AmBe, Cf-252, D-T
at 14.1 MeV each have their own spectrum).

## The physics, and how a tally becomes an answer

- **What OpenMC counts.** A `flux` score on a cell is a track-length estimate: the total distance neutrons travel inside the cell, per
  source neutron (cm per source neutron). Dividing by the cell's volume gives the volume-averaged flux per source neutron
  (per cm2). Multiplying by the source strength S gives the flux in n/cm2/s.
- **Dose.** The same track length weighted, at each neutron's energy, by the ICRP-116 fluence-to-effective-dose coefficient (pSv cm2;
  the antero-posterior geometry, `openmc.data.dose_coefficients`) gives pSv cm3 per source neutron. Divide by the volume and multiply
  by S: pSv/s. Times 3,600 and 1e-6: uSv/h.
- **Fixed-source mode.** No eigenvalue, no inactive batches. Batches only split the histories so OpenMC can estimate the statistical
  error.
- **Water.** H-1 and O-16 at 1 g/cm3, with the bound-hydrogen thermal scattering law (`c_H_in_H2O`), which matters once neutrons slow
  down.

`post.py` does the arithmetic, prints each step with its unit, and takes `--strength` (default 1e8 n/s).

## The analytic check

With the target emptied (`fill: void`) nothing scatters, and the expected track length in a sphere of radius a whose centre is d from
an isotropic point source is exact: the chord through the sphere averaged over all directions,

    T = a/2 - (d^2 - a^2) / (2 d) * ln( (d + a) / sqrt(d^2 - a^2) )

For a = 2.5 cm and d = 15 cm, T = 0.0232783 cm per source neutron. It equals the solid-angle fraction the sphere subtends times its
mean chord. The void variant must reproduce it within the statistics; the reference run did, to 0.53 standard deviations. This tests
the geometry, the source and the normalisation together, before water is put back.

## Results (OpenMC 0.15.3, ENDF/B-VIII.0, reference size, 1e8 n/s Watt source)

| Variant | Track length (cm per source neutron) | Average flux (n/cm2/s) | Effective dose rate (uSv/h) |
|---|---|---|---|
| offset, void | 0.023223 +/- 0.000104 (exact 0.023278) | 3.548e4 | 4.43e4 |
| offset, water | 0.024028 +/- 0.000117 | 3.671e4 +/- 179 | 3.773e4 +/- 158 |
| centre, water (50 cm) | 72.030 +/- 0.046 | 1.3757e4 +/- 9 | 2,617 +/- 3 |

Reading them:
- **Water and void, offset.** The small sphere gets 3% more track length with water in it, because scattering lengthens the paths
  inside. Its dose is lower than the void's, because the dose counts each neutron at its energy and the scattered neutrons are
  slower, with smaller coefficients.
- **Inverse square.** At 15 cm a point source of 1e8 n/s gives about S/(4 pi r^2) = 3.5e4 n/cm2/s, close to the void result: the
  sphere is small, so the inverse-square estimate nearly holds.

## Running it with AutoMC

1. **Build:** `OpenMCModelBuildTool` with this `build.py` as the script (set the OPTIONS line to the layout, fill, energy and size
   wanted). The user approves the build.
2. **Plot:** `OpenMCGeometryPlotTool` shows the sphere and starred source.
3. **Check:** `OpenMCModelCheckTool` should pass, with a note that fixed-source tallies are per source neutron.
4. **Run:** `OpenMCRunStartTool`, approved by the user.
5. **Read:** `post.py` on the run folder, through `PythonRunTool`: `python post.py <run folder> --strength 1e8`.

| Size | Histories | Time here (4 threads) |
|---|---|---|
| quick, offset | 20,000 x 5 | about 6 s |
| reference, offset | 1,000,000 x 10 | about 7 s (void) |
| quick, centre | 2,000 x 5 | about 7 s |
| reference, centre | 100,000 x 10 | about 25 s |

## What it teaches

- A fixed-source model with a point source and an energy distribution.
- Cell tallies of flux.
- Dose by an energy-function filter.
- Normalising per-source-particle tallies to a source strength and to a volume.
- An analytic check before the real material is used.
