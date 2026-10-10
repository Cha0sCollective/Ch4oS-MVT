from smoke import CheckFailed, InfrastructureError


class Unsupported(InfrastructureError):
    """An input or requested capability cannot be tested by this build."""


class AuthRequired(InfrastructureError):
    """The dedicated launcher account needs operator attention."""
