# Universes and lattices

Derived from: the OpenMC 0.15.3 user's guide, geometry page (universes and lattices) (MIT). Written for AutoMC; the orders below were
checked by placing distinct universes and finding them with OpenMC's Python and its C++ (`openmc.lib.find_cell`), which agree
(2026-11-04).

## Universes

1. **A universe is a set of cells that fill all space,** used where something repeats: a pin, an assembly, a particle. Fill a cell
   with it, and the cell's region clips it. Its cells' regions must cover everything the filling cell can hold.
2. **One universe, many places:** each placement is an instance. To tally each instance separately use `openmc.DistribcellFilter`
   (`working_examples/candu`, `working_examples/pandas-dataframes`).

## Rectangular lattices: `openmc.RectLattice`

3. **Set `lower_left`, `pitch` and `universes`.** `universes` is a list of rows; for a 3-D lattice a list of layers of rows.
4. **The order is not the order of the indices.**
   - In a layer, the **first row is the top** (highest y) and the first entry of a row is the left end (lowest x): write the map as
     it looks from above.
   - The **first layer is the bottom** (lowest z).
5. **`outer`** is the universe for positions outside the lattice's elements; without it a point there is lost
   (`working_examples/hexagonal-lattice`, `working_examples/mg-mode-intro`).
6. **Nested lattices:** a lattice element may be a universe that holds another lattice: pins in assemblies, assemblies in a core
   (`working_examples/mg-mode-intro`, the C5G7 core).

## Hexagonal lattices: `openmc.HexLattice`

7. **Set `center`, `pitch` and `universes`, and know `orientation`:** `"y"` (the default: flat sides left and right) or `"x"`.
8. **`universes` is a list of rings, outermost first**, the centre last as a ring of one. In a ring of the `"y"` orientation the
   first element is at the top and the rest run clockwise; in the `"x"` orientation the first is at the right. Ring n (counting the
   centre as 0) holds 6n elements. `working_examples/hexagonal-lattice` prints the index map of both orientations; check yours the
   same way before trusting it.

## Particles and packing

9. **TRISO fuel:** `openmc.model.pack_spheres` places non-overlapping spheres in a region, `openmc.model.TRISO` makes each particle
   and `openmc.model.create_triso_lattice` puts them on a lattice so that tracking stays fast (`working_examples/triso`).

## Check

10. **Plot every lattice** (`rules/plots.md`) and compare it with the drawing or the map you were given, row by row. A mirrored map
    runs without an error.
