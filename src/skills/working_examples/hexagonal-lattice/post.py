"""
Read back a built hexagonal lattice and count what is in it: the rings, the positions, each pin kind, and which positions the large
pins take.

    python post.py CASE_FOLDER

No run is needed (the model is geometry only). Prints the counts and one last line `RESULTS {json}` for scripts and tests.
"""
import argparse
import json
import os

import openmc


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("case_folder")
    a = parser.parse_args()
    model = openmc.Model.from_model_xml(os.path.join(a.case_folder, "model.xml"))
    lattice = next(iter(model.geometry.get_all_lattices().values()))

    def kind(universe):
        """A pin's kind by its fuel's material name (universe names are not kept in model.xml; material names are)."""
        fuels = [c.fill.name for c in universe.cells.values() if isinstance(c.fill, openmc.Material) and "fuel" in c.fill.name]
        return {"U-238 fuel": "large pin (U-238)", "U-235 fuel": "small pin (U-235)"}.get(fuels[0] if fuels else "", "other")

    counts = {}
    for ring in lattice.universes:
        for u in ring:
            counts[kind(u)] = counts.get(kind(u), 0) + 1
    n_rings = lattice.num_rings
    positions = sum(len(r) for r in lattice.universes)
    print(f"{n_rings} rings, {positions} positions, orientation {lattice.orientation}, pitch {lattice.pitch[0]} cm")
    for name, n in sorted(counts.items()):
        print(f"  {name}: {n}")
    print(lattice.show_indices(num_rings=n_rings, orientation=lattice.orientation))
    out = {"rings": n_rings, "positions": positions, "large_pins": counts.get("large pin (U-238)", 0),
           "small_pins": counts.get("small pin (U-235)", 0), "orientation": lattice.orientation,
           "expected_positions": 1 + 3 * n_rings * (n_rings - 1)}
    print("RESULTS " + json.dumps(out))


if __name__ == "__main__":
    main()
