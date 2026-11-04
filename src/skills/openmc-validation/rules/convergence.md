# Convergence of the fission source

Derived from: the OpenMC 0.15.3 user's guide, settings page (Shannon entropy), and the eigenvalue methods page (MIT). Written for
AutoMC from the examples' runs.

1. **An eigenvalue run's tallies count only after the fission source has settled.** The inactive batches are for that; a tally of
   active batches taken while the source still moves is biased, whatever its error bar says.
2. **Watch the Shannon entropy.** Give the model an entropy mesh over the fissile region (`openmc.Settings.entropy_mesh`); after the
   run, `openmc.StatePoint.entropy` has one value per batch. It should rise or fall during the inactive batches and then stay flat.
   `working_examples/jezebel` compares the first and second halves of the active batches and calls the source converged when they
   agree within their scatter; read the entropy with PythonRunTool the same way. The run reader shows k by batch, not the entropy.
3. **k settles before the source does.** A flat k by batch is not proof: k is an integral and forgives a source that is still
   tilting.
4. **Large, loosely coupled systems converge slowly.** When the dominance ratio (the second eigenvalue over the first) is near 1, a
   tilt in the source fades by only a little each batch. The C API example's 3 m pin has a ratio near 0.98 and needed far more
   inactive batches than given: its first run showed lumpy, lopsided heat profiles that were noise. A 1 m section (about 0.88)
   settled in 30 (`working_examples/c-api`). Tall cores, long pins, large reflected systems and weakly coupled units behave so.
5. **Signs of an unconverged source:**
   - an entropy still drifting into the active batches;
   - a symmetric model whose results are not symmetric;
   - axial or radial shapes that change from one run to the next more than their errors allow.
6. **Remedies, in order:**
   - more inactive batches;
   - a first source spread over all the fuel (`openmc-authoring/rules/sources.md`);
   - more particles per batch;
   - a smaller section of the system, if the problem allows it and the user agrees.

   Say in the result that the source was checked, and how.
7. **Fixed-source runs have no source to converge,** but their batches must still be independent and their tallies well sampled
   (`rules/statistics.md`).
