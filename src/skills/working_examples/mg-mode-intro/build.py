"""
Multigroup mode from hand-written data: the seven-group cross sections of the C5G7 benchmark (UO2, MOX at three enrichments, the
fission chamber, the guide tube and the moderator), entered as openmc.XSdata, written to an MGXS library (mgxs.h5, next to model.xml)
and used through Macroscopic materials. Three layouts:

- `infinite`: the UO2 data alone, mirrored on every side: an infinite medium, whose k-infinity post.py also gets exactly from the
  seven-group balance equations;
- `pincell`: the benchmark's pin: UO2 of radius 0.54 cm in moderator, pitch 1.26 cm, mirrored sides;
- `core`: the benchmark's two-dimensional quarter core: two UO2 and two MOX 17 x 17 assemblies and a moderator reflector, mirrored
  on the top and left, vacuum on the right and bottom, with the fission rate tallied in every pin for the pin and assembly powers.

Derived from: the OpenMC notebook "Multigroup Mode Part I: Introduction" (openmc-notebooks, tag v0.15.0; the use of XSdata,
MGXSLibrary and Macroscopic, and the C5G7 core as its problem). The data, the geometry and the reference results are those of the
OECD/NEA benchmark specification NEA/NSC/DOC(2003)16 (Tables 1(a) to 1(g), Figures 1 to 3, the reference MCNP solution). Written for
AutoMC; README.md explains the data's layout.

    python build.py                          the defaults below; writes model.xml and mgxs.h5
    python build.py --layout core --size reference

Nothing here runs transport.
"""
import argparse

import numpy as np
import openmc
import openmc.mgxs

# The choices of this example: edit them here, or give them on the command line.
OPTIONS = {"layout": "infinite", "size": "quick"}

EDGES = [1e-5, 0.0635, 10.0, 1.0e2, 1.0e3, 0.5e6, 1.0e6, 20.0e6]      # eV; nominal: the benchmark gives no group boundaries
R_FUEL, PITCH, N = 0.54, 1.26, 17
ASSEMBLY = N * PITCH                                                  # 21.42 cm
SIZES = {"quick": {"particles": 5_000, "batches": 60, "inactive": 20},
         "reference": {"particles": 50_000, "batches": 220, "inactive": 20}}
CORE_SIZES = {"quick": {"particles": 10_000, "batches": 100, "inactive": 40},      # 40 inactive: the source must spread over
              "reference": {"particles": 100_000, "batches": 300, "inactive": 50}}  # four assemblies first

# The benchmark's reference solution (MCNP, multigroup, 2-D): k and its 68% error; assembly powers with pin powers averaging 1
REFERENCE = {"keff": 1.186550, "keff_rel_error": 0.00003, "max_pin": 2.498, "min_pin": 0.232,
             "assembly": {"inner UO2": 492.8, "MOX": 211.7, "outer UO2": 139.8}}

