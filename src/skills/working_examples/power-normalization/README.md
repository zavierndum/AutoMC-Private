# From per source neutron to per second: normalizing to a power

Derived from: the OpenMC notebook "Tally Power Normalization" (openmc-notebooks, tag v0.15.0; the sphere, the reflector, the scores
and the 30 kW power). Written for AutoMC.

## The problem

OpenMC reports every tally per source neutron. A reactor engineer wants fissions per second, or a flux in neutrons/cm2/s, at the
reactor's power. Here the reactor is a 6.5 cm sphere of U-235 metal (10 g/cm3) in a water reflector out to 15 cm, at 30 kW.

## The normalization

If H is the energy released per source neutron (J) and P the power (W), the reactor makes S = P / H source neutrons per second.
Any tally T (per source neutron) then becomes T · S per second.

H comes from a heating score:

- **`fission-q-recoverable`:** the energy released by fission that stays in the reactor (neutrinos excluded);
- **`kappa-fission`:** the same idea from the evaluated data's own energy release.

Both are in eV per source neutron. `post.py` divides by 1.602e-19 J/eV.

## Results (OpenMC 0.15.3, ENDF/B-VIII.0)

| | quick (2,000 x 50 active, 5 s) | reference (20,000 x 200 active, 33 s) |
|---|---|---|
| k-eff | 0.69299 +/- 0.00220 | 0.68990 +/- 0.00041 |
| fissions per source neutron | 0.27388 | 0.27213 |
| energy per source neutron | 5.306e7 eV | 5.272e7 eV |
| source neutrons per second at 30 kW | 3.53e15 | 3.55e15 |
| fissions per second | 9.665e14 | 9.665e14 |
| energy per fission, fission-q-recoverable | 193.7 MeV | 193.7 MeV |
| energy per fission, kappa-fission | 193.4 MeV | 193.4 MeV |

- **The fission rate per second** depends only on the power and the energy per fission, so it is the same at both sizes.
- **The common 200 MeV per fission** would overstate the power by 3%.
- **About the notebook's k-eff of 0.994.** The notebook never set the water's density. `add_components({"H": 2, "O": 1})` with no
  density makes OpenMC read those fractions as atom densities: 2 atoms per barn-cm of hydrogen, about 30 times real water. Here the
  water is 1.0 g/cm3, and the sphere is well subcritical.

## Running it with AutoMC

1. **Build:** `OpenMCModelBuildTool` with `build.py`.
2. **Run:** `OpenMCRunStartTool`, approved by the user.
3. **Read:** `python post.py <run folder> --power-W 30000`.

The same steps normalize any eigenvalue model: tally a heating score over the whole problem, then divide the power by it.

## What it teaches

- What "per source neutron" means.
- `fission-q-recoverable` and `kappa-fission`.
- The factor S = P / H.
- Why a material needs a density.
