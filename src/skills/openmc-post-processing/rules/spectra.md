# Energy spectra

Derived from: the OpenMC 0.15.3 user's guide, tallies page (MIT), with `openmc.EnergyFilter` and `openmc.mgxs.GROUP_STRUCTURES` read
in the installed package (2026-11-04). Written for AutoMC.

1. **A bin's flux depends on the bin's width.** To draw a spectrum, or compare two on different grids, divide each bin by its width:
   - per unit lethargy, by ln(E_high / E_low): the usual choice for a log energy axis, on which equal areas are equal flux;
   - per eV, by E_high - E_low.

   Say which (`working_examples/flux-spectrum`).
2. **`openmc.EnergyFilter.lethargy_bin_width` is a base-10 width,** log10(E_high / E_low), not the lethargy width. Flux divided by it
   is 2.303 times the flux per unit lethargy: the shape is right and the numbers are not. Use `numpy.log` of the edge ratio when the
   values are quoted or compared with another code.
3. **A grid that starts at 0 eV** (SHEM-361) has an infinite lethargy width in its first bin. Leave that bin out of a per-lethargy
   plot, or give it a small lower edge and say so (`working_examples/flux-spectrum`).
4. **Shares and totals use the flux per bin,** not the flux per unit width: the thermal share is the sum of the bins below the
   cut-off (0.625 eV is usual) over the sum of all. Two grids over the same range give the same total within its error, which is a
   check of the tally (`working_examples/flux-spectrum`: SHEM-361 and an equal-lethargy grid agree to 1e-4).
5. **Draw bins as steps** (one value across each bin, `where="post"` on the lower edges) on a log energy axis. A point at the bin's
   centre suggests a resolution the tally does not have.
6. **Named group structures:** `openmc.mgxs.GROUP_STRUCTURES` holds 23 (CASMO, SHEM-361, VITAMIN-J, CCFE-709 and others);
   `openmc.EnergyFilter.from_group_structure` makes a filter from one by name. Energies are in eV.
7. **Checks of a thermal spectrum:** in water at room temperature the flux per unit lethargy peaks near 2kT, about 0.05 eV; the fuel's
   flux dips at U-238's resonances (6.67 eV the first large one) (`working_examples/flux-spectrum`).
8. **A pulse-height spectrum** counts pulses per bin of deposited energy, per source particle (or per second when the source
   strength is a rate). It is a distribution of events, not a flux: divide by the bin width in keV for counts per keV (the 1 keV bins
   of `working_examples/gamma-detector` need nothing), never by lethargy (`openmc-authoring/rules/photons.md`).
