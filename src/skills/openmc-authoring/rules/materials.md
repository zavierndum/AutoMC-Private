# Materials

Derived from: the OpenMC 0.15.3 user's guide, materials and nuclear data pages (MIT). Written for AutoMC; every API fact below was checked
in the installed openmc package and library (2026-11-04).

## Composition

1. **Say what the fractions are.** `openmc.Material.add_nuclide` and `openmc.Material.add_element` take a fraction and `percent_type`:
   `"ao"` (atom, the default) or `"wo"` (weight). Fractions are relative; OpenMC normalises them. A table given in weight percent
   and entered as atom fractions is silently wrong: the developers' toy concrete was, until it was declared `"wo"`
   (`working_examples/slab-fluence`).
2. **Never mix the two in one material.** Python accepts it and writes the XML, but OpenMC stops when it loads the model ("Cannot mix
   atom and weight percents"). Convert one set first.
3. **Elements expand to their natural isotopes** from the library's natural abundances. ENDF/B-VIII.0 has no natural carbon: `C`
   becomes C12 and C13. Use `add_element` unless the problem fixes the isotopes.
4. **Uranium enrichment:** `add_element("U", 1.0, enrichment=4.95)` makes U-235 4.95% of the uranium **by weight**, with U-234 and
   U-236 in their usual proportion to it. For enrichment of another element use `enrichment_target` and `enrichment_type`.
   Explicit isotopes, as in `working_examples/pincell`, give atom percent instead: say which one the problem means.
5. **Parts per million need a basis.** A "ppm" of boron may be by weight of the water or atoms per molecule; the two differ by about
   1.7x for boron in water. `working_examples/criticality-search` declares boron by weight and shows the conversion.
6. **Mixtures:** `openmc.Material.mix_materials(materials, fracs, percent_type)` takes `"ao"`, `"wo"` or `"vo"` (volume) fractions.

## Density

7. **Every material needs `openmc.Material.set_density`.** Units: `g/cm3` (or `g/cc`), `kg/m3`, `atom/b-cm`, `atom/cm3`, `sum`
   and `macro`. A material created without it has units `sum`: its fractions are read as atom densities in atom/b-cm. The OpenMC
   power-normalisation notebook's water had no density and came out about 30 times too dense (`working_examples/power-normalization`
   README).
8. **`sum` on purpose:** give each nuclide's atom density (atom/b-cm) and call `set_density("sum")`, as a benchmark specification
   gives them (`working_examples/jezebel`).
9. **`macro`** is for multigroup materials only (`rules/multigroup.md`).

## Thermal scattering and temperature

10. **Bound hydrogen needs its S(a,b) table**, or thermal neutrons scatter off free atoms and the spectrum is wrong:
    `openmc.Material.add_s_alpha_beta`. Names in this library: `c_H_in_H2O` (light water), `c_D_in_D2O` and `c_O_in_D2O` (heavy
    water), `c_Graphite`, `c_H_in_ZrH` and `c_Zr_in_ZrH` (TRIGA fuel), `c_H_in_CH2` (polyethylene), `c_Be`, `c_O_in_UO2` and
    `c_U_in_UO2`, among 34. The model check names a table that is not in the library.
11. **Temperatures** go on the material (`openmc.Material.temperature`) or the cell (`openmc.Cell.temperature`), in kelvin. The
    library holds data at a few temperatures only: neutron data at 250, 294, 600, 900, 1200 and 2500 K, and each S(a,b) table its
    own set (light water 284 to 800 K, graphite 296 to 2000 K, ZrH 296 to 1200 K). OpenMC takes the nearest one within 10 K by
    default and stops otherwise; between them set `openmc.Settings.temperature` to `{"method": "interpolation"}`
    (`working_examples/c-api` changes temperatures).

## What else a material may need

12. **A volume** (`openmc.Material.volume`, cm3) for depletion and for any per-volume result; `rules/volume.md` says how to get one.
13. **`openmc.Material.depletable = True`** for depletion (it is False by default).
14. **A name** that means something: CAD files find their materials by name (`rules/cad-and-meshes.md`), and results are read by it.
