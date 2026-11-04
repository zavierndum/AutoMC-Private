# A PWR fuel pin: k-infinity and reaction rates

Derived from: the OpenMC notebook "Modeling a Pin-Cell" (openmc-notebooks, tag v0.15.0; its dimensions and compositions). Written for
AutoMC.

## The problem

One fuel pin of a pressurised-water reactor in its square of water, with mirror boundaries on the four sides. Reflection makes it an
infinite array of identical pins (and infinite along the pin axis). The answer is k-infinity, and how often U-235 in the fuel
absorbs, fissions and captures neutrons.

## The model

| Region | Radius or size | Material |
|---|---|---|
| fuel pellet | r < 0.39 cm | UO2 at 10.0 g/cm3, U-235 3% of the uranium atoms (option `enrichment`) |
| gap | 0.39 to 0.40 cm | empty (helium in a real pin; it hardly matters for neutrons) |
| cladding | 0.40 to 0.46 cm | zirconium at 6.6 g/cm3 |
| moderator | out to the 1.26 cm square | light water at 1.0 g/cm3, bound hydrogen |

- **Geometry:** four `ZCylinder` surfaces and a reflective `RectangularPrism`. The geometry has no top or bottom: it is a 2-D
  problem.
- **Source:** the initial fission source is a box over the pellet, limited to fissionable material.
- **Tally:** U-235's total, fission, absorption and (n,gamma) rates in the fuel, per source neutron.

## Results (OpenMC 0.15.3, ENDF/B-VIII.0)

| Size | Histories | k-infinity | U-235 alpha (capture/fission) | Time here |
|---|---|---|---|---|
| quick | 1,000 x (60 - 20) | 1.40124 +/- 0.00492 | 0.202 | 8 s |
| reference | 20,000 x (220 - 20) | 1.39728 +/- 0.00040 | 0.203 | 57 s |

The notebook printed 1.40544 +/- 0.00302, made with an older data library. The difference is the data, not the model.

An alpha of about 0.2 means U-235 captures one neutron for every five it fissions. At 0.0253 eV alone the ratio is about 0.17
(99 b of capture against 585 b of fission). The neutrons absorbed in the resonances, where capture is relatively larger, raise the
spectrum average a little. The test accepts 0.15 to 0.30.

## Running it with AutoMC

1. **Build:** `OpenMCModelBuildTool` with `build.py`. Set `enrichment` in the OPTIONS line for another fuel.
2. **Plot:** `OpenMCGeometryPlotTool`. An xy view of about 1.4 cm shows the pellet, gap, cladding and water.
3. **Check:** `OpenMCModelCheckTool`. A note says the geometry has no vacuum boundary, which is right for an infinite lattice.
4. **Run:** `OpenMCRunStartTool`, approved by the user.
5. **Read:** `python post.py <run folder>`.

## What it teaches

- Materials by nuclide and element, with S(a,b).
- Cylinders and a rectangular prism.
- Reflective boundaries.
- An eigenvalue run.
- A fissionable-only initial source.
- Nuclide reaction-rate tallies, and reading alpha.
