# Dose

Derived from: the OpenMC 0.15.3 Python API page for `openmc.data` (dose coefficients) and the tallies page (functional expansions and
energy functions) (MIT). Written for AutoMC; the function's arguments and units were checked in the installed package (2026-11-04).

1. **Coefficients:** `openmc.data.dose_coefficients(particle, geometry="AP", data_source="icrp116")` returns energies (eV) and
   effective-dose coefficients (pSv cm2). `particle` is `"neutron"`, `"photon"`, `"electron"` or `"positron"` (`"photon kerma"` gives
   Sv/Gy instead); `geometry` is the irradiation geometry of ICRP 116: `"AP"`, `"PA"`, `"LLAT"`, `"RLAT"`, `"ROT"` or `"ISO"`;
   `data_source` is `"icrp116"` or `"icrp74"`.
2. **The tally:** a `"flux"` score with an `openmc.EnergyFunctionFilter(energies, coefficients)` and a cell (or mesh) filter for the
   place the dose is wanted (`working_examples/point-source-sphere-dose`). The filter interpolates the coefficients
   (`interpolation`, `"linear-linear"` by default; `"log-log"` and others are offered).
3. **Units:** the result is pSv cm3 per source particle. Divide by the cell's volume (cm3) and multiply by the source rate (1/s): pSv
   per second. Times 3600 is pSv per hour; times 3.6e-3 is microsievert per hour.
4. **Choose and state the quantity.** These are effective-dose coefficients for a whole body in a given geometry, not ambient dose
   equivalent at a point; AP is the usual conservative choice for a person facing the source, ISO for a field from all directions. Ask
   the user if the problem does not say, and say which you used.
5. **Neutrons and photons separately:** with photon transport on, use two tallies, each with an `openmc.ParticleFilter` and its
   particle's coefficients, and add the results (`rules/photons.md`).
6. **The detector cell** is a region of air or tissue; a tally in vacuum or in a void cell still counts track length and gives the
   free-field dose, which is often what is wanted. Say which.
