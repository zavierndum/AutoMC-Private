# Common mistakes

Derived from: the OpenMC 0.15.3 user's guide, troubleshooting page (MIT), and from what the 29 working examples met while they were
built (their READMEs tell each story). Written for AutoMC. Each line names the rule file that explains it.

## Mistakes that give no error

1. **Weight fractions entered as atom fractions,** or ppm without its basis (`rules/materials.md`).
2. **A material without a density:** its fractions become atom densities (`rules/materials.md`).
3. **Water, graphite, ZrH or polyethylene without its S(a,b) table** (`rules/materials.md`).
4. **Energies in MeV where OpenMC wants eV** (sources, filters, cutoffs): a "2 MeV" source written `2.0` is 2 eV
   (`rules/sources.md`).
5. **A lattice map entered upside down:** the first row is the top (`rules/universes-and-lattices.md`).
6. **Overlapping cells:** transport runs on in the wrong material (`rules/geometry.md`, `rules/plots.md`).
7. **A sector without reflecting planes at its cuts** (`rules/geometry.md`).
8. **Cell flux read as flux per cm2:** it is integrated over the volume (`rules/normalization.md`).
9. **Fixed-source tallies multiplied by the source rate twice:** OpenMC already scales them by the source strengths
   (`rules/normalization.md`).
10. **A reaction rate with nuclides read as a cross section** (`rules/tallies.md`).
11. **Too few inactive batches:** tallies gathered before the fission source settled (`rules/settings.md`, `openmc-validation`).
12. **A model changed during a run** through openmc.lib: two states in one k (`rules/c-api.md`).
13. **The largest of many noisy bins quoted at a small size:** it reads high (`openmc-validation`).
14. **A teaching depletion chain used for real burnup** (`rules/depletion.md`).

## Mistakes that stop the run

15. **No cross sections:** OPENMC_CROSS_SECTIONS is not set (the job service checks it) (`rules/nuclear-data.md`).
16. **A nuclide or temperature the library lacks** (`rules/materials.md`, `rules/nuclear-data.md`).
17. **Atom and weight fractions mixed in one material** (`rules/materials.md`).
18. **Lost particles:** a gap in the geometry or a lattice position without `outer` (`rules/geometry.md`,
    `rules/universes-and-lattices.md`).
19. **A source box mostly outside its constraint:** too few sites accepted (`rules/sources.md`).
20. **An eigenvalue model without fissionable material,** or a fixed-source model without a source (the model check names both).
21. **A file named by an absolute path** or outside the case: a CAD geometry, a mesh, a multigroup library (`rules/cad-and-meshes.md`,
    `rules/multigroup.md`).
22. **CAD inside CSG without `auto_geom_ids`,** or a CAD material tag with no material of that name (`rules/cad-and-meshes.md`).

When a run fails, read its error first in the error knowledge base: the tools attach the entries that match.
