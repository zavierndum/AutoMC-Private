# Plots of the geometry

Derived from: the OpenMC 0.15.3 user's guide, plots page (MIT). Written for AutoMC; plot attributes checked in the installed package
(2026-11-04).

1. **Draw every model before its first run** with OpenMCGeometryPlotTool: slices through the middle and through the source, in the
   planes that show its structure (`xy`, `xz`, `yz`), coloured by material and by cell. Overlaps are marked red and the source
   points starred. Look at them, and tell the user what to look for.
2. **Width and pixels:** a slice must be wide enough to show the whole model and fine enough to show its thinnest layer (a cladding
   0.06 cm thick needs pixels smaller than that). Draw a close-up of small parts.
3. **Compare with the drawing or the description,** dimension by dimension, and lattices row by row
   (`rules/universes-and-lattices.md`). A model that runs is not a model that is right.
4. **A slice exactly on a surface** shows two cells at once; the tool redraws such a slice a little to either side and keeps only
   overlaps that remain (`working_examples/cad-geometry`, the teapot's flat top).
5. **Overlaps the plot cannot show** lie between the slices. Plots at several positions and the model check together catch most; the
   validation skill says what else to do (`openmc-validation`).
6. **In OpenMC itself,** an `openmc.Plot` has `basis`, `origin`, `width`, `pixels`, `color_by`, `colors`, `show_overlaps` and
   `overlap_color`; `openmc.Model.plot_geometry` runs OpenMC in plot mode and `openmc.Universe.plot` draws a slice with matplotlib.
   Within AutoMC, use the tool: it records the plots in the case.
