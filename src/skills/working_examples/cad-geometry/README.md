# CAD geometry with DAGMC: a pin, a teapot and a tokamak

Derived from: the OpenMC notebook "DAGMC's CAD-Based Geometry in OpenMC" (openmc-notebooks, tag v0.15.0; the three CAD models, their
materials, the sources and the tallies). Written for AutoMC. The pin's CSG twin, the mirrored frames of the tokamak and the finding
that the tokamak's volumes are not merged are added here.

## The problem

Some geometries are hard or impossible to write as CSG: a teapot, a fusion device, a piping manifold. DAGMC lets OpenMC track
particles through a CAD model instead. The model is a surface mesh exported from a CAD program (an `.h5m` file). Its volumes carry
material tags, and a graveyard volume around everything kills the particles that reach it.

`openmc.DAGMCUniverse` loads such a file as a universe. It can be the whole geometry, or the fill of a CSG cell, so CAD and CSG mix.

This example runs three CAD models, chosen by `OPTIONS["model"]`, and checks each against something independent.

## The models

| Model | CAD file (data cache) | Run | What is checked |
|---|---|---|---|
| `pin` | `cad/fuel_pin.h5m`: a U-235 cylinder (r = 9 cm, 40 cm tall) in a water annulus to r = 11 cm | eigenvalue | k of the CAD pin against its exact CSG twin |
| `teapot` | `cad/teapot.h5m`: an iron teapot holding water | fixed source: 12 eV neutrons born only in the water | the flux in the water and in the iron; the leakage |
| `tokamak` | `cad/paramak_with_graveyard.h5m`: half a tokamak (Paramak), cut at y = 0 | fixed source: 14 MeV fusion neutrons on a ring in the plasma | the half as published against the same half mirrored at its cut, and a quarter mirrored at both cuts |

Fetch the files once with `python -m automc data fetch cad-geometry`. `build.py` copies the one a model needs into the case, and every
run folder carries it.

**Materials by tag.** Each CAD volume names its material, and OpenMC matches the tag to a `Material`'s name, or to its id when the
tag is a number. The pin's file tags its water "41", so its water material must have id 41. `DAGMCUniverse.material_names` lists the
tags a file uses.

**What the CAD files hold,** found by point queries (`openmc.lib.find_cell`) here, since a CAD file shows nothing until it is
queried or plotted:
- the pin's fuel is a cylinder of radius 9.000 cm, round to 4e-6 over 360 degrees, from z = -20 to +20 cm, in water to r = 11 cm, with
  empty space to the graveyard: `PIN_CSG` in `build.py` is that geometry in CSG;
- the teapot's water fills 1.8% of its graveyard box: a source sampled over the whole box would be rejected too often (OpenMC 0.15.3
  stops below 1 in 20 accepted), so the source box is drawn around the water;
- the tokamak fills only y >= 0, and nothing in the file mirrors it at its cut: the file has no reflecting boundary tags, only the
  graveyard;
