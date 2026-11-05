# Troubleshooting: crashes, lost particles, geometry debugging

Derived from: the OpenMC 0.15.3 user's guide, troubleshooting page (MIT), with the behaviour read in OpenMC's source
(`src/geometry.cpp`, `check_cell_overlap`; `src/output.cpp`, `print_overlap_check`; `src/particle.cpp`, `write_restart`;
`src/particle_restart.cpp`; `openmc/deplete/pool.py`) and what the working examples met. The replay of a lost particle and the
coverage summary were checked on a small model with a gap (2026-11-04). Written for AutoMC.

Messages the error knowledge base knows (`ERKB.yaml`) come back as `known_errors` in the failed result; this file covers what has no
message to match, and how to find a fault in the geometry.

## Reading a failure

1. **Start from what the result carries:** its error, the end of its log (`log_tail`) and its `known_errors`. When those do not
   explain it, read the whole log with FileReadTool. The first ERROR or WARNING line usually names the cause; what follows it is
   mostly consequence.
2. **Make it fail small:** fewer particles and batches, one thread, only the part of the model that fails. A small failure is
   quicker to repeat and points closer to its cause.

## Crashes

3. **A crash with no error line.** `openmc.run` raises "OpenMC aborted unexpectedly" only when OpenMC printed no ERROR line before
   it stopped; that is usually a segmentation fault (the program read memory outside its own). Find the smallest case that does it
   and report it with the OpenMC version, the data library and the input. Finding the failing line needs a debug build of OpenMC,
   which is work for OpenMC's developers, not a fix AutoMC can make.
4. **A geometry-debug run may crash right after its overlap message.** The message is the finding; the crash after it is not a
   second fault to chase (`openmc-validation/rules/before-run.md` 5).

## Lost particles

5. **A lost particle crossed a surface into space no cell covers.** Check two things:
   - the outer surfaces carry a boundary (vacuum, reflective, periodic);
   - every part of the space inside is in some cell. Space with no material still needs a cell: one with no fill is void.
6. **Find the missing cell.** The warning names the surface the particle crossed. The cells whose regions use that surface
   (`openmc.Region.get_surfaces` on each cell's region, or a search of `model.xml`) border the gap; the missing cell is on the far
   side of one of them. Plot that place with the cells coloured and named, and show the user.
7. **Replay one lost history to see where it went.** Each lost particle leaves `particle_<batch>_<id>.h5` in the run folder. It holds
   the particle's starting state (where its history began), not the place it was lost. A script run whose `run.py` calls
   `openmc.run(restart_file=...)` with that file's path replays the one history with full output: every surface it crosses, then,
   at each level of the geometry, the cell it was last in and its coordinates where it was lost. Those coordinates are where to plot.
8. **Never raise the lost-particle limits to let a run finish** (`openmc-authoring/rules/settings.md` 11). One lost particle already
   means the geometry has a fault.

## Geometry debugging

9. **Plots first:** overlaps drawn red, every cell coloured and named, slices through the suspect parts
   (`openmc-authoring/rules/plots.md`, `openmc-validation/rules/before-run.md` 4 to 7).
10. **A geometry-debug run** (`openmc.run(geometry_debug=True)`, the command line's `-g`) checks, at every event of every particle,
    whether a second cell at any level of the geometry also contains the particle. It stops at the first such point with
    "Overlapping cells detected: <cell id>, <cell id> on universe <universe id>". It is much slower than a normal run, so run it small.
11. **Read its coverage.** A geometry-debug run that ends normally prints a CELL OVERLAP CHECK SUMMARY: how many checks were made
    in each cell, then the cells with fewer than 10. A cell with few checks was hardly tested, and the clean run says nothing about
    it. Run more particles, or move the source (a box over the parts rarely reached) until every cell that matters has been checked,
    and name the ones that were not.
12. **A clean geometry-debug run** shows only that the particles met no overlap where they went. It does not replace the plots of
    the parts they rarely reach.

## CAD files

13. **`check_watertight`, DAGMC's tool:** give it an output file, `check_watertight -o check.h5m model.h5m`; without `-o` it may
    write over the file it checks. Read its report together with the model check and the plots:
    - it passed a tokamak whose touching volumes were not merged (`working_examples/cad-geometry`, "Using the tokamak");
    - its default, topological check reported 6 of the CAD pin's 21 surfaces unsealed, although every pin volume closed point for
      point and the pin's k matched its CSG twin.

    A file that passes can still be unmerged; the model check finds that (`openmc-validation/rules/before-run.md` 7).

## Depletion

14. **A depletion that hangs or crashes at random** may come from an MPI build that does not allow its processes to fork. Set
    `openmc.deplete.pool.USE_MULTIPROCESSING = False` in `run.py` before the integrator starts: the materials are then solved one
    after another, slower but without forking.
