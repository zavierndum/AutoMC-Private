# Weight windows for deep shielding

Derived from: the OpenMC notebook "Variance Reduction - Weight Windows" (openmc-notebooks, develop bb39f25; the building, materials,
source and procedure). Written for AutoMC.

## The problem

A 2.5 MeV neutron source sits in a concrete room. Beyond its 2 m walls are a gap, a corridor and the outside air. In an analog run
almost no neutron gets through the concrete, so the flux outside is zero or very noisy, however long the run goes on.

**Weight windows** fix this. Space is divided into mesh cells, each with a band of allowed particle weights:

- a particle heavier than the band splits into several lighter ones;
- a lighter one plays Russian roulette.

Effort then follows the neutrons that matter. The **MAGIC** method sets each cell's band from a first, analog flux estimate, so the
second run pushes more histories into the regions the first barely reached.

## The model

- **The building.** 15.5 x 19 m in plan and 7 m high, built from 14 boxes: the room, its walls, roof and floor, an inner wall with a
  gap, a corridor, and the air outside. The concrete is 2.3 g/cm3 and its composition is given in atom fractions, as in the notebook.
- **The source.** A point in the room, 2.5 MeV, isotropic.
- **The tally.** A `RegularMesh.from_domain(geometry)` of 100 x 100 x 1 over the building (500 x 500 in the notebook), scoring the
  neutron flux.

## The procedure (run.py, one OpenMC session through openmc.lib)

1. `openmc.lib.init()`, then `openmc.lib.WeightWindows.from_tally(tally)`: windows on the tally's mesh, empty for now.
2. An analog run with the first particle count.
3. `windows.update_magic(tally)` makes the windows from that flux; then `settings.weight_windows_on = True`.
4. `openmc.lib.reset()` clears the tallies, and a second run uses fewer particles, since each history now splits.
5. Both runs' means and standard deviations go to `comparison.npz`.

Because it steps OpenMC through its C API, `run.py` is a **script run**: OpenMCRunStartTool with `case` and `script`, approved by the
user.

## Results (OpenMC 0.15.3, ENDF/B-VIII.0)

| | quick | reference |
|---|---|---|
| analog: histories, time | 2,000 x 5, 0.3 s | 20,000 x 5, 3.2 s |
| weight windows: histories, time | 200 x 5, 20 s | 2,000 x 5, 86 s |
| mesh cells reached, analog | 51% | 57% |
| mesh cells reached, weight windows | 68% | 78% |
| converged cells agreeing within 3 sigma | 96% | 98% |

- **The windows reach about a third more of the building:** the corridor and the far side of the walls, which the analog run hardly
  sees.
- **Where both runs converged** they agree within their errors: weight windows change the noise, not the answer.
- **The figure of merit.** 1 / (R^2 T) measures a method's efficiency independently of the run length. Taken over the cells both
  runs reached, it favours the analog run, because those cells lie mostly near the source, where analog sampling already does well.
  The windows pay off where the analog run gives nothing. Judge variance reduction on the region of interest, here the far side of
  the shielding.
- **Splitting costs time.** With windows made from a short analog run, the second run spends most of its time splitting in the deep
  concrete. The notebook saw the same: about 20 s for 300 particles per batch.

## Running it with AutoMC

1. **Build:** `OpenMCModelBuildTool` with `build.py` (OPTIONS: mesh, size).
2. **Plot:** `OpenMCGeometryPlotTool`, an xy view of 15.5 x 19 m at mid-height.
3. **Run:** `OpenMCRunStartTool` with `case` and `script` (the text of `run.py`), approved by the user.
4. **Read:** `python post.py <run folder> --plot flux_maps.png`.

To run with windows in an ordinary run instead, add a `WeightWindowGenerator` to the settings of one run, then load its
`weight_windows.h5` into the next case's build.

## What it teaches

- Weight windows and the MAGIC method.
- `openmc.lib` sessions: init, run, reset.
- Comparing methods by coverage, agreement and figure of merit.
- Why a figure of merit needs a region.
