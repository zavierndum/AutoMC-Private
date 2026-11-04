"""
TRISO fuel particles packed at random into a graphite box, then sorted into a coarse lattice so that particle tracking stays fast.

Derived from: the OpenMC notebook "Modeling TRISO Particles" (openmc-notebooks, tag v0.15.0; the layer radii, compositions and packing
fraction). Written for AutoMC; README.md explains each step.

    python build.py                           the defaults below; writes model.xml (geometry only: run_mode plot) and centers.json
    python build.py --packing 0.2 --seed 7

Nothing here runs transport.
"""
import argparse
import json

import openmc
import openmc.model

# The choices of this example: edit them here, or give them on the command line.
OPTIONS = {"packing": 0.30, "box_cm": 1.0, "seed": 124848351, "lattice": 3}

# kernel, buffer, inner PyC, SiC and outer PyC: outer radius of each layer, micrometres
LAYERS_UM = [("kernel", 215.0), ("buffer", 315.0), ("inner PyC", 350.0), ("SiC", 385.0), ("outer PyC", 425.0)]


def materials():
    kernel = openmc.Material(name="UCO kernel")
    kernel.add_nuclide("U235", 4.6716e-2)
    kernel.add_nuclide("U238", 2.8697e-1)
    kernel.add_nuclide("O16", 5.0e-1)
    kernel.add_element("C", 1.6667e-1)
    kernel.set_density("g/cm3", 10.5)

    def carbon(name, density):
        m = openmc.Material(name=name)
        m.add_element("C", 1.0)
        m.set_density("g/cm3", density)
        m.add_s_alpha_beta("c_Graphite")
        return m

    sic = openmc.Material(name="silicon carbide")
    sic.add_element("C", 0.5)
    sic.add_element("Si", 0.5)
    sic.set_density("g/cm3", 3.2)
    return {"kernel": kernel, "buffer": carbon("porous carbon buffer", 1.0), "inner PyC": carbon("inner pyrolytic carbon", 1.9),
            "SiC": sic, "outer PyC": carbon("outer pyrolytic carbon", 1.87), "matrix": carbon("graphite matrix", 1.1995)}


def particle_universe(mats):
    """Concentric spheres, the outermost layer open to infinity (the particle's own outer surface bounds it where it is placed)."""
    radii = [r * 1e-4 for _, r in LAYERS_UM]
    spheres = [openmc.Sphere(r=r) for r in radii[:-1]]
    cells = []
    for i, (name, _) in enumerate(LAYERS_UM):
        if i == 0:
            region = -spheres[0]
        elif i == len(LAYERS_UM) - 1:
            region = +spheres[-1]
        else:
            region = +spheres[i - 1] & -spheres[i]
        cells.append(openmc.Cell(name=name, fill=mats[name], region=region))
    return openmc.Universe(name="TRISO particle", cells=cells), radii[-1]


def build(packing=0.30, box_cm=1.0, seed=124848351, lattice=3):
    mats = materials()
    particle, outer = particle_universe(mats)
    half = box_cm / 2.0
    box = openmc.model.RectangularParallelepiped(-half, half, -half, half, -half, half, boundary_type="reflective")
    # random close packing without overlaps, at the given volume fraction
    centers = openmc.model.pack_spheres(radius=outer, region=-box, pf=packing, seed=seed)
    particles = [openmc.model.TRISO(outer, particle, c) for c in centers]
    # a coarse lattice over the box: each element only lists the particles that touch it, so finding a cell stays cheap
    pitch = (box_cm / lattice,) * 3
    grid = openmc.model.create_triso_lattice(particles, (-half, -half, -half), pitch, (lattice,) * 3, mats["matrix"])
    compact = openmc.Cell(name="fuel compact", fill=grid, region=-box)
    with open("centers.json", "w", encoding="utf-8") as fh:
        json.dump({"radius_cm": outer, "box_cm": box_cm, "packing_target": packing, "centers": [list(map(float, c)) for c in centers]}, fh)

    settings = openmc.Settings()
    settings.run_mode = "plot"
    return openmc.Model(geometry=openmc.Geometry([compact]), materials=openmc.Materials(list(mats.values())), settings=settings)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--packing", type=float, default=OPTIONS["packing"])
    parser.add_argument("--box-cm", type=float, default=OPTIONS["box_cm"])
    parser.add_argument("--seed", type=int, default=OPTIONS["seed"])
    parser.add_argument("--lattice", type=int, default=OPTIONS["lattice"])
    a = parser.parse_args()
    build(a.packing, a.box_cm, a.seed, a.lattice).export_to_model_xml()


if __name__ == "__main__":
    main()
