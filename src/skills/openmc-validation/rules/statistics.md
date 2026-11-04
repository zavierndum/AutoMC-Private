# Statistics of a result

Derived from: the OpenMC 0.15.3 user's guide, tallies and processing pages (MIT), and the methods page on tally statistics. Written
for AutoMC from the examples' runs.

1. **Every number has an error.** OpenMC gives the mean over active batches and the standard deviation of that mean. Quote both;
   the relative error R is their ratio.
2. **A working rule for R:** below about 10% a value can be discussed, below about 5% it can be quoted, and a value compared with
   another needs an R small against the difference sought. A bin with no scores, or with scores from only a few batches, has an
   error that means nothing: say it is unsampled.
3. **The error should fall as one over the square root of the histories.** Run the quick size and the reference size: if R does not
   shrink about as sqrt(N) grows, the tally is dominated by rare events, and its error is not to be trusted yet.
4. **The figure of merit** FOM = 1 / (R^2 T), with T the run time, should stay about constant as a run grows. A FOM that drifts or
   jumps between the quick and the reference size means the tally is not yet well sampled. Compare methods by FOM only over the
   region the answer is wanted (`working_examples/shielded-room-weight-windows`).
5. **The largest of many noisy bins reads high.** The maximum of 1,056 pin powers at the quick size of the C5G7 core was 2.63 +/-
   0.05 against a reference of 2.498: the noise lifts the highest of many values above its true value. Judge peaks and peaking
   factors at a size where their error is small, or allow for the bias (`working_examples/mg-mode-intro`).
6. **Agreement between two numbers** is judged by their difference in units of the combined error, sqrt(s1^2 + s2^2): within 2 is
   agreement, beyond 3 is a difference to explain, 2 to 3 is a matter for more histories. When many bins are compared some will fall
   beyond 2 by chance (about 1 in 20); a pattern of them will not.
7. **Two runs with the same seed** and the same build agree to rounding; that tests reproducibility, not correctness. Statistical
   agreement needs different seeds.
8. **Triggers** stop a run at a chosen error (`openmc-authoring/rules/settings.md`); say which trigger stopped it, or that the
   batch limit did.
