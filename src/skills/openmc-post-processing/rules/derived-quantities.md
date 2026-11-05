# Ratios, sums and other derived numbers

Derived from: the OpenMC 0.15.3 user's guide, processing page (MIT), and the tally arithmetic in `openmc/tallies.py` (`hybrid_product`
and its documentation). Written for AutoMC.

1. **A derived number carries an error too.** For independent values, relative errors add in quadrature for products and ratios,
   and absolute errors for sums and differences.
2. **Tallies of one run are not independent:** the same histories score them all. OpenMC's tally arithmetic (`+`, `-`, `*`, `/`
   between `openmc.Tally` objects, and `openmc.Tally.summation`) propagates errors as if they were, and its documentation warns that
   the error can then come out too small. It can come out too large as well: in `working_examples/tally-arithmetic` the k from
   production over losses carried a larger error than OpenMC's own k, because the two parts rise and fall together. Call a
   propagated error approximate. When it decides a conclusion, compare it with the spread of a few runs that differ only in their
   seed.
3. **Microscopic cross sections:** a reaction rate over the flux over the atom density (atoms per barn-cm) gives barns, for the same
   cell and energy range (`working_examples/tally-arithmetic`). The rate and the flux must come from the same filter bins.
4. **Ratios of reactions** (capture to fission of U-235, conversion ratios): score both on the same filter and nuclide, and divide
   (`working_examples/pincell`). A ratio of two scores of one tally shares its histories; say that its error is approximate.
5. **Balances as checks:** production over absorption plus leakage is another estimate of k and should agree with OpenMC's within
   the errors; the six factors of the six-factor formula multiply back to it exactly, because their numerators and denominators cancel
   in turn (`working_examples/tally-arithmetic`).
6. **Peaking factors** (the largest bin over the mean) read high at low statistics: `openmc-validation/rules/statistics.md` 5.
7. **Normalisation** to a source strength or a power comes before any physical comparison: `openmc-authoring/rules/normalization.md`.
   Reactivity, worths and dollars: the post-processing skill itself.
8. **Write the arithmetic in `post.py`,** with the units of every result in its name or its comment, and print one `RESULTS` line of
   JSON as the examples do, so that a later run can be checked against it.
