"""The single response envelope every endpoint returns.

Success:
    {
      "success": true,
      "data": <object | array | null>,
      "meta": <pagination object | null>,
      "message": <string | null>,
      "request_id": "req_9f2c...",
      "timestamp": "2026-08-08T10:00:00Z"
    }

Error:
    {
      "success": false,
      "error": {
        "code": "VALIDATION_ERROR",
        "message": "Some fields are invalid.",
        "details": [{"field": "email", "message": "Not a valid email address."}]
      },
      "request_id": "req_9f2c...",
      "timestamp": "2026-08-08T10:00:00Z"
    }

`success` is the discriminator: the frontend narrows the union on it (see
`frontend/types/api.ts`), so `data` is only ever read on a success and `error`
only on a failure. `data` and `error` never both appear.

Endpoints should return `ok(...)` / `paginated(...)` rather than raw dicts, and
raise `AppError` subclasses rather than building error bodies by hand.
"""

from datetime import datetime, timezone
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field

from app.core.context import get_request_id

T = TypeVar("T")


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------- #
# Pydantic models. These exist so FastAPI's OpenAPI schema documents the real
# envelope; the runtime path uses the plain-dict builders below (cheaper, and
# they avoid re-validating already-validated data).
# --------------------------------------------------------------------------- #
class PaginationMeta(BaseModel):
    page: int = Field(..., ge=1, description="Current page, 1-indexed.")
    page_size: int = Field(..., ge=1, description="Items per page.")
    total: int = Field(..., ge=0, description="Total items across all pages.")
    total_pages: int = Field(..., ge=0, description="Total number of pages.")
    has_next: bool
    has_prev: bool


class ErrorBody(BaseModel):
    code: str = Field(..., description="Stable machine-readable code from ErrorCode.")
    message: str = Field(..., description="User-safe explanation.")
    details: list[dict[str, str]] = Field(
        default_factory=list,
        description="Field-level problems, populated for validation errors.",
    )


class SuccessResponse(BaseModel, Generic[T]):
    success: bool = True
    data: T | None = None
    meta: PaginationMeta | None = None
    message: str | None = None
    request_id: str
    timestamp: str


class ErrorResponse(BaseModel):
    success: bool = False
    error: ErrorBody
    request_id: str
    timestamp: str


# --------------------------------------------------------------------------- #
# Builders (the runtime path)
# --------------------------------------------------------------------------- #
def ok(
    data: Any = None,
    *,
    message: str | None = None,
    meta: dict[str, Any] | None = None,
    request_id: str | None = None,
) -> dict[str, Any]:
    """Build a success envelope.

    `request_id` defaults to the current request's id, read from the context
    variable the middleware set. Endpoints therefore never pass it, and cannot
    forget to.
    """
    return {
        "success": True,
        "data": data,
        "meta": meta,
        "message": message,
        "request_id": request_id if request_id is not None else get_request_id(),
        "timestamp": _now_iso(),
    }


def build_pagination_meta(page: int, page_size: int, total: int) -> dict[str, Any]:
    total_pages = (total + page_size - 1) // page_size if page_size else 0
    return {
        "page": page,
        "page_size": page_size,
        "total": total,
        "total_pages": total_pages,
        "has_next": page < total_pages,
        "has_prev": page > 1,
    }


def paginated(
    items: list[Any],
    *,
    page: int,
    page_size: int,
    total: int,
    message: str | None = None,
    request_id: str | None = None,
) -> dict[str, Any]:
    """Build a success envelope whose `data` is a list and `meta` is pagination."""
    return ok(
        data=items,
        message=message,
        meta=build_pagination_meta(page, page_size, total),
        request_id=request_id,
    )


def error(
    code: str,
    message: str,
    *,
    details: list[dict[str, str]] | None = None,
    request_id: str | None = None,
) -> dict[str, Any]:
    """Build an error envelope. Normally called only by the exception handlers."""
    return {
        "success": False,
        "error": {
            "code": code,
            "message": message,
            "details": details or [],
        },
        "request_id": request_id if request_id is not None else get_request_id(),
        "timestamp": _now_iso(),
    }
