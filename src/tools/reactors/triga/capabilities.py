"""
What the writers can build in the current block. A valid spec can still be unsupported: that raises UnsupportedFeature, naming
the field and the block in which it arrives. Kept apart from spec validation on purpose, so widening support in a later block
changes this file only.
"""
from __future__ import annotations

from .errors import InvalidSpec, UnsupportedFeature

CODES = ("openmc", "mcnp")


def check_supported(spec, code="openmc"):
    """Raise UnsupportedFeature (or InvalidSpec for an unknown code) if this writer cannot build this spec yet."""
    if code not in CODES:
        raise InvalidSpec("code", f"must be one of {list(CODES)}, got {code!r}")
    if code == "mcnp":
        raise UnsupportedFeature("code=mcnp", "the MCNP writer is planned for Block 4")
    env = spec.environment
    if env.barite != env.detector_geometry:
        raise UnsupportedFeature(
            "environment.barite / environment.detector_geometry",
            "the patched OpenMC builder always adds both the barite region and the detector cells, so both must be true or both "
            f"false; got barite={env.barite}, detector_geometry={env.detector_geometry}")
