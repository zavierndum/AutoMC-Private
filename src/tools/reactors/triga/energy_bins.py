"""
Energy bins for tallies. Edges are in eV, ascending; n edges make n - 1 bins, and each bin is one value of an OpenMC EnergyFilter.
The spec field `tallies_options.energy_bins` is a name from the list below or an explicit list of edges:

  energy_bins_238              238 bins from 1e-05 eV to 20 MeV (239 edges), the structure used for the beam-port detector tallies.
                               ENERGY_BIN_EDGES_238_EV below is copied programmatically from the `energy_bins` list in the reference
                               script, so the values are the reference ones, not retyped.
  energy_bins_mpact_51_groups  the edges of the 51 energy groups of the MPACT cross-section library, taken from OpenMC's own
                               'MPACT-51' structure (the library's problem builders use it for their spectrum tally). They are
                               looked up by name inside the build worker, not copied here. Here "groups" is MPACT's term for its
                               multigroup energy structure; in a tally each group is one energy bin. It has nothing to do with
                               delayed neutron precursor groups.
  [e0, e1, ..., en]            any ascending list of at least two edges in eV (custom bins).
"""

ENERGY_BIN_EDGES_238_EV = (
    1e-05, 0.0001, 0.0005, 0.00075, 0.001, 0.0012, 0.0015, 0.002, 0.0025, 0.003, 0.004, 0.005, 0.0075, 0.01, 0.0253, 0.03, 0.04,
    0.05, 0.06, 0.07, 0.08, 0.09, 0.1, 0.125, 0.15, 0.175, 0.2, 0.225, 0.25, 0.275, 0.3, 0.325, 0.35, 0.375, 0.4, 0.45, 0.5, 0.55,
    0.6, 0.625, 0.65, 0.7, 0.75, 0.8, 0.85, 0.9, 0.925, 0.95, 0.975, 1.0, 1.01, 1.02, 1.03, 1.04, 1.05, 1.06, 1.07, 1.08, 1.09, 1.1,
    1.11, 1.12, 1.13, 1.14, 1.15, 1.175, 1.2, 1.225, 1.25, 1.3, 1.35, 1.4, 1.45, 1.5, 1.59, 1.68, 1.77, 1.86, 1.94, 2.0, 2.12, 2.21,
    2.3, 2.38, 2.47, 2.57, 2.67, 2.77, 2.87, 2.97, 3.0, 3.05, 3.15, 3.5, 3.73, 4.0, 4.75, 5.0, 5.4, 6.0, 6.25, 6.5, 6.75, 7.0, 7.15,
    8.1, 9.1, 10.0, 11.5, 11.9, 12.9, 13.75, 14.4, 15.1, 16.0, 17.0, 18.5, 19.0, 20.0, 21.0, 22.5, 25.0, 27.5, 30.0, 31.25, 31.75,
    33.25, 33.75, 34.6, 35.5, 37.0, 38.0, 39.1, 39.6, 41.0, 42.4, 44.0, 45.2, 47.0, 48.3, 49.2, 50.6, 52.0, 53.4, 59.0, 61.0, 65.0,
    67.5, 72.0, 76.0, 80.0, 82.0, 90.0, 100.0, 108.0, 115.0, 119.0, 122.0, 186.0, 192.5, 207.5, 210.0, 240.0, 285.0, 305.0, 550.0,
    670.0, 683.0, 950.0, 1150.0, 1500.0, 1550.0, 1800.0, 2200.0, 2290.0, 2580.0, 3000.0, 3740.0, 3900.0, 6000.0, 8030.0, 9500.0,
    13000.0, 17000.0, 25000.0, 30000.0, 45000.0, 50000.0, 52000.0, 60000.0, 73000.0, 75000.0, 82000.0, 85000.0, 100000.0, 128300.0,
    150000.0, 200000.0, 270000.0, 330000.0, 400000.0, 420000.0, 440000.0, 470000.0, 499520.0, 550000.0, 573000.0, 600000.0,
    670000.0, 679000.0, 750000.0, 820000.0, 861100.0, 875000.0, 900000.0, 920000.0, 1010000.0, 1100000.0, 1200000.0, 1250000.0,
    1317000.0, 1356000.0, 1400000.0, 1500000.0, 1850000.0, 2354000.0, 2479000.0, 3000000.0, 4304000.0, 4800000.0, 6434000.0,
    8187300.0, 10000000.0, 12840000.0, 13840000.0, 14550000.0, 15683000.0, 17333000.0, 20000000.0
)

assert len(ENERGY_BIN_EDGES_238_EV) == 239 and list(ENERGY_BIN_EDGES_238_EV) == sorted(ENERGY_BIN_EDGES_238_EV)

ENERGY_BIN_COUNTS = {"energy_bins_238": len(ENERGY_BIN_EDGES_238_EV) - 1, "energy_bins_mpact_51_groups": 51}
NAMES = tuple(ENERGY_BIN_COUNTS)
MAX_CUSTOM_BINS = 100_000


def resolve(value, field="tallies_options.energy_bins"):
    """
    A name or an explicit list of edges -> a validated name (str) or list of floats. An explicit list equal to the 238-bin structure
    is returned under its name, so equal choices hash equally. Standard library only.
    """
    from .errors import InvalidSpec
    if isinstance(value, str):
        if value not in NAMES:
            raise InvalidSpec(field, f"must be one of {list(NAMES)} or a list of ascending edges in eV, got {value!r}")
        return value
    if not isinstance(value, (list, tuple)) or len(value) < 2:
        raise InvalidSpec(field, f"must be one of {list(NAMES)} or a list of at least two ascending edges in eV, got {value!r}")
    if any(isinstance(x, bool) or not isinstance(x, (int, float)) or x != x or x in (float("inf"), float("-inf")) or x < 0 for x in value):
        raise InvalidSpec(field, "edges must be finite numbers in eV, zero or larger")
    edges = [float(x) for x in value]
    if any(b <= a for a, b in zip(edges, edges[1:])):
        raise InvalidSpec(field, "edges must be strictly ascending")
    if len(edges) - 1 > MAX_CUSTOM_BINS:
        raise InvalidSpec(field, f"at most {MAX_CUSTOM_BINS:,} bins, got {len(edges) - 1:,}")
    return "energy_bins_238" if edges == list(ENERGY_BIN_EDGES_238_EV) else edges


def bin_count(value):
    """Number of energy bins for a resolved value (a name or a list of edges)."""
    return ENERGY_BIN_COUNTS[value] if isinstance(value, str) else len(value) - 1
