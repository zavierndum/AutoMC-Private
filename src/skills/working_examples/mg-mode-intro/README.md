# Multigroup mode from hand-entered data: the C5G7 benchmark

Derived from: the OpenMC notebook "Multigroup Mode Part I: Introduction" (openmc-notebooks, tag v0.15.0; the use of XSdata,
MGXSLibrary and Macroscopic, and the C5G7 core as its problem). The cross sections, the geometry and the reference results are those
of the OECD/NEA benchmark specification NEA/NSC/DOC(2003)16: Tables 1(a) to 1(g), Figures 1 to 3, and the reference MCNP solution
(Tables 3 and 5, page 25). Written for AutoMC.

## The problem

Multigroup mode can run on any group data, not only data OpenMC made: a benchmark's published cross sections, or a lattice code's
output. The C5G7 benchmark was written to test transport codes without homogenising the fuel pins. It gives seven-group cross sections
for seven materials and a two-dimensional quarter core of four 17 x 17 assemblies, two UO2 and two MOX, in water. Its reference answer
is a multigroup MCNP run with those same data and that same geometry. An OpenMC multigroup run of the same problem must therefore agree
with it to within statistics, which makes it a sharp test of the data entry and the geometry.

## The model

| Part | Value |
|---|---|
| data | seven groups for UO2, MOX at 4.3, 7.0 and 8.7%, the fission chamber, the guide tube and the moderator, entered in `DATA` from the benchmark's tables; isotropic scattering |
| pin | radius 0.54 cm in a 1.26 cm square of moderator; guide tubes and fission chambers have the same geometry |
| assemblies | 17 x 17; UO2 throughout, or MOX at 4.3% on the edge, 7.0% inside it and 8.7% in the centre; 24 guide tubes and a central fission chamber in both |
| core | UO2 and MOX in a checkerboard at the top left, moderator along the right and the bottom: 64.26 cm square |
| boundaries | mirrored on the top and left (the core's centre is the top left corner), vacuum on the right and bottom, mirrored in z |

**The total cross section is the benchmark's transport-corrected one.** Its specification asks for this. With it, absorption plus
each row of the scattering block equals the total for every material and group, to 3 parts in a million. The benchmark gives no group
boundaries, so `EDGES` is nominal: in multigroup mode it only labels the groups.

Three layouts (`OPTIONS["layout"]`):
- `infinite`: the UO2 data alone in an infinite medium. Its k-infinity also follows exactly from the seven-group balance equations,
  which `post.py` solves.
- `pincell`: one UO2 pin in moderator, mirrored.
- `core`: the benchmark itself, with the fission rate tallied in every pin.

## How it runs

- **`build.py`** writes the seven data sets as `openmc.XSdata` into an `openmc.MGXSLibrary` (`mgxs.h5`, next to `model.xml`). Each
  material is `Macroscopic` data from it. For the core it builds:
  - one universe per pin type;
  - the two assembly lattices from the pin maps `MOX_MAP` and `UO2_MAP`, which an assertion checks against the benchmark's counts:
    64, 100 and 100 MOX pins and 24 guide tubes;
  - the 3 x 3 core lattice;
  - a 34 x 34 mesh tally of fission over the fuel.
- **`post.py`** reads k, and for each layout:
  - **infinite:** compares k with the exact seven-group value;
  - **pincell:** prints the thermal flux per volume in the fuel against the moderator;
  - **core:** normalises the pin powers so the 1,056 fuel pins average 1, as the benchmark does, and compares k, the three assembly
    powers and the highest and lowest pins with the reference.

## Results (multigroup, so no nuclear data library)

| Layout | Size | Histories | k | Checked against | Time here |
|---|---|---|---|---|---|
| infinite | quick | 5,000 x (60 - 20) | 0.73719 ± 0.00092 | exact 0.73821: -1.1 σ | 10 s |
| infinite | reference | 50,000 x (220 - 20) | 0.73825 ± 0.00012 | exact 0.73821: +0.3 σ | 185 s |
| pincell | reference | 50,000 x (220 - 20) | 1.32588 ± 0.00023 | thermal flux per volume, fuel / moderator 0.853 | 80 s |
| core | quick | 10,000 x (100 - 40) | 1.18731 ± 0.00107 | reference 1.186550: +76 pcm, +0.7 σ | 12 s |
| core | reference | 100,000 x (300 - 50) | 1.18639 ± 0.00016 | reference 1.186550: -16 pcm, -0.9 σ | 221 s |

The core at the reference size, against the benchmark's MCNP solution (pin powers averaging 1):

| | Here | Reference |
|---|---|---|
| inner UO2 assembly | 492.3 | 492.8 ± 0.10% |
| MOX assemblies (two, mirror images) | 211.8 and 212.2 | 211.7 ± 0.18% |
| outer UO2 assembly | 139.7 | 139.8 ± 0.20% |
| highest pin | 2.496 ± 0.009 (row 4, column 5, inner UO2) | 2.498 ± 0.16% |
| lowest pin | 0.229 ± 0.003 (row 33, column 33, the outer UO2's vacuum corner) | 0.232 ± 0.58% |

Rows and columns are counted as in the benchmark's Figure 3, from 1 at the top left.

- **k and every assembly power agree within statistics,** as they should for the same data and geometry:
  - k is within one combined standard deviation;
  - the assemblies are within 0.2%, about the reference's own error.

  The assembly powers are the sharper test of the geometry, since the reference knows them to 0.1 to 0.2%.
- **The power is highest in the inner UO2 assembly**, next to the core's centre, and lowest at the outer corner by the vacuum
  boundaries.
- **At the quick size the highest pin reads high** (2.63 ± 0.05). The largest of 1,056 noisy values sits a few standard deviations
  above the true maximum. Judge pin peaks at the reference size.
- **The notebook** got 1.18574 ± 0.00111 for the core with its own copy of the data.

## Running it with AutoMC

1. **Build:** `OpenMCModelBuildTool` with `build.py`. It writes `mgxs.h5` beside `model.xml`, and each run folder gets a copy.
2. **Plot and check:** `OpenMCGeometryPlotTool` (the xy plot shows the checkerboard and the reflector), `OpenMCModelCheckTool`.
3. **Run:** `OpenMCRunStartTool`, approved by the user.
4. **Read:** `python post.py <run folder>` through `PythonRunTool`.

Choose the layout and size in `OPTIONS` (or `--layout core --size reference`).

## What it teaches

- Multigroup data written by hand: `XSdata` (total, absorption, the scattering matrix, fission, nu-fission, chi) and `MGXSLibrary`.
- `Macroscopic` materials and `materials.cross_sections` pointing at the library by a relative path.
- A benchmark's convention (the transport-corrected total) and why its group boundaries do not matter in multigroup mode.
- Nested lattices: pins in assemblies, assemblies in a core, and mixed boundary conditions on one box.
- A pin-power map from a mesh tally, normalised the way a benchmark defines it.
- An exact check (the infinite medium) and a benchmark check (the core).
