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


class DemoReadOnlyError(ForbiddenError):
    """The public demo refuses changes, so every visitor sees the same data."""

    code = "demo_read_only"

    def __init__(self) -> None:
        super().__init__("This is a read-only demo: you can sign in and ask questions.")


class RateLimitedError(DomainError):
    """Too many questions in a short time (SECURITY.md T8)."""

    code = "rate_limited"

    def __init__(
        self,
        retry_after_s: float,
        detail: str = "Too many questions in a short time. Wait a moment and try again.",
    ) -> None:
        super().__init__(detail)
        self.retry_after_s = retry_after_s


class BudgetExhaustedError(RateLimitedError):
    """The tenant spent its monthly token budget; it resets on the 1st (UTC)."""

    code = "budget_exhausted"

    def __init__(self, retry_after_s: float) -> None:
        super().__init__(
            retry_after_s,
            "Your organization has used its monthly question budget. "
            "An administrator can raise it.",
        )


class UnsupportedDocumentError(DomainError):
    """Rejected at upload: not one of the four accepted formats, whatever its name says."""

    code = "unsupported_document"


class DocumentTooLargeError(DomainError):
    code = "document_too_large"


class IngestionError(Exception):
    """A step of the ingestion pipeline failed.

    `code` is stored on the version and shown to the admin; `retryable` says
    whether trying again can help (a provider outage) or not (a corrupt file).
    """

    def __init__(self, code: str, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


class UnreadableDocumentError(IngestionError):
    def __init__(self, message: str) -> None:
        super().__init__("unreadable_document", message, retryable=False)


class EmptyDocumentError(IngestionError):
    def __init__(self) -> None:
        super().__init__(
            "empty_document",
            "No text could be extracted. Scanned documents need OCR, which is not supported yet.",
            retryable=False,
        )


class ProviderUnavailableError(IngestionError):
    """An embedding provider, the vector store or object storage did not answer in time."""

    def __init__(self, provider: str, message: str) -> None:
        super().__init__(f"{provider}_unavailable", message, retryable=True)
