"""Global exception handlers.

Registered in `main.py`. Their job is to guarantee that *no* code path can
return a body that isn't the standard error envelope, including paths we don't
control (FastAPI's own validation, Starlette's 404/405, and unhandled crashes).

Security note: the unhandled-exception handler logs the traceback and returns a
generic message. Exception text can contain connection strings, row data, or
upstream API keys, so it never reaches the client.
"""

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError as PydanticValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.errors import AppError, ErrorCode, RateLimitedError
from app.core.response import error as error_body

logger = logging.getLogger(__name__)

# Starlette raises bare HTTPExceptions for routing failures. Map the ones we can
# reach onto our codes so the frontend never sees an unknown code.
_STATUS_TO_CODE: dict[int, ErrorCode] = {
    400: ErrorCode.BAD_REQUEST,
    401: ErrorCode.UNAUTHENTICATED,
    403: ErrorCode.FORBIDDEN,
    404: ErrorCode.NOT_FOUND,
    405: ErrorCode.BAD_REQUEST,
    409: ErrorCode.CONFLICT,
    422: ErrorCode.UNPROCESSABLE,
    429: ErrorCode.RATE_LIMITED,
    500: ErrorCode.INTERNAL_ERROR,
    502: ErrorCode.UPSTREAM_ERROR,
    503: ErrorCode.SERVICE_UNAVAILABLE,
}


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "")


def _json(status: int, body: dict[str, Any], headers: dict[str, str] | None = None) -> JSONResponse:
    return JSONResponse(status_code=status, content=body, headers=headers or {})


def _field_path(loc: tuple) -> str:
    """Turn pydantic's loc tuple into a dotted field path the frontend can use
    to highlight an input. Drops the leading 'body'/'query' segment."""
    parts = [str(p) for p in loc if p not in ("body", "query", "path", "header")]
    return ".".join(parts) or "_root"


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(request: Request, exc: AppError) -> JSONResponse:
        # 5xx is our bug; 4xx is the caller's. Log accordingly so alerting can
        # key off ERROR without drowning in 401s.
        log = logger.error if exc.status_code >= 500 else logger.info
        log(
            "AppError %s (%s) on %s %s: %s | ctx=%s",
            exc.code,
            exc.status_code,
            request.method,
            request.url.path,
            exc.message,
            exc.log_context or {},
        )
        headers = {}
        if isinstance(exc, RateLimitedError):
            headers["Retry-After"] = str(exc.retry_after)
        return _json(
            exc.status_code,
            error_body(
                exc.code,
                exc.message,
                details=[d.to_dict() for d in exc.details],
                request_id=_request_id(request),
            ),
            headers,
        )

    @app.exception_handler(RequestValidationError)
    async def _request_validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        """FastAPI's own body/query validation. Reshaped into our envelope so the
        frontend has one validation format to handle, not two."""
        details = [
            {"field": _field_path(e.get("loc", ())), "message": e.get("msg", "Invalid value.")}
            for e in exc.errors()
        ]
        logger.info(
            "Validation failed on %s %s: %s", request.method, request.url.path, details
        )
        return _json(
            400,
            error_body(
                ErrorCode.VALIDATION_ERROR,
                "Some of the submitted fields are invalid.",
                details=details,
                request_id=_request_id(request),
            ),
        )

    @app.exception_handler(PydanticValidationError)
    async def _pydantic_validation(request: Request, exc: PydanticValidationError) -> JSONResponse:
        """A model we constructed ourselves failed validation. That is a server
        bug, so log loudly but still answer in the standard shape."""
        logger.error(
            "Internal model validation failed on %s %s: %s",
            request.method,
            request.url.path,
            exc.errors(),
        )
        return _json(
            500,
            error_body(
                ErrorCode.INTERNAL_ERROR,
                "Something went wrong on our end.",
                request_id=_request_id(request),
            ),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_exception(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _STATUS_TO_CODE.get(exc.status_code, ErrorCode.INTERNAL_ERROR)
        message = exc.detail if isinstance(exc.detail, str) else "Request failed."
        if exc.status_code == 404:
            message = "The requested endpoint does not exist."
        elif exc.status_code == 405:
            message = "That method is not allowed on this endpoint."
        return _json(
            exc.status_code,
            error_body(code, message, request_id=_request_id(request)),
            dict(getattr(exc, "headers", None) or {}),
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        # Never leak exception text: it can contain DSNs, row data, upstream keys.
        logger.exception(
            "Unhandled exception on %s %s (request_id=%s)",
            request.method,
            request.url.path,
            _request_id(request),
        )
        return _json(
            500,
            error_body(
                ErrorCode.INTERNAL_ERROR,
                "Something went wrong on our end. The team has been notified.",
                request_id=_request_id(request),
            ),
        )
