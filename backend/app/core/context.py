"""Per-request context, carried without threading it through every signature.

A `ContextVar` is set once by `RequestContextMiddleware` and read by the
response builders, so `ok(...)` stamps the correct request id with no argument
passed and no endpoint able to forget it.

This is safe under asyncio: each request runs in its own task, and a task
inherits a *copy* of the context at creation, so concurrent requests cannot see
each other's id.
"""

from contextvars import ContextVar

# Empty default covers code that runs outside a request: the reminder scheduler,
# CLI scripts, and tests.
_request_id: ContextVar[str] = ContextVar("request_id", default="")


def set_request_id(value: str) -> None:
    _request_id.set(value)


def get_request_id() -> str:
    return _request_id.get()
