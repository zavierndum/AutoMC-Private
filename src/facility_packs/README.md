# Facility packs

A facility pack holds one facility's data, never code that AutoMC depends on: its presets, its assumptions register, and anything the
builders need that is facility data (a table that numbers the configurations, fuel compositions, materials, tally presets on the
facility's own cells, a library profile with a facility patch). The code stays general; the data says where it comes from and who may
see it.

| Where | What | Label |
|---|---|---|
| `src/facility_packs/<pack>/` (this folder) | packs that ship with AutoMC | every item must be **public**; AutoMC refuses to load anything else here |
| the folders in `AUTOMC_FACILITY_PACKS` (separated by `:` on Linux, `;` on Windows), by default `AutoMC_restricted/facility_packs/` next to the AutoMC folder | packs with data that may not be published | **restricted**; AutoMC refuses to load a restricted pack from inside the AutoMC folder |

Shipped here: `netl_public`, the NETL TRIGA from public sources only (the pinned library's defaults, which cite the SAR, NRC ADAMS
ML23279A146), with fresh fuel and the stock 90 cm pool. Its preset is `netl_public_fresh_fuel@1`.

## How AutoMC uses packs

- A spec's `preset` names its pack. When no preset is given, the default comes from a restricted pack if one is installed for the
  facility, otherwise from a public one.
- Every other lookup (configuration table, fuel source, material, tally preset, library profile) goes to the spec's own pack first,
  then to the other installed packs of the same facility, restricted packs first. A public preset can therefore use a restricted table
  when one is installed, and the result then says so.
- Every build records in `result.json` (`facility_data`) each pack item it read, with the item's label and checksum, and an overall
  label: `restricted` if any item is. `python -m automc triga spec --views` shows the same before anything is built. No local paths
  are recorded.
- A build lists every assumption that applies to its spec, from the packs whose data it uses. An assumption id means the same topic in
  every pack of a facility (A2 is always the pool and port lengths); the spec's own pack gives the wording.

## The format (version 1)

```
<pack>/
  pack.yaml          what the pack is, its sources, and every file with a label (required)
  presets.yaml       presets (optional)
  assumptions.yaml   the assumptions register (optional)
  README.md          free text (optional, needs no label)
  ...                any data files, each listed in pack.yaml items
```

### pack.yaml

```yaml
format: 1
pack: netl_public                 # unique name: letters, digits, _ . + -
facility: netl                    # the facility the builder knows
title: one line
distribution: public              # public | restricted; restricted if any item is
default_preset: netl_public_fresh_fuel   # optional; a restricted pack's default wins over a public pack's

sources:                          # where the data comes from, each with its own label
  sar: {what: "NETL TRIGA Safety Analysis Report, NRC ADAMS ML23279A146", distribution: public}

items:                            # EVERY file of the pack (a folder item covers everything inside it)
  presets.yaml: {what: "...", sources: [sar], distribution: public}

provides:                         # optional: what the builder can take from the pack, by kind
  configurations: configurations.yaml          # {number: {pnt, three_el, description}}
  measured_worths: measured_worths.yaml        # reference values, not used by the builder
  tally_presets: tally_presets.yaml            # same keys as tools/reactors/triga/tallies.py TALLY_PRESETS
  fuel_sources: {name: data/module.py}         # a Python module: make_all_depleted_fuel_materials(temperature), GRID_LOCATION_TO_MCNP_MATERIAL
  materials: {barite_concrete: data/barite.yaml}   # {name, density_gcc, composition: [[element, number], ...]}
  library_profiles:                            # extra library profiles (snapshot folders and patches)
    name: {snapshots: [...], commits: {...}, patches: [...]}        # entries "pack:<path>" or "snapshots:<path in library_snapshots>"
  derived_snapshots:                           # how to make a snapshot of the pack: python library_snapshots/make_snapshots.py --pack DIR
    library_snapshots/<name>: {base: "snapshots:<name>", patches: [...]}
  skills: skills                               # the agent's skills from this pack: one subfolder per skill, each with a SKILL.md;
                                               # a restricted pack's skills are given only to a local model
  eval_answers: evals/answers.yaml             # {case id: {answer_all, answer_none}}: the answer phrases of the evaluation cases
                                               # marked answer_from_pack (src/evals/cases.yml), so facility values stay in the pack
  canaries: checks/canaries.yaml               # {values: [...]}: text of a restricted pack that must never appear in a public file
                                               # of AutoMC or in a prompt; the tests search for it (tests/reactors/triga/_packs.py)
```

Checks made when a pack is loaded (any failure stops AutoMC with a message naming the pack): unknown or missing keys; a file not listed
in `items`; an item listed but missing; an item labelled public that uses a restricted source; a pack labelled public that holds a
restricted item; a restricted pack in `src/facility_packs` or anywhere inside the AutoMC folder; two packs with the same name or the same
preset; two packs of one kind (public or restricted) both setting a default for the same facility.

### presets.yaml

```yaml
my_preset@1:
  description: one paragraph
  spec: {...}                     # a complete spec: every field, no defaults anywhere else
my_preset_variant@1:
  description: ...
  based_on: my_preset@1           # a preset of the same pack
  changes: {tallies: [flux_mesh]} # merged into the base field by field
```

A preset is never edited after it has been used: a change is a new version (`@2`).

### assumptions.yaml

A list; each entry has exactly `id`, `title`, `statement`, `fields` (spec fields whose values the build reports), `confirm_with`,
`status` and `applies`:

```
applies: always                              every spec
applies: {path: value, ...}                  every path equals its value (true/false only match booleans)
applies: {path: {not: value}}                the path does not equal the value
applies: {path: {in: [a, b]}}                the path is one of the values
applies: {path: {not_empty: true}}           the path holds a non-empty list
applies: {any: [{...}, {...}]}               at least one of the conditions holds
```

A path is a dotted spec field (`environment.barite`, `inserts.three_el.liner`). A path through a null field reads as null; a field that
does not exist is an error, so a typo cannot silently switch an assumption off.

## Adding a facility

A second TRIGA (for example Texas A&M's) needs two things: its geometry in the builder (a module next to `facilities/netl.py` and the
library elements it uses), which is code, and a pack for its data. Write `pack.yaml` with every source and label, a preset with every
spec field, and an assumptions register; put the pack here if everything in it is public, otherwise in a restricted folder outside
AutoMC. A new set of data for a facility the builder already knows (another core loading, another detector design) is only a pack.