- the tokamak's 19 volumes are each sealed and face outward, but where two of them touch, the file holds two copies of the same face,
  one per volume, instead of one face shared by both (read from the file's triangles; see "Using the tokamak" below).

## How it runs

- **`build.py`** builds the chosen model: `pin` with the CAD file as the root universe; `teapot` with a domain-restricted source and a
  flux tally per material; `tokamak` (the half as published) with a ring source and the blanket's flux.
- **`run_pin.py`** (a script run) runs the CAD pin and then `pin_csg()`, its CSG twin, with the same materials and settings.
- **`run_tokamak.py`** (a script run) runs the half three ways (`tokamak_geometry()`):
  - **as published;**
  - **mirrored:** inside a CSG box with a reflecting plane at its cut, y = 0, which stands for the whole torus;
  - **quarter:** a box cut again at x = 0 and reflecting there too, which also stands for the whole torus.

  A DAGMC universe inside a CSG cell needs `auto_geom_ids = True`, so its cell and surface ids do not clash with the CSG ones.
- **`post.py`** reads whichever model ran.

## Results (OpenMC 0.15.3, ENDF/B-VIII.0)

| Model | Size | Result | Time here |
|---|---|---|---|
| pin | quick | CAD 0.95289 ± 0.00187, CSG 0.95418 ± 0.00183: -129 pcm (-0.5 σ); leakage 0.581 against 0.580 | 8 s |
| pin | reference | CAD 0.95449 ± 0.00025, CSG 0.95399 ± 0.00025: +49 pcm (+1.4 σ); leakage 0.5797 against 0.5798 | 32 s |
| teapot | quick | flux per source neutron 16.6 cm in the water, 0.32 cm in the iron; leakage 0.697 | 8 s |
| teapot | reference | 16.54 cm in the water, 0.3231 cm in the iron; leakage 0.6979 | 28 s |
| tokamak | quick | blanket flux per source neutron: half 51.3 cm, mirrored 115.4 cm, quarter 118.4 cm; leakage 0.814, 0.183 and 0.176 | 21 s |
| tokamak | reference | half 51.23 ± 0.16 cm, mirrored 115.07 ± 0.16 cm, quarter 117.09 ± 0.29 cm; leakage 0.8141, 0.1831 and 0.1769 | 207 s |

Quick: pin and teapot 5,000 particles, tokamak 5,000 x 10 batches per frame. Reference: 50,000 particles (pin 220 batches, 20
inactive; teapot and tokamak 20 batches).

- **The CAD pin and its CSG twin agree** within statistics. A CAD geometry tracks like CSG when its surfaces are fine enough; these
  are.
- **The published half tokamak leaks 81% of its neutrons, mostly through its cut.** Mirrored at the cut, it leaks 18%, and its blanket
  sees 2.2 times the flux. The notebook ran the half as published and printed that same leakage (0.81232), which is the leakage of half
  a torus open on its cut face, not of a tokamak. A CAD sector needs reflecting boundaries: in the file (DAGMC boundary tags) or, as
  here, from a CSG box around it.
- **The model check marks overlaps in this CAD file, but its volumes do not overlap: they are not merged.**
  - The plots show points in the first wall, the blanket and the other touching volumes in two cells at once, in all three slices
    and still when a slice is moved a little. `openmc -g` (geometry debug) stops on "Overlapping cells detected", naming the plasma
    cell and DAGMC's implicit complement (the space outside every volume).
  - Every marked point lies inside exactly one volume when tested against the file's own triangles. The second claimant is the
    implicit complement. The cause is the duplicate faces described in "Using the tokamak" below.
  - The model check reads the file and says so: its overlap error names 25 pairs of touching volumes that are not merged, the
    first wall and the blanket first (8,064 triangles, 119 m2).
  - The mirrored half and the quarter should be the same torus, but at the reference size their blanket fluxes differ by 1.8%, 6.1
    combined standard deviations. The model is mirror-symmetric about x = 0: the quarter on the other side (x <= 0) gives
    117.34 ± 0.16 cm against 117.09 ± 0.29 cm (0.2%, 0.75 σ), and the mirrored half's blanket flux splits 57.62 ± 0.14 cm for
    x < 0 against 57.45 ± 0.14 cm for x > 0. So the 2% comes with the reflecting plane at x = 0 cutting through the CAD volumes, not
    with the model's shape. Whether the unmerged faces cause it needs a merged copy of the file, which has not been made. The
    comparison is therefore reported, not checked.
- **The teapot's check reported overlaps at first, in error.** The horizontal slice at z = 0 lies exactly on the water's flat top
  surface, where a point reads as in both cells. A slice 0.01 cm away shows nothing. The model check now tries each slice a little to
  either side, and reports such a slice as lying on a surface, not as an overlap.

## Using the tokamak, or a variation of it

**What the published file holds** (checked 2026-11-03, the file of `data.json`, SHA-256 796c4773...):

| Check | Result |
|---|---|
| DAGMC's `check_watertight` (installed in the automc environment) | 0 of 19 surfaces and 0 of 19 volumes leak |
| Each volume's triangles, joined on their points | closed and consistently oriented: every edge used twice, once in each direction; every signed volume positive |
| Touching volumes | no surface is shared by two volumes. 25 pairs touch through duplicate faces, triangle for triangle (29,114 triangles). The first wall and the blanket duplicate 8,064 triangles (119 m2); each poloidal field coil sits in its case with 99% of its surface duplicated |
| The points the model check marks | each inside exactly one volume, by the triangles |

The other two files are merged: the teapot's iron and water share their surfaces (127,367 triangles, none duplicated), and so do
the pin's volumes. Their model checks mark no overlaps.

**Why it matters.** OpenMC needs a DAGMC model to be watertight and its surfaces imprinted and merged [1]. Where two volumes touch,
there should be one face, with one volume on each side. This file passes the first test and fails the second.

DAGMC finds the volume that holds a point by tracing a ray to the faces around it. At a duplicated face the answer depends on which
copy the ray meets first, so the implicit complement also claims points inside the touching volumes. In transport, a particle leaving
one volume enters the implicit complement (void here) and must then meet the neighbour's copy at the same place.

**What to trust from this file:**
- the leakage through the cut (81% against 18%) and the mirrored frame. This is a large geometric effect, and the duplicate faces do
  not change the conclusion;
- not the blanket flux to the percent. Whether the duplicate faces change it has not been measured. No run here stopped on lost
  particles.

**A variation of this file** (other materials, sources, tallies or frames):
- the check marks the same "overlaps". They come from this file's unmerged faces, not from a mistake in the variation;
- say so with the results, and do not quote blanket results more finely than the comparisons here.

**Your own CAD model:**
- run the model check, and `check_watertight -o check.h5m <file>.h5m`. Without `-o` the tool may write over its input file;
- read `check_watertight` with care. It passes this unmerged tokamak. Its default (topological) check also reports 6 of the CAD
  pin's 21 surfaces unsealed, although every pin volume is closed point for point and its k matches the CSG twin;
- the model check reads each CAD file and names the touching volumes that hold two copies of a face. It matches faces triangle for
  triangle, so faces faceted apart escape it: if it finds the file merged but marks overlaps, and `openmc -g` names the implicit
  complement, still suspect touching volumes that were not merged before suspecting volumes that cross;
- the fix is in the CAD export: imprint and merge the volumes before faceting (for example with Cubit's imprint and merge [2]).
  OpenMC settings cannot repair it.

## Running it with AutoMC

1. `python -m automc data fetch cad-geometry`, once (the user runs it; it needs the network).
2. **Build:** `OpenMCModelBuildTool` with `build.py` (`OPTIONS["model"]` chooses the model).
3. **Plot and check:** `OpenMCGeometryPlotTool`, `OpenMCModelCheckTool`. A CAD model shows its shape only in plots.
4. **Run:**
   - `pin` and `tokamak`: `OpenMCRunStartTool` with `case` and `script` (`run_pin.py` or `run_tokamak.py`);
   - `teapot`: an ordinary run.
   
   The user approves each.
5. **Read:** `python post.py <run folder>` through `PythonRunTool`.

## What it teaches

- A CAD model as a DAGMC universe: material tags by name or id, the graveyard, `n_cells`, `material_names`.
- Finding out what a CAD file holds: point queries and plots.
- Checking a CAD geometry against CSG where both can describe it.
- A source restricted to one material (`constraints={"domains": [...]}`), and a source box drawn around it.
- CAD inside CSG: a DAGMC universe as a cell's fill, `auto_geom_ids`, and reflecting planes that turn a sector into the whole device.
- Reading the model check:
  - a real overlap persists through a volume, a slice on a surface does not;
  - in a CAD model, marked overlaps with the implicit complement point to touching volumes that were not merged;
  - a sealed file (`check_watertight`) can still be unmerged.

## References

1. OpenMC user's guide, "Geometry", section "Using CAD-based Geometry" (the note on clean DAGMC models):
   https://docs.openmc.org/en/stable/usersguide/geometry.html#using-cad-based-geometry
2. DAGMC user's guide, "Cubit basics" (imprint and merge): https://svalinn.github.io/DAGMC/usersguide/cubit_basics.html
