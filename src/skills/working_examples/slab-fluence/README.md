# A neutron beam through water and concrete: the fluence profile

Derived from: the AutoMC developers' toy problem (a beam through water and concrete). Written for AutoMC.

## The problem

A beam of 2 MeV neutrons enters a 10 cm water slab and then a 20 cm concrete slab. How does the fluence change with depth, in total and
above 0.1 MeV? This is the classic shield-penetration picture: in water the beam scatters and slows (the total fluence even builds up
for a while, because slowed neutrons linger), and in concrete the fast part decays almost exponentially.

## The model

- **Slabs:** across x from 0 to 10 cm (water) and from 10 to 30 cm (concrete).
- **Faces:** the front and back faces are vacuum boundaries. The four sides reflect, so the slabs behave as if they were infinite
  across the beam.
- **Source:** a pencil beam just inside the front face, heading along +x. With reflecting sides this stands for a broad, uniform
  beam.
- **Materials:**
  - water, with bound hydrogen;
  - ordinary concrete at 2.3 g/cm3, by weight: H 1.0%, O 52.9%, Si 33.7%, Ca 4.4%, Al 3.4%, Fe 1.2%, Na 1.7%, K 1.3%, Mg 0.2%.

  These fractions are weights. The toy problem passed them to `add_element` without saying so, which OpenMC reads as atom
  fractions; here they are declared as weight fractions (`percent_type="wo"`).
- **Tallies:** a 1-D mesh of 20 bins along x scores the flux: all energies, and above 0.1 MeV with an energy filter. Each bin's value
  is the track length in the bin per source neutron. The bins have equal volumes, so they compare directly.

## Results (OpenMC 0.15.3, ENDF/B-VIII.0, reference size: 200,000 x 10 histories, 40 s here)

Values are track length per source neutron, in cm, for each 1.5 cm bin.

| Quantity | Value |
|---|---|
| first bin (0 to 1.5 cm) | 3.661 +/- 0.003 |
| last bin (28.5 to 30 cm) | 0.4589 +/- 0.0011 |
| sum over the 20 bins | 66.962 +/- 0.021 |
| fast part (above 0.1 MeV) in the concrete | falls by a factor e every 8.46 cm |

How the profile looks:
- **Build-up in the water.** The total peaks near 5 cm, at about 1.7 times the first bin, as slowed neutrons accumulate. The fast
  part only falls.
- **The concrete.** Both fall almost exponentially. The e-folding length of the fast part is the number a shielding engineer wants
  from this.
- **The last bins.** They drop faster still, because the back face is a vacuum and nothing scatters back from beyond it.

`post.py --plot profile.png` draws the profile on a log scale.

## Running it with AutoMC

1. **Build:** `OpenMCModelBuildTool` with `build.py`. Change the OPTIONS line for other thicknesses, energies or bins.
2. **Plot** with `OpenMCGeometryPlotTool` (an xy slice shows both slabs and the starred source at the front face).
3. **Check** with `OpenMCModelCheckTool`.
4. **Run** with `OpenMCRunStartTool` (the user approves).
5. **Read:** `python post.py <run folder> --plot profile.png` through `PythonRunTool`.

Sizes: quick 5,000 x 5 (about 9 s), reference 200,000 x 10 (about 40 s).

## What it teaches

- A monodirectional source.
- Reflective boundaries standing in for an infinite medium.
- A mesh tally along one axis, with an energy filter.
- Weight-fraction compositions.
- How to read a depth profile and an attenuation length.
