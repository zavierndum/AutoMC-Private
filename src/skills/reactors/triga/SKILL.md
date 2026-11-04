---
name: TRIGA Input Builder
description: Building OpenMC models of the NETL TRIGA - presets, insert labels and hardware, configurations, rod units, run sizes, sweeps and what a build reports.
---

# reactors/triga

## What it builds
OpenMC input (model.xml) for the UT Austin NETL TRIGA from a spec: rods, conditions, fuel, environment, inserts, tallies and run
settings. It never runs transport. Every build records its spec, the library versions, the assumptions, warnings, a run-time estimate
and which facility data it used.

## Presets
A preset is a complete set of defaults; a spec changes only the fields it names. TRIGAInputSpecTool shows the resolved spec. The default
preset depends on the installed facility packs: the public one (netl_public_fresh_fuel) is built from the pinned library defaults and
the SAR, with fresh fuel, a 90 cm pool, no barite region and rods fully inserted. A restricted pack may install the facility's
reference preset and become the default; its results are labelled restricted. When the user asks for the public model, set
preset to netl_public_fresh_fuel in every tool call: the default may be a restricted preset.

## Inserts
- pnt: none, thermal, epithermal (the pneumatic transfer tube terminus at G-34).
- three_el: none, thermal, epithermal, fast (the three-element irradiator in E-11, F-13, F-14).
- A label resolves to hardware fields under inserts (terminus and wrapper of the PNT; design, liner, liner thickness, modified geometry
  of the 3-EL; what the slots hold without a 3-EL). Any field can be overridden. Several mappings are hypotheses: the assumptions
  register says which (A7, A12 to A16).
- A numbered configuration (config) exists only if an installed facility pack numbers them; otherwise give pnt and three_el.

## Rods
transient, regulating, shim_1, shim_2 (or TR, RR, S1, S2). A value is a fraction of the travel (0 inserted, 1 withdrawn) or
{value, unit} with unit fraction, percent, console (0 to 960, assumption A5), cm_withdrawn (0 to 38.1 cm) or z_cm. One number sets all
four rods. TRIGAInputSpecTool shows every rod in every unit.

## Run sizes
run.size smoke (20,000 x 40 batches, 10 inactive), standard (500,000 x 100, 20 inactive), production (5,000,000 x 150, 20
inactive), or explicit particles, batches, inactive and seed. The build's run_estimate gives an order of magnitude only.

## Sweeps
TRIGASweepBuilderTool builds one model per state (a rod at several positions, a spec field at several values, or a list of states),
all with the same random seed, then OpenMCRunStartTool with the sweep runs them as one job.

## Reporting
- With any result, name the preset, the configuration or insert labels, the rods, the run size and the assumptions that apply.
- Fresh-fuel results are not predictions for the operating core (assumption A17).
- The model's k-eff has a known bias only where a facility pack's skill states one; do not invent one.
