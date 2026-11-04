# A CANDU 37-element bundle: k-eff and the flux by ring

Derived from: the OpenMC notebook "Modeling a CANDU Bundle" (openmc-notebooks, tag v0.15.0; its radii, pin rings and materials).
Written for AutoMC.

## The problem

A CANDU fuel channel holds a bundle of 37 natural-uranium pins in rings of 1, 6, 12 and 18, cooled by heavy water inside a pressure
tube. A gas gap separates the pressure tube from the calandria tube, and heavy-water moderator surrounds it. Two layouts:

| Layout | Around the channel | Purpose |
|---|---|---|
| `lattice` (default) | heavy-water moderator out to a 28.575 cm square with reflective sides: the channel in its lattice cell, an infinite array of channels | a realistic k-infinity and flux shape |
| `channel` | nothing: a vacuum boundary on the calandria tube | the notebook's geometry, a geometry lesson; nearly all neutrons leak, so k is tiny |

## The model

| Part | Radius (cm) | Material |
|---|---|---|
| fuel | 0.6122 | natural UO2, 10.0 g/cm3 |
| sheath | 0.6540 | zircaloy (as Zr), 6.0 g/cm3 |
| rings of pin centres | 0, 1.4885, 2.8755, 4.3305 (the third ring turned 15 degrees) | |
| coolant | inside the pressure tube | heavy water, 1.1 g/cm3, `c_D_in_D2O` |
| pressure tube | 5.16890 to 5.60320 | zircaloy |
| gas annulus | 5.60320 to 6.44780 | empty |
| calandria tube | 6.44780 to 6.58750 | zircaloy |
| moderator (`lattice`) | out to the 28.575 cm square | heavy water |

- **Pins.** Each pin is its own cell, named by ring and position, filled with one pin universe and translated to its place.
- **Coolant.** The coolant is split into annuli between the rings, so each annulus only excludes the pins it holds. That keeps the
  regions simple and particle tracking quick.
- **Tally.** A `DistribcellFilter` on the fuel cell gives one flux bin for each of the 37 places the fuel appears. `post.py` uses
  the geometry's paths to say which pin, and so which ring, each bin is in.

## Results (OpenMC 0.15.3, ENDF/B-VIII.0)

| Layout | Size | k | Ring flux relative to the centre pin (rings 0, 1, 2, 3) | Time here |
|---|---|---|---|---|
| lattice | reference (20,000 x 200 active) | 1.15696 +/- 0.00041 | 1.000, 1.009, 1.029, 1.066 | 110 s |
| channel | reference | 0.05617 +/- 0.00004 | 1.000, 0.931, 0.773, 0.531 | 20 s |

Reading them:
- **The lattice.** Thermal neutrons come from the moderator outside the bundle, so the outer ring sees the most flux and the centre
  the least: the flux dips toward the middle.
- **The channel alone.** Neutrons leak out through the sides and the flux peaks at the centre instead.
- **Symmetry.** In both layouts the pins of one ring agree within a few standard deviations, which the test checks.
- **k-infinity.** 1.157 is for fresh fuel at room temperature, with no xenon and no fission products. In a real core, burnup, xenon
  and the reactivity devices bring k down to 1.

## Running it with AutoMC

1. **Build:** `OpenMCModelBuildTool` with `build.py` (OPTIONS: layout, size).
2. **Plot:** `OpenMCGeometryPlotTool`. An xy view about 14 cm wide shows the bundle; 30 cm shows the lattice cell.
3. **Check:** `OpenMCModelCheckTool`.
4. **Run:** `OpenMCRunStartTool`, approved by the user.
5. **Read:** `python post.py <run folder>`.

## What it teaches

- Rings of pins placed by translation.
- Splitting a region into annuli.
- `DistribcellFilter` and mapping its bins to pins.
- An isolated channel compared with its lattice cell.
- Reading flux depression.
