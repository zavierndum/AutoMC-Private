# The C API: OpenMC stepped from Python, with feedback between two states

Derived from: the OpenMC notebook "Using the C/C++ API" (openmc-notebooks, tag v0.15.0; the pin, its ten axial zones, the
materials and temperatures, the heat tally, and the calls that change temperatures and densities). Written for AutoMC.

## The problem

A coupled calculation does not run OpenMC once. It keeps OpenMC in memory and repeats a loop:
1. run some batches;
2. hand the heat in each region to a thermal-hydraulics code;
3. take back new temperatures and densities;
4. run again.

`openmc.lib`, the Python wrapper of OpenMC's C API, does exactly this. It initialises once, runs batches one at a time, reads tallies
from memory and changes cells and materials between runs. This example does one turn of that loop on a PWR pin cut into ten axial
zones, and measures what the feedback did.

## The model

| Part | Value |
|---|---|
| pin | UO2 2.4 wt% (10.29769 g/cm3, r = 0.39218 cm), helium gap, Zircaloy-4 cladding to r = 0.45720 cm, pitch 1.25984 cm |
| height | 1 m in ten 10 cm zones; vacuum at both ends, mirrored sides |
| zones | each its own fuel, gap, cladding and water cells, and its own water material, so each can be changed alone |
| as built | fuel 800 K, gap 700 K, cladding 600 K, water 500 K at 0.70 g/cm3 |
| feedback | fuel 900 K; water from 0.74 g/cm3 at the bottom to 0.66 at the top (heated along the channel, the same average) |
| tally | the heat deposited in each fuel zone (`fission-q-recoverable`) |

**The pin is 1 m tall, not the notebook's 3 m.** A tall pin's fission source settles slowly. The ratio of its first two axial modes
is about 0.98 at 3 m, so a tilt in the source shrinks by only 2% per batch and lasts hundreds of batches. At 1 m the ratio is about
0.88, and 30 inactive batches remove it. A first run of this example at 3 m gave lumpy, lopsided heat profiles that were noise, not
physics. A full-length pin or core needs many more inactive batches, and the Shannon entropy to show when the source has settled.

Temperatures are interpolated between the library's (`settings.temperature`, range 250 to 1200 K), and all of them are loaded, so a
cell can be set to any of them while OpenMC runs.

## How it runs

- **`build.py`** builds the pin as it is built (`model.xml`), for plotting and checking.
- **`run.py`** is a **script run** (it steps OpenMC through its C API):
  1. `openmc.lib.init()` once;
  2. the first state: `reset()`, `simulation_init()`, every batch with `next_batch()`, then `keff()` and the heat tally read from
     memory (`openmc.lib.tallies[id].mean`), then `simulation_finalize()`;
  3. the feedback: `cells[id].set_temperature(...)` for every fuel cell, `materials[id].set_density(...)` for every water zone,
     both read back from OpenMC;
  4. the second state, run the same way, and `finalize()`. It writes `comparison.json`.
- **`post.py`** reads it:
  - **reactivity:** the change between the states, (k2 - k1) / (k1 k2), with its standard deviation;
  - **heat:** each state's heat by zone, normalised to an average of 1, and its centroid (the heat-weighted mean height);
  - **readback:** whether OpenMC reports what was set.

## Results (OpenMC 0.15.3, ENDF/B-VIII.0)

| Size | Histories per state | k as built | k after the feedback | Reactivity change | Heat centroid | Time here |
|---|---|---|---|---|---|---|
| quick | 2,000 x (70 - 30) | 1.22568 ± 0.00324 | 1.21406 ± 0.00318 | -780 ± 305 pcm | 50.2 to 48.7 cm | 16 s |
| reference | 20,000 x (230 - 30) | 1.21665 ± 0.00044 | 1.21368 ± 0.00047 | -201 ± 44 pcm | 49.9 to 48.6 cm | 216 s |

The heat by zone at the reference size, bottom to top, each state averaging 1:

| Zone | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 |
|---|---|---|---|---|---|---|---|---|---|---|
| as built | 0.328 | 0.762 | 1.104 | 1.348 | 1.474 | 1.473 | 1.340 | 1.089 | 0.755 | 0.327 |
| after the feedback | 0.361 | 0.830 | 1.176 | 1.403 | 1.490 | 1.444 | 1.283 | 1.022 | 0.692 | 0.298 |

- **As built, the heat is symmetric:**
  - the bottom zone gets 1.004 times what the top one gets;
  - both end zones get only a fifth of what the middle ones get, because neutrons leak out of the ends.
- **The feedback moves the heat down by 1.3 cm.** The denser water at the bottom moderates better, so more fissions happen there.
  That is the kind of shift a coupled iteration then feeds back again.
- **The reactivity falls by 201 ± 44 pcm.** The water's average density is the same in both states, so most of this is likely the
  hotter fuel's Doppler effect: about -2 pcm per K, typical of UO2 fuel.
- **At the quick size the change reads -780 ± 305 pcm,** within two of its standard deviations of the reference. The heat centroid's
  shift, a smaller error on a cleaner quantity, is clear at either size.
- **Every value set** reads back exactly from OpenMC.
- **The notebook's k** (1.26075 ± 0.00292) was taken from one run with the change made half way through, which mixes the two states.
  Its pin was also 3 m tall.

## Running it with AutoMC

1. **Build:** `OpenMCModelBuildTool` with `build.py`.
2. **Plot and check:** `OpenMCGeometryPlotTool` (an xz plot shows the zones), `OpenMCModelCheckTool`.
3. **Run:** `OpenMCRunStartTool` with `case` and `script` (the text of `run.py`), approved by the user. It can be stopped or
   cancelled, not paused.
4. **Read:** `python post.py <run folder>` through `PythonRunTool`.

Change the feedback in `FEEDBACK` (fuel temperature, water at the bottom and top) and the run size in `OPTIONS`.

## What it teaches

- `openmc.lib`: `init`, `simulation_init`, `next_batch`, `keff`, `simulation_finalize`, `reset`, `finalize`.
- Tallies read from memory while OpenMC runs.
- Changing cell temperatures and material densities in memory, and what a model needs for it (one material per region that changes,
  temperature data loaded for the range).
- Two states in one session, each with its own k, instead of one run that mixes them.
- A reactivity change between states, and a power shift measured by its centroid.
- Why a tall geometry needs many inactive batches.
