# Comparing with a benchmark, another code or a published model

Derived from: the OpenMC 0.15.3 user's guide, troubleshooting page (MIT), and what the working examples and AutoMC's TRIGA work met.
Written for AutoMC.

## Against a benchmark

1. **Use both uncertainties.** A benchmark's value carries its own (Jezebel's k is 1.0000 +/- 0.0020); the difference in combined
   sigma is (k - k_b) / sqrt(s^2 + s_b^2). The quick run's -126 pcm is -0.39 combined sigma; the reference run's -18 pcm is well
   within (`working_examples/jezebel`).
2. **Model the benchmark's model,** not the experiment: a benchmark specification simplifies the real assembly on purpose
   (Jezebel's homogeneous sphere), and its value applies to that simplified model.
3. **Prefer an exact check when one exists:** an analytic value (the uncollided flux at a distance, `working_examples/point-source-sphere-dose`),
   a sum rule, an identity (power = source rate x energy per source neutron, `working_examples/power-normalization`), or a CSG twin of
   a CAD model (`working_examples/cad-geometry`).

## Against another code or a published result

4. **Make it the same model first.** Most large differences between two codes are differences between two models: a missing part, a
   different density, a library bug in one version. Before blaming the codes, compare dimensions, materials, temperatures and
   S(a,b) tables item by item, and record the builder's version (`rules/before-run.md`).
5. **Same data or not:** results from ENDF/B-VII.1 and ENDF/B-VIII.0 can differ by hundreds of pcm on the same model. The OpenMC
   notebooks' printed values came from older data; the examples quote them for context and test against their own reference runs.
   Say which library each side used.
6. **Same physics settings:** temperatures, S(a,b), photon transport, multigroup or continuous energy. A multigroup result is
   compared with continuous energy on the problem its library was made for (`working_examples/mgxs-library-mg-mode`: +109 pcm, 2.0
   sigma).
7. **A known bias is part of the result.** When a model is known to differ from measurement by an amount, report it with the
   result, and never hand on a bare number (a k without its bias, its library and its assumptions).

## Saying it

8. Report the difference in pcm (or percent) and in combined sigma, the sizes and libraries of both sides, and what the difference
   is attributed to, marked as verified or not. "Agrees within 0.4 sigma" and "differs by 6 sigma, not yet explained" are both
   results; "close" is not.