# C5G7 seven-group data (1/cm), group 1 the fastest. "total" holds the benchmark's transport-corrected cross section, as its
# specification asks; with it, absorption plus each row of the scattering block (from group g, row, to group g', column) gives the
# total. nu_fission is nu x fission.
DATA = {
    "UO2": {
        "total": [0.177949, 0.329805, 0.480388, 0.554367, 0.311801, 0.395168, 0.564406],
        "absorption": [0.0080248, 0.0037174, 0.026769, 0.096236, 0.03002, 0.11126, 0.28278],
        "fission": [0.00721206, 0.000819301, 0.0064532, 0.0185648, 0.0178084, 0.0830348, 0.216004],
        "nu": [2.78145, 2.47443, 2.43383, 2.4338, 2.4338, 2.4338, 2.4338],
        "chi": [0.58791, 0.41176, 0.00033906, 1.1761e-07, 0, 0, 0],
        "scatter": [[0.127537, 0.042378, 9.4374e-06, 5.5163e-09, 0, 0, 0],
                    [0, 0.324456, 0.0016314, 3.1427e-09, 0, 0, 0],
                    [0, 0, 0.45094, 0.0026792, 0, 0, 0],
                    [0, 0, 0, 0.452565, 0.0055664, 0, 0],
                    [0, 0, 0, 0.00012525, 0.271401, 0.010255, 1.0021e-08],
                    [0, 0, 0, 0, 0.0012968, 0.265802, 0.016809],
                    [0, 0, 0, 0, 0, 0.0085458, 0.27308]]},
    "MOX 4.3%": {
        "total": [0.178731, 0.330849, 0.483772, 0.566922, 0.426227, 0.678997, 0.682852],
        "absorption": [0.0084339, 0.0037577, 0.02797, 0.10421, 0.13994, 0.40918, 0.40935],
        "fission": [0.00762704, 0.000876898, 0.00569835, 0.0228872, 0.0107635, 0.232757, 0.248968],
        "nu": [2.85209, 2.89099, 2.85486, 2.86073, 2.85447, 2.86415, 2.8678],
        "chi": [0.58791, 0.41176, 0.00033906, 1.1761e-07, 0, 0, 0],
        "scatter": [[0.128876, 0.041413, 8.229e-06, 5.0405e-09, 0, 0, 0],
                    [0, 0.325452, 0.0016395, 1.5982e-09, 0, 0, 0],
                    [0, 0, 0.453188, 0.0026142, 0, 0, 0],
                    [0, 0, 0, 0.457173, 0.0055394, 0, 0],
                    [0, 0, 0, 0.00016046, 0.276814, 0.0093127, 9.1656e-09],
                    [0, 0, 0, 0, 0.0020051, 0.252962, 0.01485],
                    [0, 0, 0, 0, 0, 0.0084948, 0.265007]]},
    "MOX 7.0%": {
        "total": [0.181323, 0.334368, 0.493785, 0.591216, 0.474198, 0.833601, 0.853603],
        "absorption": [0.0090657, 0.0042967, 0.032881, 0.12203, 0.18298, 0.56846, 0.58521],
        "fission": [0.00825446, 0.00132565, 0.00842156, 0.032873, 0.0159636, 0.323794, 0.362803],
        "nu": [2.88498, 2.91079, 2.86574, 2.87063, 2.86714, 2.86658, 2.87539],
        "chi": [0.58791, 0.41176, 0.00033906, 1.1761e-07, 0, 0, 0],
        "scatter": [[0.130457, 0.041792, 8.5105e-06, 5.1329e-09, 0, 0, 0],
                    [0, 0.328428, 0.0016436, 2.2017e-09, 0, 0, 0],
                    [0, 0, 0.458371, 0.0025331, 0, 0, 0],
                    [0, 0, 0, 0.463709, 0.0054766, 0, 0],
                    [0, 0, 0, 0.00017619, 0.282313, 0.0087289, 9.0016e-09],
                    [0, 0, 0, 0, 0.002276, 0.249751, 0.013114],
                    [0, 0, 0, 0, 0, 0.0088645, 0.259529]]},
    "MOX 8.7%": {
        "total": [0.183045, 0.336705, 0.500507, 0.606174, 0.502754, 0.921028, 0.955231],
        "absorption": [0.0094862, 0.0046556, 0.03624, 0.13272, 0.2084, 0.6587, 0.69017],
        "fission": [0.00867209, 0.00162426, 0.0102716, 0.0390447, 0.0192576, 0.374888, 0.430599],
        "nu": [2.90426, 2.91795, 2.86986, 2.87491, 2.87175, 2.86752, 2.87808],
        "chi": [0.58791, 0.41176, 0.00033906, 1.1761e-07, 0, 0, 0],
        "scatter": [[0.131504, 0.042046, 8.6972e-06, 5.1938e-09, 0, 0, 0],
                    [0, 0.330403, 0.0016463, 2.6006e-09, 0, 0, 0],
                    [0, 0, 0.461792, 0.0024749, 0, 0, 0],
                    [0, 0, 0, 0.468021, 0.005433, 0, 0],
                    [0, 0, 0, 0.00018597, 0.285771, 0.0083973, 8.928e-09],
                    [0, 0, 0, 0, 0.0023916, 0.247614, 0.012322],
                    [0, 0, 0, 0, 0, 0.0089681, 0.256093]]},
    "fission chamber": {
        "total": [0.126032, 0.29316, 0.28425, 0.28102, 0.33446, 0.56564, 1.17214],
        "absorption": [0.00051132, 7.5813e-05, 0.00031643, 0.0011675, 0.0033977, 0.0091886, 0.023244],
        "fission": [4.79002e-09, 5.82564e-09, 4.63719e-07, 5.24406e-06, 1.4539e-07, 7.14972e-07, 2.08041e-06],
        "nu": [2.76283, 2.46239, 2.4338, 2.4338, 2.4338, 2.4338, 2.4338],
        "chi": [0.58791, 0.41176, 0.00033906, 1.1761e-07, 0, 0, 0],
        "scatter": [[0.0661659, 0.05907, 0.00028334, 1.4622e-06, 2.0642e-08, 0, 0],
                    [0, 0.240377, 0.052435, 0.0002499, 1.9239e-05, 2.9875e-06, 4.214e-07],
                    [0, 0, 0.183425, 0.092288, 0.0069365, 0.001079, 0.00020543],
                    [0, 0, 0, 0.0790769, 0.16999, 0.02586, 0.0049256],
                    [0, 0, 0, 3.734e-05, 0.099757, 0.20679, 0.024478],
                    [0, 0, 0, 0, 0.00091742, 0.316774, 0.23876],
                    [0, 0, 0, 0, 0, 0.049793, 1.0991]]},
    "guide tube": {
        "total": [0.126032, 0.29316, 0.28424, 0.28096, 0.33444, 0.56564, 1.17215],
        "absorption": [0.00051132, 7.5801e-05, 0.00031572, 0.0011582, 0.0033975, 0.0091878, 0.023242],
        "scatter": [[0.0661659, 0.05907, 0.00028334, 1.4622e-06, 2.0642e-08, 0, 0],
                    [0, 0.240377, 0.052435, 0.0002499, 1.9239e-05, 2.9875e-06, 4.214e-07],
                    [0, 0, 0.183297, 0.092397, 0.0069446, 0.0010803, 0.00020567],
                    [0, 0, 0, 0.0788511, 0.17014, 0.025881, 0.0049297],
                    [0, 0, 0, 3.7333e-05, 0.0997372, 0.20679, 0.024478],
                    [0, 0, 0, 0, 0.00091726, 0.316765, 0.23877],
                    [0, 0, 0, 0, 0, 0.049792, 1.09912]]},
    "moderator": {
        "total": [0.159206, 0.41297, 0.59031, 0.58435, 0.718, 1.25445, 2.65038],
        "absorption": [0.00060105, 1.5793e-05, 0.00033716, 0.0019406, 0.0057416, 0.015001, 0.037239],
        "scatter": [[0.0444777, 0.1134, 0.00072347, 3.7499e-06, 5.3184e-08, 0, 0],
                    [0, 0.282334, 0.12994, 0.0006234, 4.8002e-05, 7.4486e-06, 1.0455e-06],
                    [0, 0, 0.345256, 0.22457, 0.016999, 0.0026443, 0.00050344],
                    [0, 0, 0, 0.0910284, 0.41551, 0.063732, 0.012139],
                    [0, 0, 0, 7.1437e-05, 0.139138, 0.51182, 0.061229],
                    [0, 0, 0, 0, 0.0022157, 0.699913, 0.53732],
                    [0, 0, 0, 0, 0, 0.13244, 2.4807]]},
}

