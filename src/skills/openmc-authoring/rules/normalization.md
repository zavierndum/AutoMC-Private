# Normalisation: from per source particle to physical units

Derived from: the OpenMC 0.15.3 user's guide, tallies page, and the energy deposition methods page (MIT). Written for AutoMC; the
fixed-source scaling below was read in OpenMC's source (`src/tallies/tally.cpp`, `Tally::accumulate`) (2026-11-04).

## What a tally holds

1. **Per source particle.** A flux tally is a track length: cm per source particle. A reaction score is reactions per source
   particle. Nothing is per second or per cm3 until you make it so.
2. **A cell or mesh-element flux is integrated over its volume.** Divide by the volume (cm3) for flux per cm2 per source particle.
   Comparing two cells' raw flux compares their volumes as much as their fluxes: a pin cell's fuel-to-moderator thermal flux ratio
   read 1.16 raw and 0.853 per volume (`working_examples/mg-mode-intro`).

## Fixed source

3. **OpenMC multiplies fixed-source tallies by the sum of the source strengths.** With every `strength` left at 1.0 the result is per
   source particle; with strengths in particles per second (an activity, a generator's output) it is already per second, and must
   not be multiplied again. Give one or the other, and say which.
4. **The usual chain:** flux (cm per source particle) x source rate (1/s) / volume (cm3) = particles per cm2 per second
   (`working_examples/point-source-sphere-dose`, which also checks the result against the exact uncollided flux).

## Eigenvalue, at a power

5. **Source rate from the power:** S (neutrons per second) = P (W) / (H x 1.602e-19 J/eV), where H is the energy deposited per
   source neutron in eV (a `"heating"`, `"kappa-fission"` or `"fission-q-recoverable"` tally over the whole model). Every other
   tally times S is then per second. `working_examples/power-normalization` checks S x H = P exactly.
6. **Which energy score:** `"fission-q-recoverable"` and `"kappa-fission"` count the energy released at fission sites (193.7 and
   193.4 MeV per fission in `working_examples/power-normalization`); `"heating"` counts what is deposited where it lands, including
   photons when photon transport is on. Name the one you used.
7. **A pin or assembly per unit length:** a power in W/cm times the model's height is the power of the model.

## Dose

8. Dose tallies come out in pSv cm3 per source particle; `rules/dose.md` turns them into a dose rate.

## Say it

9. Every quantity you report carries its units and its normalisation: "per source neutron", "per second at 1e8 n/s", "at 174 W/cm".
   A number without them cannot be checked.
