"""
triga: build transport-code input for a TRIGA reactor from a code-neutral spec.

The spec layer (`resolve`, `Spec`, presets, assumptions, errors) needs no libraries. `build_input` runs the writer in a worker
process that has CoreForge and OpenMC, and itself imports neither.
"""
# AutoMC's one version since 0.8.0 (automc_version.py; the builder had its own, last 0.7.0). Every case records it as triga_version,
# and a cached build is reused only by the same version, so a release rebuilds.
from automc_version import VERSION as __version__

from .errors import BuildTimeout, InvalidSpec, MissingLibrary, TrigaError, UnsupportedFeature
from .presets import active_assumptions, all_assumptions, describe as describe_presets, facility_data
from .spec import RUN_SIZES, Spec, resolve, rod_to_fraction, rod_views
from .capabilities import check_supported
from .result import BuildResult
from .build import build_input
from .sweep import build_sweep, field_states, list_states, load_sweep, plan_sweep, rod_states

__all__ = [
    "__version__", "build_input", "BuildResult", "resolve", "Spec", "check_supported", "active_assumptions", "all_assumptions",
    "describe_presets", "facility_data", "rod_to_fraction", "rod_views", "RUN_SIZES",
    "plan_sweep", "build_sweep", "rod_states", "field_states", "list_states", "load_sweep",
    "TrigaError", "InvalidSpec", "UnsupportedFeature", "MissingLibrary", "BuildTimeout",
]
