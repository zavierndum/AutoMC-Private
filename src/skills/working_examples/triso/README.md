# TRISO particles packed in graphite

Derived from: the OpenMC notebook "Modeling TRISO Particles" (openmc-notebooks, tag v0.15.0; its layer radii, compositions and packing
fraction). Written for AutoMC.

## The problem

High-temperature gas reactors carry their fuel as TRISO particles, a kernel inside four coatings, scattered at random through a
graphite matrix. The example packs them into a 1 cm cube at a 30% volume fraction, with no two particles overlapping, and makes the
geometry fast to track. It is geometry only (`run_mode plot`).

| Layer | Outer radius (um) | Material |
|---|---|---|
| kernel | 215 | UCO: U-235, U-238, O-16, C (atom fractions 0.0467, 0.287, 0.5, 0.167), 10.5 g/cm3 |
| buffer | 315 | porous carbon, 1.0 g/cm3 |
| inner PyC | 350 | pyrolytic carbon, 1.9 g/cm3 |
| SiC | 385 | silicon carbide, 3.2 g/cm3 |
| outer PyC | 425 | pyrolytic carbon, 1.87 g/cm3 |
| matrix | (around the particles) | graphite, 1.1995 g/cm3 |

Every carbon layer uses the graphite thermal scattering law (`c_Graphite`).

## How it is built

1. **One particle as a universe:** concentric spheres, the outermost layer left open. `TRISO` places the universe at a centre and
   bounds it by its outer sphere.
2. **Random packing:** `openmc.model.pack_spheres` places non-overlapping spheres in the region at the requested packing fraction,
   by random sequential addition and, at higher fractions, a close-random-packing pass. A fixed seed gives the same packing every
   time.
3. **A coarse lattice:** `create_triso_lattice` sorts the particles into a 3 x 3 x 3 lattice over the cube, with graphite as the
   background. A neutron then only tests the particles that touch its lattice element. Without it, every particle would be a cell in
   one universe and each collision would search them all.

`build.py` also writes `centers.json`, the particle centres, so `post.py` can check the packing.

## Results

| Quantity | Value |
|---|---|
| particles | 932 |
| packing fraction reached | 0.2997 (asked 0.30) |
| every particle inside the box | yes |
| closest centres | 850.3 um apart (no overlap needs 850.0 um) |

## Running it with AutoMC

1. **Build:** `OpenMCModelBuildTool` with `build.py`. The OPTIONS line sets the packing fraction, box size, seed and lattice.
2. **Plot:** `OpenMCGeometryPlotTool`. A slice of about 1 cm with 1,000 pixels or more shows the particles.
3. **Check:** `OpenMCModelCheckTool`.
4. **Read:** `python post.py <case folder>`.

For a criticality calculation, set `run_mode` to `eigenvalue` with a fissionable source. The reflective cube then stands for an
infinite compact.

## What it teaches

- Layered particles as universes.
- `pack_spheres` and its seed.
- `TRISO` and `create_triso_lattice` for tracking speed.
- Checking a random packing.
