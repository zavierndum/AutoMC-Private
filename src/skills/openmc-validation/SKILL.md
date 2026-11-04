---
name: OpenMC validation
description: The review gate around every run; is the model the one meant before it runs, and is the answer converged, precise and fairly compared after.
---

# openmc-validation

## When to use
Before planning the first run of a model, and before reporting any number from a run. Read the rule files with SkillLookupTool,
`file` set to the path below.

## Before a run
- Plotted in the planes that show the model, and compared with the description; the plots shown to the user.
- The model check passed: every error fixed, every warning explained for this problem.
- The source inside the geometry; symmetry planes where the model is cut; the library and the builder's version recorded.
- For a suspect geometry, a geometry-debug run at a small size.

## After a run
- Eigenvalue: the fission source converged (the Shannon entropy flat over the active batches), not only k.
- Each quoted value with its error and units; relative errors small enough for the use; peaks judged at a size where their error is
  small.
- Compared fairly: both uncertainties, the same model, the same data, the same physics.

## Rule files (`openmc-validation/rules/...`)
| Question | File |
|---|---|
| what the model check sees and misses; overlaps; cuts; builder versions | `before-run.md` |
| the fission source: entropy, dominance ratio, inactive batches | `convergence.md` |
| relative errors, the figure of merit, biased maxima, agreement | `statistics.md` |
| benchmarks, other codes, published models | `comparing.md` |
