# Photons

Derived from: the OpenMC 0.15.3 user's guide, settings page (photon transport), and the photon physics methods page (MIT). Written for
AutoMC; defaults and the pulse-height rules were read in OpenMC's source (`src/settings.cpp`, `src/tallies/tally.cpp`) (2026-11-04).

1. **`openmc.Settings.photon_transport = True`** tracks photons: those of a photon source, and those neutrons make (capture
   gammas, inelastic gammas). Without it a neutron run follows no photons. A photon source turns it on by itself (OpenMC does so when
   it reads the source); set it anyway, so that the model says what it does. The library here has the photon data.
2. **Electrons are not transported.** `openmc.Settings.electron_treatment` is `"ttb"` by default (thick-target bremsstrahlung: the
   electrons' bremsstrahlung photons are made, the rest of their energy stays put) or `"led"` (all of it stays put).
3. **The photon energy cutoff is 1 keV by default** (`openmc.Settings.cutoff`, key `energy_photon`, in eV); below it a photon's
   energy is deposited at once.
4. **Splitting neutron and photon results:** add an `openmc.ParticleFilter` (`["neutron", "photon"]`); dose needs each particle's
   own coefficients (`rules/dose.md`). With photon transport on, `"heating"` is scored by collisions and includes photon deposition.
5. **A detector's spectrum is a `"pulse-height"` score:** the energy deposited in a cell per source particle, binned by an
   `openmc.EnergyFilter`. OpenMC requires photon transport for it and allows only a cell filter and an energy filter with it. The
   detector's resolution is applied afterwards, in post-processing (`working_examples/gamma-detector`: the photopeaks, the Compton
   edge and a Gaussian resolution).
6. **Source lines:** a photon source is `particle="photon"` with its energies in eV (`rules/sources.md`); its strength in photons per
   second makes every tally per second (`rules/normalization.md`).
