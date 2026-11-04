# Sources

Derived from: the OpenMC 0.15.3 user's guide, settings page (external sources) and decay sources page (MIT). Written for AutoMC;
API facts checked in the installed openmc package (2026-11-04).

## An independent source

1. **`openmc.IndependentSource(space=..., angle=..., energy=..., strength=..., particle=...)`**, put in `openmc.Settings.source`
   (one source or a list). `particle` is `"neutron"` (the default) or `"photon"`.
2. **Where:** `openmc.stats.Point` (a point), `openmc.stats.Box` (uniform in a box), `openmc.stats.CartesianIndependent`,
   `openmc.stats.SphericalIndependent` and `openmc.stats.CylindricalIndependent` (a distribution per coordinate),
   `openmc.stats.MeshSpatial` (by mesh element), `openmc.stats.PointCloud` (listed points).
3. **Which direction:** isotropic unless said (`openmc.stats.Isotropic`); a beam is `openmc.stats.Monodirectional` along its unit
   vector (`working_examples/slab-fluence`); `openmc.stats.PolarAzimuthal` for anything in between.
4. **What energy, in eV:** `openmc.stats.Discrete` (lines), `openmc.stats.Watt` (a fission spectrum; its defaults, a = 0.988 MeV and
   b = 2.249 per MeV, written in eV units, are U-235's for thermal fission), `openmc.stats.Maxwell`, `openmc.stats.Uniform`,
   `openmc.stats.Tabular`, `openmc.stats.Normal`, `openmc.stats.Mixture`. A 2 MeV source is `2.0e6`, not `2.0`
   (`working_examples/point-source-sphere-dose` has both a Watt and a single-energy variant).

## Constraints and rejection

5. **`constraints`** narrows what is sampled: `{"fissionable": True}` (eigenvalue starting sources: only sites in fissionable
   material), `{"domains": [cells, materials or universes]}`, `energy_bounds`, `time_bounds`, and `rejection_strategy`
   (`"resample"` or `"kill"`). The older `only_fissionable` of `openmc.stats.Box` and the `domains` argument still work but are
   deprecated.
6. **Draw the sampling volume close to the target.** A site outside the constraint is drawn again, and the run stops when fewer than 1
   in 20 are accepted. The CAD teapot's water filled 1.8% of its bounding box, so the example draws the box around the water
   instead (`working_examples/cad-geometry`). A source box that pokes a little out of the geometry is fine
   (`working_examples/candu`).
7. **An eigenvalue run's source is only a first guess,** spread over the fissile region; the inactive batches converge it
   (`rules/settings.md`).

## Several sources and their strengths

8. **`strength` weights the sources against each other** for sampling. In fixed-source mode it also sets the units of every tally
   (`rules/normalization.md`): give strengths in particles per second when the answer is wanted per second
   (`working_examples/gamma-detector`: an 800 keV beam and a Cs-137 source with its activity).

## Other kinds

9. **Decay photons:** `openmc.data.decay_photon_energy` gives a nuclide's photon spectrum and `openmc.Material.get_decay_photon_energy`
   a material's (as a distribution whose integral is the photon rate); both need a depletion chain with decay data
   (`openmc.config["chain_file"]`). `working_examples/gamma-detector` writes Cs-137's line by hand and says how.
10. **`openmc.FileSource`** replays sites from a source file (a surface source written by another run); **`openmc.CompiledSource`**
    loads a compiled C++ source; **`openmc.MeshSource`** gives each mesh element its own source.