# The pins of one assembly (row 0 at the top), as Figure 3 of the benchmark. U: UO2; L, M, H: MOX at 4.3, 7.0 and 8.7%;
# G: guide tube; F: fission chamber. Both assembly types have the same 24 guide tubes and a fission chamber at the centre.
MOX_TOP = ["LLLLLLLLLLLLLLLLL",
           "LMMMMMMMMMMMMMMML",
           "LMMMMGMMGMMGMMMML",
           "LMMGMHHHHHHHMGMML",
           "LMMMHHHHHHHHHMMML",
           "LMGHHGHHGHHGHHGML",
           "LMMHHHHHHHHHHHMML",
           "LMMHHHHHHHHHHHMML",
           "LMGHHGHHFHHGHHGML"]
MOX_MAP = MOX_TOP + MOX_TOP[-2::-1]                                   # rows 9 to 16 mirror rows 7 to 0
UO2_MAP = ["".join("U" if c in "LMH" else c for c in row) for row in MOX_MAP]
PIN_MATERIAL = {"U": "UO2", "L": "MOX 4.3%", "M": "MOX 7.0%", "H": "MOX 8.7%", "G": "guide tube", "F": "fission chamber"}
# The quarter core, top row first (Figure 1): mirrored on the top and left, so the inner UO2 assembly sits at the core's centre.
CORE_MAP = [["UO2", "MOX", "moderator"],
            ["MOX", "UO2", "moderator"],
            ["moderator", "moderator", "moderator"]]


