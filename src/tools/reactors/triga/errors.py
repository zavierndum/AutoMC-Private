"""Exceptions raised by the triga builder. Each names the field or feature at fault. No silent fallbacks."""


class TrigaError(Exception):
    """Base class for every error raised on purpose by this package."""


class InvalidSpec(TrigaError):
    """A spec value is malformed or out of range. `field` is the dotted path, for example "rods.transient"."""

    def __init__(self, field, message):
        self.field = field
        self.message = message
        super().__init__(f"{field}: {message}")


class UnsupportedFeature(TrigaError):
    """The spec is valid, but the chosen writer or the current block cannot build it yet."""

    def __init__(self, feature, message):
        self.feature = feature
        self.message = message
        super().__init__(f"{feature}: {message}")


class MissingLibrary(TrigaError):
    """A library profile cannot be activated: snapshot missing, or another version already imported in this process."""


class BuildTimeout(TrigaError):
    """The worker process did not finish in time and its whole process group was killed."""


def exit_code_for(exc):
    """The command-line exit code for an exception: 2 invalid spec, 3 unsupported, 4 missing library, 5 timeout, 1 anything else."""
    if isinstance(exc, InvalidSpec):
        return 2
    if isinstance(exc, UnsupportedFeature):
        return 3
    if isinstance(exc, MissingLibrary):
        return 4
    if isinstance(exc, BuildTimeout):
        return 5
    return 1


def error_payload(exc):
    """JSON-friendly description of an error, for `--json` output and for agents."""
    return {"type": type(exc).__name__, "field": getattr(exc, "field", None) or getattr(exc, "feature", None),
            "message": str(exc), "exit_code": exit_code_for(exc)}
