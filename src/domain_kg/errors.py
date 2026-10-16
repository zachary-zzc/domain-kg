"""Safe domain failures; transport wrappers never return raw backend exceptions."""

from enum import StrEnum
from uuid import UUID, uuid4


class ErrorCode(StrEnum):
    UNAUTHENTICATED = "UNAUTHENTICATED"
    FORBIDDEN = "FORBIDDEN"
    RESOURCE_NOT_ACCESSIBLE = "RESOURCE_NOT_ACCESSIBLE"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    UNSUPPORTED_FORMAT = "UNSUPPORTED_FORMAT"
    UNSUPPORTED_FILTER = "UNSUPPORTED_FILTER"
    QUOTA_EXCEEDED = "QUOTA_EXCEEDED"
    VERSION_CONFLICT = "VERSION_CONFLICT"
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
    CURSOR_EXPIRED = "CURSOR_EXPIRED"
    BACKEND_UNAVAILABLE = "BACKEND_UNAVAILABLE"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    EVIDENCE_VALIDATION_FAILED = "EVIDENCE_VALIDATION_FAILED"
    POLICY_CHANGED = "POLICY_CHANGED"
    REVISION_NOT_READY = "REVISION_NOT_READY"
    PROJECTION_INCONSISTENT = "PROJECTION_INCONSISTENT"


class DomainError(Exception):
    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        retryable: bool = False,
        request_id: UUID | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.request_id = request_id or uuid4()


def inaccessible_reference() -> DomainError:
    return DomainError(
        ErrorCode.RESOURCE_NOT_ACCESSIBLE, "The referenced resource is not accessible."
    )
