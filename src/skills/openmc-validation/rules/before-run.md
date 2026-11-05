# Before a run: is the model the one meant?

Derived from: the OpenMC 0.15.3 user's guide, troubleshooting and plots pages (MIT), and what AutoMC's model check does
(`tools/openmc/_author_child.py`, `check`). Written for AutoMC.

## What the model check looks at, and what it cannot

1. **OpenMCModelCheckTool** reads the model back and reports, each with a severity:
   - nuclear data: every nuclide and S(a,b) table in the library; a multigroup library that exists;
   - settings that fit the mode: inactive batches and fissionable material for eigenvalue, a source for fixed source, enough
     histories;
   - a vacuum boundary somewhere;
   - the source: points sampled and found in cells (more than 95% outside is an error; fewer is a warning, or a note when the
     source has a fissionable or domain constraint);
   - overlaps: OpenMC's own overlap marking on slices through the middle and through the source;
   - CAD files: touching volumes that hold two copies of a face instead of sharing one (not merged), read from the file's
     triangles;
   - files `model.xml` names (missing, absolute, outside the case);
   - meshes that lie outside the geometry, and how fixed-source tallies are normalised.
2. **It does not see:**
   - an overlap that lies between its slices;
   - a wrong dimension, density or composition that is valid OpenMC;
   - a mirrored lattice, a cell filled with the wrong material, a missing part.

   Only plots compared with the drawing, and the user, catch those.
3. **Fix every error before planning a run,** and say what each warning means for this problem; never plan a run over an error.

## Overlaps

4. **Point queries cannot find overlaps.** `openmc.Geometry.find` and `openmc.lib.find_cell` return the first cell that contains the
   point; a second cell claiming the same point is never seen. Use the plots (overlaps drawn red) and the check.
5. **A geometry-debug run** tests every surface crossing and stops at the first overlap it meets: a script run whose `run.py` calls
   `openmc.run(geometry_debug=True, particles=...)` on a small size. Use it when the plots are clean but the model is suspect (a
   complicated region, a library-built geometry, a CAD file). It may crash after printing its overlap lines; the lines are the
   result.
6. **Slices that lie on a surface** show points in two cells; the check redraws them a little to either side and keeps only overlaps
   that remain, reporting the rest as "lies on a surface" (`working_examples/cad-geometry`, the teapot's flat top).
7. **CAD overlaps with the implicit complement** usually mean touching volumes that were not imprinted and merged: no volume crosses
   another, and the fix is in the CAD export (`openmc-authoring/rules/cad-and-meshes.md`; `working_examples/cad-geometry`, "Using
   the tokamak"). The check then names the pairs of volumes. It matches faces triangle for triangle, so it cannot see touching
   faces that were faceted apart; overlaps that remain in a file it finds merged are crossing volumes, or such faces.

## The parts of the model

8. **Plot it and compare with the description:** every dimension, every material in its place, lattices row by row
   (`openmc-authoring/rules/plots.md`). Show the user the plots and say what to look for.
9. **Symmetry and cuts:** a quarter or half model needs reflecting planes at its cuts. A leakage far above what the device should
   have, or a source that fills only part of the space, points there (`working_examples/cad-geometry`: the tokamak half leaked 81%
   without them, 18% with).
10. **Record what built it:** for a model made by a library (a builder, CoreForge for the TRIGA) note the library's commit and the
    known fixes it carries. A model built from an older commit still carries the bugs fixed since, and runs without complaint.
