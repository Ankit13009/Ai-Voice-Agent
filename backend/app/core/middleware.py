"""Cross-cutting HTTP middleware: request ids, security headers, rate limiting.

Order matters. In `main.py` these are added so that the request-id middleware
runs outermost, because the exception handlers and every log line depend on
`request.state.request_id` existing.
"""

import logging
import time
import uuid
from collections import defaultdict, deque

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.core.context import set_request_id
from app.core.errors import ErrorCode
from app.core.response import error as error_body

logger = logging.getLogger(__name__)


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Attach a request id to every request, echo it on the response, and log
    one structured line per request.

    The id is what a clinic quotes when reporting a problem; it ties the user's
    error toast to the exact server log line.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        # Honour an upstream id (load balancer / gateway) if it looks sane.
        incoming = request.headers.get("x-request-id", "")
        request_id = incoming if 8 <= len(incoming) <= 64 else f"req_{uuid.uuid4().hex[:16]}"
        request.state.request_id = request_id
        # Also published as a context variable so `core.response.ok()` can stamp
        # it without every endpoint having to pass it down.
        set_request_id(request_id)

        started = time.perf_counter()
        response = await call_next(request)
        duration_ms = (time.perf_counter() - started) * 1000

        response.headers["X-Request-ID"] = request_id
        logger.info(
            "%s %s -> %s in %.1fms (request_id=%s)",
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
            request_id,
        )
        return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Baseline hardening headers.

    This is a JSON API with no HTML surface, so the CSP is maximally strict:
    nothing is allowed to load or execute. It matters because a browser that is
    tricked into rendering an API response (content sniffing, an old
    `Content-Type` bug) then has no way to run script from it.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'",
        )
        response.headers.setdefault(
            "Permissions-Policy", "geolocation=(), microphone=(), camera=()"
        )
        # Only meaningful over TLS; harmless in dev where the scheme is http.
        if request.url.scheme == "https":
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
            )
        return response


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Fixed-cost sliding-window limiter, applied only to sensitive prefixes.

    Deliberately in-process: it is a brute-force speed bump for a single-node
    deployment, not a distributed quota system. Multi-node deployments must move
    this to Redis, otherwise the effective limit multiplies by the node count.
    That tradeoff is fine at clinic scale and documented in the README.
    """

    def __init__(self, app, rules: dict[str, tuple[int, int]]) -> None:
        """Args:
        rules: {path_prefix: (max_requests, window_seconds)}
        """
        super().__init__(app)
        self.rules = rules
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def _rule_for(self, path: str) -> tuple[int, int] | None:
        for prefix, rule in self.rules.items():
            if path.startswith(prefix):
                return rule
        return None

    def _client_key(self, request: Request, prefix: str) -> str:
        # X-Forwarded-For is only trustworthy behind a proxy that overwrites it.
        # Take the left-most entry, which is the original client for well-behaved
        # proxies, and fall back to the socket peer.
        fwd = request.headers.get("x-forwarded-for", "")
        ip = fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else "unknown")
        return f"{ip}:{prefix}"

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        path = request.url.path
        rule = self._rule_for(path)
        if rule is None:
            return await call_next(request)

        max_requests, window = rule
        key = self._client_key(request, path)
        now = time.monotonic()
        bucket = self._hits[key]

        while bucket and now - bucket[0] > window:
            bucket.popleft()

        if len(bucket) >= max_requests:
            retry_after = int(window - (now - bucket[0])) + 1
            request_id = getattr(request.state, "request_id", "")
            logger.warning("Rate limit hit for %s on %s", key, path)
            return JSONResponse(
                status_code=429,
                headers={"Retry-After": str(retry_after)},
                content=error_body(
                    ErrorCode.RATE_LIMITED,
                    "Too many attempts. Please wait a moment and try again.",
                    request_id=request_id,
                ),
            )

        bucket.append(now)
        return await call_next(request)
