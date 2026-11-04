"""
A BWR-style 8 x 8 assembly with gadolinia-bearing pins and a cruciform control blade along two of its sides, mirrored all round, with
two two-group MGXS libraries over a 10 x 10 mesh: one isotropic, one angle-dependent (8 azimuthal bins), both with scattering as
11-bin histograms in angle. run.py runs it in continuous energy, then in multigroup mode with each library.

Derived from: the OpenMC notebook "Multigroup Mode Part III: Advanced Feature Showcase" (openmc-notebooks, tag v0.15.0; the assembly,
its materials and blade, the mesh, the histogram scattering and the angle-dependent library). Written for AutoMC; README.md explains
why angle dependence matters here.

    python build.py                          writes model.xml (the CE model with both libraries' tallies)
    python run.py                            CE run and two MG runs: a script run, approved by the user

Nothing here runs transport.
"""
import argparse

import openmc
import openmc.mgxs as mgxs

# The choices of this example: edit them here, or give them on the command line. run.py reads them from here too.
OPTIONS = {"size": "quick"}

FUEL_R, CLAD_R, PITCH, NP = 0.53213, 0.61341, 1.6256, 8
LENGTH = (NP + 2) * PITCH                              # the assembly plus a pitch of water (and blade) on each side
ROD_THICK = 0.47752 / 2 + 0.14224
ROD_SPAN = 7 * PITCH
SIZES = {"quick": {"particles": 2_000, "batches": 80, "inactive": 20},
         "reference": {"particles": 10_000, "batches": 320, "inactive": 20}}
TYPES = ["total", "absorption", "nu-fission", "fission", "nu-scatter matrix", "multiplicity matrix", "chi"]


def libraries(geometry, mesh):
    """The isotropic and the angle-dependent libraries over `mesh`; build.py adds their tallies, run.py loads them."""
    out = {}
    for name, azimuthal in (("isotropic", 1), ("angle-dependent", 8)):
        lib = mgxs.Library(geometry)
        lib.energy_groups = mgxs.EnergyGroups([0.0, 0.625, 20.0e6])
        lib.mgxs_types = TYPES
        lib.domain_type = "mesh"
        lib.domains = [mesh]
        lib.correction = None                      # a correction has no meaning for histogram scattering
        lib.scatter_format = "histogram"
        lib.histogram_bins = 11
        lib.num_azimuthal = azimuthal
        lib.build_library()
        out[name] = lib
    return out


def build_with_libraries(size="quick"):
    def material(name, density, parts, enrichment=None):
        m = openmc.Material(name=name)
        m.set_density("g/cm3", density)
        for el, f in parts:
            m.add_element(el, f, enrichment=enrichment) if el == "U" else m.add_element(el, f)
        return m
    fuel = material("fuel", 10.32, [("O", 2), ("U", 1)], enrichment=3.0)
    gad = material("gadolinia fuel", 10.23, [("O", 2), ("U", 1), ("Gd", 0.02)], enrichment=3.0)
    zirc = material("zircaloy", 6.55, [("Zr", 1)])
    water = material("boiling water", 0.6, [("H", 2), ("O", 1)])
    b4c = material("boron carbide", 0.7 * 2.52, [("B", 4), ("C", 1)])          # 70% of theoretical density
    steel = material("steel", 7.75, [("Fe", 1)])

    x = lambda v, **k: openmc.XPlane(v, **k)
    y = lambda v, **k: openmc.YPlane(v, **k)
    gx0, gx1 = x(0.0, boundary_type="reflective"), x(LENGTH, boundary_type="reflective")
    gy0, gy1 = y(0.0, boundary_type="reflective"), y(LENGTH, boundary_type="reflective")
    ax0, ax1, ay0, ay1 = x(PITCH), x(LENGTH - PITCH), y(PITCH), y(LENGTH - PITCH)
    top_y, top_x1 = y(LENGTH - ROD_THICK), x(ROD_SPAN)
    left_x1, left_y0, left_y1 = x(ROD_THICK), y(LENGTH - ROD_SPAN), y(9 * PITCH)
    everything = +gx0 & -gx1 & +gy0 & -gy1
    assembly_box = +ax0 & -ax1 & +ay0 & -ay1
    top_blade = +top_y & -gy1 & +ax0 & -top_x1                  # B4C wing along the top
    left_blade = +left_y0 & -left_y1 & +gx0 & -left_x1         # B4C wing down the left side
    corner = (+left_y1 & -top_y & +gx0 & -left_x1) | (+gx0 & -ax0 & +top_y & -gy1)    # the blade's steel centre

    fr, cr = openmc.ZCylinder(r=FUEL_R), openmc.ZCylinder(r=CLAD_R)
    def pin(name, m):
        return openmc.Universe(name=name, cells=[openmc.Cell(fill=m, region=-fr), openmc.Cell(fill=zirc, region=+fr & -cr),
                                                 openmc.Cell(fill=water, region=+cr)])
    f, g = pin("fuel pin", fuel), pin("gadolinia pin", gad)
    h = openmc.Universe(name="water hole", cells=[openmc.Cell(fill=water)])
    lattice = openmc.RectLattice(name="8 x 8 assembly")
    lattice.pitch, lattice.lower_left = (PITCH, PITCH), (PITCH, PITCH)
    lattice.universes = [[f, f, f, f, f, f, f, f],
                         [f, f, f, f, f, f, f, f],
                         [f, f, f, g, f, g, f, f],
                         [f, f, g, h, h, f, g, f],
                         [f, f, f, h, h, f, f, f],
                         [f, f, g, f, f, f, g, f],
                         [f, f, f, g, f, g, f, f],
                         [f, f, f, f, f, f, f, f]]
    cells = [openmc.Cell(name="assembly", fill=lattice, region=assembly_box),
             openmc.Cell(name="top blade", fill=b4c, region=top_blade),
             openmc.Cell(name="left blade", fill=b4c, region=left_blade),
             openmc.Cell(name="blade centre", fill=steel, region=corner),
             openmc.Cell(name="water around", fill=water, region=everything & ~assembly_box & ~top_blade & ~left_blade & ~corner)]
    geometry = openmc.Geometry(cells)

    settings = openmc.Settings()
    settings.run_mode = "eigenvalue"
    for key, value in SIZES[size].items():
        setattr(settings, key, value)
    settings.seed = 20261116
    settings.source = openmc.IndependentSource(space=openmc.stats.Box([PITCH, PITCH, -1.0], [LENGTH - PITCH, LENGTH - PITCH, 1.0]),
                                               constraints={"fissionable": True})
    settings.output = {"tallies": False}
    mesh = openmc.RegularMesh(name="10 x 10 over the whole cell")
    mesh.dimension, mesh.lower_left, mesh.upper_right = [10, 10], [0.0, 0.0], [LENGTH, LENGTH]
    libs = libraries(geometry, mesh)
    tallies = openmc.Tallies()
    for lib in libs.values():
        lib.add_to_tallies_file(tallies, merge=True)
    model = openmc.Model(geometry=geometry, materials=openmc.Materials([fuel, gad, zirc, water, b4c, steel]), settings=settings,
                         tallies=tallies)
    return model, libs


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--size", choices=list(SIZES), default=OPTIONS["size"])
    a = parser.parse_args()
    build_with_libraries(a.size)[0].export_to_model_xml()


if __name__ == "__main__":
    main()