def library(path="mgxs.h5"):
    """Write the seven data sets as an MGXS library; isotropic scattering (order 0), as the benchmark's data are."""
    groups = openmc.mgxs.EnergyGroups(EDGES)
    lib = openmc.MGXSLibrary(groups)
    for name, d in DATA.items():
        xs = openmc.XSdata(name, groups)
        xs.order = 0
        xs.set_total(d["total"])
        xs.set_absorption(d["absorption"])
        xs.set_scatter_matrix(np.array(d["scatter"])[:, :, np.newaxis])     # shape (group in, group out, Legendre order + 1)
        if "fission" in d:
            xs.set_fission(d["fission"])
            xs.set_nu_fission(np.multiply(d["nu"], d["fission"]))
            xs.set_chi(d["chi"])
        lib.add_xsdata(xs)
    lib.export_to_hdf5(path)


def macro(name):
    m = openmc.Material(name=name)
    m.set_density("macro", 1.0)
    m.add_macroscopic(name)
    return m


def pin_counts():
    """{material: pins} of one MOX assembly; the benchmark's 64, 100 and 100 MOX pins and 24 guide tubes check the map."""
    text = "".join(MOX_MAP)
    return {PIN_MATERIAL[c]: text.count(c) for c in "LMHGF"}


def core_geometry(mats):
    """The 2-D quarter core: pins in 17 x 17 lattices, assemblies in a 3 x 3 lattice, the box's boundary conditions."""
    assert pin_counts() == {"MOX 4.3%": 64, "MOX 7.0%": 100, "MOX 8.7%": 100, "guide tube": 24, "fission chamber": 1}, pin_counts()
    fuel_radius = openmc.ZCylinder(r=R_FUEL)
    pins = {c: openmc.Universe(name=f"{PIN_MATERIAL[c]} pin", cells=[
        openmc.Cell(name=PIN_MATERIAL[c], fill=mats[PIN_MATERIAL[c]], region=-fuel_radius),
        openmc.Cell(name="moderator", fill=mats["moderator"], region=+fuel_radius)]) for c in PIN_MATERIAL}

    def assembly(pin_map, name):
        lat = openmc.RectLattice(name=name)
        lat.pitch, lat.lower_left = (PITCH, PITCH), (-ASSEMBLY / 2, -ASSEMBLY / 2)
        lat.universes = [[pins[c] for c in row] for row in pin_map]
        return openmc.Universe(name=name, cells=[openmc.Cell(fill=lat)])

    kinds = {"UO2": assembly(UO2_MAP, "UO2 assembly"), "MOX": assembly(MOX_MAP, "MOX assembly"),
             "moderator": openmc.Universe(name="reflector", cells=[openmc.Cell(name="reflector", fill=mats["moderator"])])}
    core = openmc.RectLattice(name="quarter core")
    half = 1.5 * ASSEMBLY                                             # 32.13 cm: the core box is 64.26 cm across
    core.pitch, core.lower_left = (ASSEMBLY, ASSEMBLY), (-half, -half)
    core.universes = [[kinds[k] for k in row] for row in CORE_MAP]
    box = (+openmc.XPlane(-half, boundary_type="reflective") & -openmc.XPlane(half, boundary_type="vacuum")
           & +openmc.YPlane(-half, boundary_type="vacuum") & -openmc.YPlane(half, boundary_type="reflective")
           & +openmc.ZPlane(-1.0, boundary_type="reflective") & -openmc.ZPlane(1.0, boundary_type="reflective"))
    return openmc.Geometry([openmc.Cell(name="core", fill=core, region=box)])


