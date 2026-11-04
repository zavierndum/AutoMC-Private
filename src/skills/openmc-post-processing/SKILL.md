---
name: OpenMC post-processing
description: Reading a finished OpenMC run - k-eff and its error, active batches, worths in pcm and dollars, plausibility, tallies.
---

# openmc-post-processing

## k-eff
- RunResultReaderTool gives keff.value and keff.sigma (one standard deviation of the mean over the active batches), k by batch, the
  batch counts, speed, tallies and lost particles. Quote value +/- sigma with the number of active batches.
- Inactive batches converge the fission source and are not in the mean. If k by batch still drifts after the inactive batches, say the
  source may not have converged.
- "plausible" means finite k-eff with a positive error, finite tallies, no lost particles and no errors in the log; it is not a
  validation of the physics.

## Reactivity and worth
- The worth of going from state a to state b, in pcm: rho = 1e5 * (1/k_a - 1/k_b). Its error, treating the runs as independent:
  1e5 * sqrt((s_a / k_a^2)^2 + (s_b / k_b^2)^2). This is conservative when both runs share a random seed.
- Dollars need the effective delayed neutron fraction: rho / (1e5 * beta_eff). No value is assumed; the TRIGA assumptions register
  says which one is proposed and whether it is confirmed. Say which beta_eff you used.
- TRIGASweepResultsTool does this arithmetic for a sweep, state by state, and checks for rod sweeps that k-eff does not fall as a rod is
  withdrawn beyond twice the error of the step.

## Run size and error
- The error of k-eff falls roughly as one over the square root of the number of active histories. Four times the histories halve it.
- Before comparing two states, compare the difference with its error. A smoke-size run (20,000 x 40 batches) is a check that the model
  runs; its error is too large for most insert worths.

## Tallies
- Each tally has a bin count and a flag for finite values; a tally with no non-zero bins usually means it scored in the wrong cell or
  energy range. Large tallies make large statepoints (about 16 bytes per bin).
