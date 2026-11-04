"""
Which facility packs the tests can use. Tests of the reference preset need the restricted NETL pack and skip without it, saying so.
Their expected values are read from the pack (REF), so no restricted number is written in a test; the values that must never appear
in a public file or a prompt are read from it too (canaries).
"""
import unittest

from tools.reactors.triga import packs, presets

RESTRICTED_PACK = "netl_restricted"
REFERENCE_PRESET = "netl_boc505"
PUBLIC_PRESET = "netl_public_fresh_fuel"
RESTRICTED_REASON = (f"needs the restricted NETL facility pack ({RESTRICTED_PACK}), which is not installed: set AUTOMC_FACILITY_PACKS "
                     f"or put it in AutoMC_restricted/facility_packs next to AutoMC")


def restricted():
    """The restricted NETL pack, or None when it is not installed."""
    return next((p for p in packs.installed().packs if p.id == RESTRICTED_PACK), None)


def require_restricted():
    """For setUpModule of a test module that needs the reference preset."""
    if restricted() is None:
        raise unittest.SkipTest(RESTRICTED_REASON)


# the reference preset as a dict (None without the pack), for expected values
REF = presets.get(REFERENCE_PRESET)[1] if restricted() is not None else None


def canaries():
    """The restricted pack's canaries (provides.canaries: text that must never appear in a public file of AutoMC or in a prompt), or
    [] without the pack."""
    pack = restricted()
    return list(((pack.data("canaries") if pack is not None else None) or {}).get("values", []))


def ref_port_lengths():
    """(ports 2 to 4, port 1/5) of the reference preset, in cm."""
    env = REF["environment"]
    common = env["beam_port_length_factor"] * env["pool_radius_cm"]
    return common, (common if env["beam_port_1_5_length_cm"] is None else env["beam_port_1_5_length_cm"])
