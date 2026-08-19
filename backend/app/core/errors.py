"""Application error taxonomy.

Every failure that leaves the API is an `AppError` (or is converted into one by
the handlers in `core/handlers.py`), so the wire format is identical no matter
where the failure came from. Handlers never invent codes; they map onto this
enum.

Rule: `message` is safe to show a user. Anything sensitive (SQL, stack traces,
upstream response bodies) goes to the log, never into the response.
"""

from enum import StrEnum
from typing import Any


class ErrorCode(StrEnum):
    """Stable, machine-readable error codes.

    These are part of the public API contract. The frontend switches on them
    (see `types/api.ts`), so renaming one is a breaking change.
    """

    # --- 400 ---
    VALIDATION_ERROR = "VALIDATION_ERROR"
    BAD_REQUEST = "BAD_REQUEST"

    # --- 401 ---
    UNAUTHENTICATED = "UNAUTHENTICATED"
    INVALID_CREDENTIALS = "INVALID_CREDENTIALS"
    TOKEN_EXPIRED = "TOKEN_EXPIRED"
    TOKEN_INVALID = "TOKEN_INVALID"

    # --- 403 ---
    FORBIDDEN = "FORBIDDEN"
    # The caller is authenticated but holds a one-time password and must set
    # a real one before doing anything else. Distinct from FORBIDDEN so the
    # dashboard can route to the change-password screen rather than showing a
    # permission error the user cannot act on.
    PASSWORD_CHANGE_REQUIRED = "PASSWORD_CHANGE_REQUIRED"
    INSUFFICIENT_ROLE = "INSUFFICIENT_ROLE"
    BUSINESS_ACCESS_DENIED = "BUSINESS_ACCESS_DENIED"
    WEBHOOK_SIGNATURE_INVALID = "WEBHOOK_SIGNATURE_INVALID"

    # --- 404 ---
    NOT_FOUND = "NOT_FOUND"

    # --- 409 ---
    CONFLICT = "CONFLICT"
    ALREADY_EXISTS = "ALREADY_EXISTS"
    SLOT_UNAVAILABLE = "SLOT_UNAVAILABLE"

    # --- 422 ---
    UNPROCESSABLE = "UNPROCESSABLE"

    # --- 429 ---
    RATE_LIMITED = "RATE_LIMITED"

    # --- 5xx ---
    INTERNAL_ERROR = "INTERNAL_ERROR"
    UPSTREAM_ERROR = "UPSTREAM_ERROR"
    INTEGRATION_NOT_CONFIGURED = "INTEGRATION_NOT_CONFIGURED"
    SERVICE_UNAVAILABLE = "SERVICE_UNAVAILABLE"


class ErrorDetail:
    """A single field-level problem. Kept dict-shaped for cheap serialization."""

    __slots__ = ("field", "message")

    def __init__(self, field: str, message: str) -> None:
        self.field = field
        self.message = message

    def to_dict(self) -> dict[str, str]:
        return {"field": self.field, "message": self.message}


