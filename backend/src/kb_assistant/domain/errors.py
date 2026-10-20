"""Domain errors.

The API maps each class to one RFC 7807 problem type (`api/errors.py`).
Messages are written for the client: they never contain another tenant's
data, identifiers the caller did not send, or internal details.
"""


class DomainError(Exception):
    """Base class. `code` is a stable, machine-readable slug."""

    code = "domain_error"

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class NotAuthenticatedError(DomainError):
    code = "not_authenticated"


class InvalidCredentialsError(NotAuthenticatedError):
    """Same message for unknown email, wrong password, inactive user, no membership."""

    code = "invalid_credentials"

    def __init__(self) -> None:
        super().__init__("Invalid email or password.")


class ForbiddenError(DomainError):
    code = "forbidden"


class NotFoundError(DomainError):
    """Also used for resources that exist but belong to someone else (no 403 oracle)."""

    code = "not_found"


class ConflictError(DomainError):
    code = "conflict"


class InvalidInvitationError(DomainError):
    code = "invalid_invitation"

    def __init__(self) -> None:
        super().__init__("This invitation is invalid, expired or already used.")


class PolicyViolationError(DomainError):
    """Input is well-formed but breaks a business rule (e.g. a weak password)."""

    code = "policy_violation"
