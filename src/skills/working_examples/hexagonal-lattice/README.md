# Hexagonal lattices: rings, positions and orientation

Derived from: the OpenMC notebook "Modeling Hexagonal Lattices" (openmc-notebooks, tag v0.15.0; its layout and materials). Written for
AutoMC.

## The problem

A hexagonal lattice of 37 pins in water: 33 small pins of U-235 and 4 large pins of U-238 in a line from the centre outwards. It is
the shape of many fast-reactor, VVER and research-reactor assemblies. The example is about building such a lattice correctly. It is
geometry only (`run_mode plot`): it is plotted and checked, not run.

## How OpenMC fills a hexagonal lattice

- **Rings.** A `HexLattice` is filled ring by ring, from the outermost ring inwards: ring i (the centre is ring 0) has 6i positions,
  so 4 rings hold 1 + 6 + 12 + 18 = 37 positions (3n(n-1) + 1 for n rings).
- **Position order.** Within a ring the positions go clockwise, starting straight above the centre (orientation `y`) or straight to
  its right (orientation `x`). `HexLattice.show_indices(4, orientation="x")` prints the map of indices. `post.py` prints it for the
  built lattice.
- **Orientation.** With `y`, pins stack straight above one another and the lattice as a whole has a corner at the top; with `x`,
  everything turns by 90 degrees: pins sit side by side along x and the lattice has a flat side at the top. The boundary must match.
  A `HexagonalPrism` with the same orientation letter has its corners on the lines of corner pins. With an edge length of
  rings x pitch (5 cm here), those corners are 5 cm out, while the outer ring's corner pins are at 3.75 cm, so the outer ring fits
  with about one pitch to spare.
- **`outer`.** The lattice's outer universe (water here) fills everything around the lattice positions.
- **Pins.** Each pin is a universe: a fuel cylinder and water around it, with no outer boundary of its own. The lattice cuts it to
  its hexagonal cell.

| Part | Value |
|---|---|
| pitch | 1.25 cm |
| small pin | U-235 at 10 g/cm3, r = 0.25 cm |
| large pin | U-238 at 10 g/cm3, r = 0.50 cm, the first position of every ring and the centre |
| water | 1.0 g/cm3 |

## Results

`post.py` reads the built model back and counts:

| Variant | Positions | Large pins | Small pins | Orientation |
|---|---|---|---|---|
| `x-hexagon` (default) | 37 | 4 | 33 | x, a hexagonal prism boundary |
| `y-cylinder` | 37 | 4 | 33 | y, a cylindrical boundary |

The plots show the line of large pins and the boundary hugging the outer ring. OpenMC's plot mode finds no overlaps.

## Running it with AutoMC

1. **Build:** `OpenMCModelBuildTool` with `build.py` (OPTIONS: orientation, boundary, rings).
2. **Plot:** `OpenMCGeometryPlotTool`, colour by material.
3. **Check:** `OpenMCModelCheckTool` (a geometry-only model).
4. **Read:** `python post.py <case folder>`.

To make it a criticality problem, set `run_mode` to `eigenvalue`, give batches and a fissionable source, and add tallies (see
`pincell`).

## What it teaches

- `HexLattice` filling order and orientation.
- `HexagonalPrism`.
- Lattice `outer` universes.
- Pin universes.
- Counting what a lattice holds.