def fuel_region():
    """Lower left and upper right (x, y) of the four fuel assemblies, the 34 x 34 pins of Figure 3."""
    half = 1.5 * ASSEMBLY
    return (-half, half - 2 * ASSEMBLY), (-half + 2 * ASSEMBLY, half)


def build(layout="infinite", size="quick"):
    library("mgxs.h5")
    if layout == "core":
        mats = {name: macro(name) for name in DATA}
        geometry = core_geometry(mats)
        (x0, y0), (x1, y1) = fuel_region()
        source = openmc.stats.Box([x0, y0, -1.0], [x1, y1, 1.0])
        mesh = openmc.RegularMesh(name="pins")
        mesh.dimension, mesh.lower_left, mesh.upper_right = (2 * N, 2 * N), (x0, y0), (x1, y1)
        pins = openmc.Tally(name="pin fission rate")
        pins.filters = [openmc.MeshFilter(mesh)]
        pins.scores = ["fission"]
        tallies, materials, sizes = openmc.Tallies([pins]), list(mats.values()), CORE_SIZES[size]
    else:
        uo2, moderator = macro("UO2"), macro("moderator")
        box = openmc.model.RectangularParallelepiped(-PITCH / 2, PITCH / 2, -PITCH / 2, PITCH / 2, -PITCH / 2, PITCH / 2,
                                                     boundary_type="reflective")
        if layout == "infinite":
            cells, materials = [openmc.Cell(name="UO2", fill=uo2, region=-box)], [uo2]
        else:
            pin = openmc.ZCylinder(r=R_FUEL)
            cells = [openmc.Cell(name="UO2", fill=uo2, region=-pin & -box),
                     openmc.Cell(name="moderator", fill=moderator, region=+pin & -box)]
            materials = [uo2, moderator]
        geometry = openmc.Geometry(cells)
        source = openmc.stats.Box([-R_FUEL / 2] * 3, [R_FUEL / 2] * 3)
        flux = openmc.Tally(name="flux by group")
        flux.filters = [openmc.CellFilter(cells), openmc.EnergyFilter(EDGES)]
        flux.scores = ["flux"]
        tallies, sizes = openmc.Tallies([flux]), SIZES[size]
    mats = openmc.Materials(materials)
    mats.cross_sections = "mgxs.h5"                 # relative: the run folder gets a copy next to its model.xml

    settings = openmc.Settings()
    settings.energy_mode = "multi-group"
    settings.run_mode = "eigenvalue"
    for key, value in sizes.items():
        setattr(settings, key, value)
    settings.seed = 20261117
    settings.source = openmc.IndependentSource(space=source, constraints={"fissionable": True})
    return openmc.Model(geometry=geometry, materials=mats, settings=settings, tallies=tallies)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--layout", choices=["infinite", "pincell", "core"], default=OPTIONS["layout"])
    parser.add_argument("--size", choices=list(SIZES), default=OPTIONS["size"])
    a = parser.parse_args()
    build(a.layout, a.size).export_to_model_xml()


if __name__ == "__main__":
    main()