class AppError(Exception):
    """Base class for every deliberate API failure.

    Args:
        code: machine-readable `ErrorCode`.
        message: user-safe explanation.
        status_code: HTTP status to return.
        details: optional field-level breakdown (validation).
        log_context: extra data for the log line only. NEVER serialized.
    """

    status_code: int = 500
    code: ErrorCode = ErrorCode.INTERNAL_ERROR

    def __init__(
        self,
        message: str,
        *,
        code: ErrorCode | None = None,
        status_code: int | None = None,
        details: list[ErrorDetail] | None = None,
        log_context: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        if status_code is not None:
            self.status_code = status_code
        self.details = details or []
        self.log_context = log_context or {}


# --------------------------------------------------------------------------- #
# Concrete errors. Prefer these over raising AppError directly, so the status
# code and error code can't drift apart across call sites.
# --------------------------------------------------------------------------- #
class ValidationError(AppError):
    status_code = 400
    code = ErrorCode.VALIDATION_ERROR


class BadRequestError(AppError):
    status_code = 400
    code = ErrorCode.BAD_REQUEST


class UnauthenticatedError(AppError):
    status_code = 401
    code = ErrorCode.UNAUTHENTICATED

    def __init__(self, message: str = "Authentication required.", **kw: Any) -> None:
        super().__init__(message, **kw)


class InvalidCredentialsError(AppError):
    status_code = 401
    code = ErrorCode.INVALID_CREDENTIALS

    def __init__(self, message: str = "Email or password is incorrect.", **kw: Any) -> None:
        super().__init__(message, **kw)


class TokenExpiredError(AppError):
    status_code = 401
    code = ErrorCode.TOKEN_EXPIRED

    def __init__(self, message: str = "Your session has expired. Please sign in again.", **kw: Any) -> None:
        super().__init__(message, **kw)


class TokenInvalidError(AppError):
    status_code = 401
    code = ErrorCode.TOKEN_INVALID

    def __init__(self, message: str = "Invalid authentication token.", **kw: Any) -> None:
        super().__init__(message, **kw)


class ForbiddenError(AppError):
    status_code = 403
    code = ErrorCode.FORBIDDEN

    def __init__(self, message: str = "You do not have access to this resource.", **kw: Any) -> None:
        super().__init__(message, **kw)


class InsufficientRoleError(AppError):
    status_code = 403
    code = ErrorCode.INSUFFICIENT_ROLE

    def __init__(self, message: str = "Your role does not permit this action.", **kw: Any) -> None:
        super().__init__(message, **kw)


class BusinessAccessDeniedError(AppError):
    """Raised when a token's business does not match the requested resource.

    Deliberately worded like a 403 rather than leaking whether the id exists.
    """

    status_code = 403
    code = ErrorCode.BUSINESS_ACCESS_DENIED

    def __init__(self, message: str = "You do not have access to this business.", **kw: Any) -> None:
        super().__init__(message, **kw)


class WebhookSignatureError(AppError):
    status_code = 403
    code = ErrorCode.WEBHOOK_SIGNATURE_INVALID

    def __init__(self, message: str = "Webhook signature verification failed.", **kw: Any) -> None:
        super().__init__(message, **kw)


class NotFoundError(AppError):
    status_code = 404
    code = ErrorCode.NOT_FOUND

    def __init__(self, resource: str = "Resource", **kw: Any) -> None:
        super().__init__(f"{resource} not found.", **kw)


class ConflictError(AppError):
    status_code = 409
    code = ErrorCode.CONFLICT


class AlreadyExistsError(AppError):
    status_code = 409
    code = ErrorCode.ALREADY_EXISTS


class SlotUnavailableError(AppError):
    status_code = 409
    code = ErrorCode.SLOT_UNAVAILABLE

    def __init__(self, message: str = "That time slot is no longer available.", **kw: Any) -> None:
        super().__init__(message, **kw)


class RateLimitedError(AppError):
    status_code = 429
    code = ErrorCode.RATE_LIMITED

    def __init__(self, message: str = "Too many requests. Please try again shortly.", **kw: Any) -> None:
        super().__init__(message, **kw)
        self.retry_after: int = int(kw.get("log_context", {}).get("retry_after", 60))


class IntegrationNotConfiguredError(AppError):
    """A third-party integration (Google Calendar, WhatsApp, VAPI) is not set up
    for this business. 503 because it is a server-side config gap, not a caller
    mistake."""

    status_code = 503
    code = ErrorCode.INTEGRATION_NOT_CONFIGURED


class UpstreamError(AppError):
    """A third-party API failed. The upstream body is logged, never returned."""

    status_code = 502
    code = ErrorCode.UPSTREAM_ERROR

    def __init__(self, service: str, message: str | None = None, **kw: Any) -> None:
        super().__init__(message or f"{service} is currently unavailable.", **kw)
        self.service = service


class InternalError(AppError):
    status_code = 500
    code = ErrorCode.INTERNAL_ERROR

    def __init__(self, message: str = "Something went wrong on our end.", **kw: Any) -> None:
        super().__init__(message, **kw)


class PasswordChangeRequiredError(AppError):
    """Issued a one-time password and has not replaced it yet.

    Enforced rather than advisory: the flag was set on every created user and
    every reset, shown in the dashboard, and never checked, so a password read
    out over the phone kept working indefinitely. The UI told operators the user
    "will be asked to change it", which was simply untrue.
    """

    status_code = 403
    code = ErrorCode.PASSWORD_CHANGE_REQUIRED

    def __init__(
        self,
        message: str = "Set a new password before you continue.",
        **kw: Any,
    ) -> None:
        super().__init__(message, **kw)
