"""
Facts about the UT Austin NETL TRIGA that do not depend on any transport code or library version: geometry from the pinned
library (which cites the SAR), and the console rod scale and rod aliases from the control-room software. Facility data that is not
public lives in facility packs, not here.

Rod geometry numbers are duplicated from CoreForge/progression_problems on purpose, so a spec can be validated and converted
without importing a library. The OpenMC writer cross-checks them against the active library at build time and fails loudly if
they ever differ.
"""

CM_PER_INCH = 2.54

# Control rods ---------------------------------------------------------------------------------------------------------
RODS = ("transient", "regulating", "shim_1", "shim_2")
# the Control Room Agent's names (dt_config) map onto ours
ROD_ALIASES = {
    "transient": "transient", "regulating": "regulating", "shim_1": "shim_1", "shim_2": "shim_2",
    "TR": "transient", "RR": "regulating", "S1": "shim_1", "S2": "shim_2",
}
ROD_TRAVEL_CM = 15.0 * CM_PER_INCH            # 38.1 cm from fully inserted to fully withdrawn
CONSOLE_MAX = 960.0                           # NETL console units; Control Room Agent dt_config.ROD_MAX_POSITION
# reactor-frame z of the rod bottom when fully inserted (CoreForge DefaultGeometries, ref. SAR p. 58)
INSERTED_Z_CM = {"transient": -73.0250, "regulating": -76.5180, "shim_1": -76.5180, "shim_2": -76.5180}

# Locations in the core map ---------------------------------------------------------------------------------------------
PNT_LOCATION = "G-34"
THREE_EL_LOCATIONS = ("E-11", "F-13", "F-14")
# The conventional 3-EL liner sits between the inner sleeve (outer radius 2.06375 cm) and the outer casing (inner radius 2.23393 cm) in the
# pinned library (progression_problems 3el_spec@1f31371, DefaultGeometries.ThreeElementIrradiator), so it must be thinner than the gap.
THREE_EL_MAX_LINER_THICKNESS_CM = round(2.23393 - 2.06375, 5)
THREE_EL_LINER_THICKNESS_CM = 0.1016             # the library's default liner, 40 mil
D03 = "D-03"                                  # graphite in the stock core

# Pool (CoreForge DefaultGeometries.pool). The radius is a spec field; the height is fixed here (SAR p. 54).
POOL_HEIGHT_CM = 160.0
BEAM_PORT_AXIAL_OFFSET_CM = -6.985             # z of the beam-port centerlines (CoreForge DefaultGeometries.reactor)

# Neutron-data library ---------------------------------------------------------------------------------------------------
# ENDF/B-VIII.0 HDF5 temperatures available in the library installed with AutoMC (K)
XS_TEMPERATURES_K = (250.0, 294.0, 600.0, 900.0, 1200.0, 2500.0)
XS_TEMPERATURE_TOLERANCE_K = 10.0             # OpenMC's default "nearest" tolerance

# The numbered configurations and the measured insert worths are facility data, not library facts: they come from facility packs
# (configurations.yaml, measured_worths.yaml), see packs.py.
