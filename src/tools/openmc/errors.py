"""The one error type of the general OpenMC tools: it names the field at fault and carries the exit code and the type a payload shows."""


class OpenMCError(Exception):
    """kind: InvalidArgument (2), NoResult (2), NotAllowed (3), NeedsApproval (3), MissingLibrary (4), NotFound (2)."""

    def __init__(self, field, message, kind="InvalidArgument", exit_code=2):
        self.field, self.kind, self.exit_code, self.message = field, kind, exit_code, message
        super().__init__(f"{field}: {message}" if field else message)

    def payload(self):
        return {"type": self.kind, "field": self.field, "message": str(self), "exit_code": self.exit_code}
