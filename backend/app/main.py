"""FastAPI application entrypoint.

Run locally:
    uvicorn app.main:app --reload --port 8000

Middleware order is deliberate. Starlette runs middleware in reverse order of
registration, so the request-context middleware is added last to run first,
which guarantees `request.state.request_id` exists before anything else, the
rate limiter included, needs to build an error response.
"""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.config import get_settings, validate_production_config
from app.core.handlers import register_exception_handlers
from app.core.middleware import (
    RateLimitMiddleware,
    RequestContextMiddleware,
    SecurityHeadersMiddleware,
)
from app.core.response import ok
from app.webhooks import vapi as vapi_webhooks
from app.webhooks import whatsapp as whatsapp_webhooks

settings = get_settings()

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
logger = logging.getLogger("business-receptionist")

# Error reporting is optional and off unless a DSN is configured, so the app
# runs unchanged for anyone without a Sentry account. It matters in production
# because the free hosting tier keeps no log history: an exception during a call
# is unrecoverable an hour later, which is usually when someone reports it.
if settings.sentry_dsn:
    try:
        import sentry_sdk

        sentry_sdk.init(
            dsn=settings.sentry_dsn,
            environment=settings.app_env,
            # Errors only. Performance traces on a free tier burn the quota that
            # the actual exceptions need.
            traces_sample_rate=0.0,
            # Transcripts, phone numbers and names pass through this app, and
            # none of it belongs in a third-party error tracker.
            send_default_pii=False,
        )
        logger.info("Error reporting enabled.")
    except Exception:  # noqa: BLE001
        logger.exception("Could not start error reporting; continuing without it.")

# Brute-force protection on the endpoints worth attacking: {prefix: (max, seconds)}
RATE_LIMIT_RULES = {
    "/api/v1/auth/login": (10, 300),
    "/api/v1/auth/refresh": (30, 300),
    "/api/v1/auth/change-password": (5, 300),
    "/api/v1/onboarding": (20, 3600),
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    validate_production_config(settings)

    from app.db.session import dispose_db, init_db

    await init_db()

    stop_event = asyncio.Event()
    reminder_task: asyncio.Task | None = None

    if settings.reminder_scheduler_enabled:
        from app.services.reminders import reminder_loop

        reminder_task = asyncio.create_task(reminder_loop(stop_event))
    else:
        logger.warning("Reminder scheduler is disabled; WhatsApp reminders will not be sent.")

    logger.info("Business AI Receptionist started (env=%s)", settings.app_env)

    yield

    stop_event.set()
    if reminder_task is not None:
        try:
            # Bounded wait: a hung scheduler must not block shutdown forever.
            await asyncio.wait_for(reminder_task, timeout=10)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            reminder_task.cancel()
    await dispose_db()
    logger.info("Shutdown complete.")


app = FastAPI(
    title="Business AI Receptionist API",
    version="1.0.0",
    description=(
        "Multi-tenant AI phone receptionist for businesses. Every endpoint returns "
        "the same envelope: `{success, data, meta, message, request_id, timestamp}` "
        "on success and `{success, error: {code, message, details}, request_id, "
        "timestamp}` on failure."
    ),
    lifespan=lifespan,
    # Interactive docs are useful in development and an information leak in
    # production, where they advertise every endpoint and schema.
    docs_url=None if settings.is_production else "/docs",
    redoc_url=None if settings.is_production else "/redoc",
    openapi_url=None if settings.is_production else "/openapi.json",
)

register_exception_handlers(app)

# Added in reverse execution order: CORS runs innermost, request context outermost.
app.add_middleware(
    CORSMiddleware,
    # Exact origins only. A regex like `.*\.vercel\.app` would let any attacker's
    # preview deployment read authenticated responses from a logged-in business.
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
    expose_headers=["X-Request-ID"],
    max_age=600,
)
app.add_middleware(RateLimitMiddleware, rules=RATE_LIMIT_RULES)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RequestContextMiddleware)

app.include_router(api_router)
# Webhooks sit outside /api/v1: they answer to VAPI's and Meta's fixed contracts,
# not to our envelope, and they authenticate by signature rather than JWT.
app.include_router(vapi_webhooks.router)
app.include_router(whatsapp_webhooks.router)


@app.get("/health/ready", tags=["system"], summary="Readiness probe")
async def readiness() -> dict:
    """Whether this instance can actually serve a call, not merely that it is running.

    Kept separate from /health on purpose. Render uses /health to decide whether
    a deploy succeeded, so making that one depend on the database would turn a
    brief database blip into a failed deploy and a rollback. This endpoint is
    for an external uptime monitor, which wants the opposite: the truth about
    whether a real caller would be served right now.

    Returns 503 when the database is unreachable, because a monitor that only
    reads the body will otherwise treat a broken system as healthy.
    """
    from sqlalchemy import select, text as sql_text

    from app.db.models import Business
    from app.db.session import SessionLocal

    checks: dict[str, object] = {}
    healthy = True

    try:
        async with SessionLocal() as db:
            await db.execute(sql_text("SELECT 1"))
            # Counting businesses proves the schema is present too, not just
            # that a connection opened: a database restored without migrations
            # answers SELECT 1 perfectly well and then fails every real query.
            total = len((await db.execute(select(Business.id))).scalars().all())
            checks["database"] = {"ok": True, "businesses": total}
    except Exception as exc:  # noqa: BLE001
        healthy = False
        logger.exception("Readiness check failed on the database.")
        checks["database"] = {"ok": False, "error": type(exc).__name__}

    # Configuration gaps are reported but not fatal: a deployment with no
    # WhatsApp account still answers calls, and paging someone at night for it
    # would train them to ignore the alert that matters.
    checks["config"] = {
        "vapi": bool(settings.vapi_api_key),
        "google_oauth": bool(settings.google_client_id),
        "whatsapp": bool(settings.whatsapp_access_token),
    }

    if not healthy:
        raise HTTPException(status_code=503, detail={"status": "degraded", "checks": checks})

    return ok({"status": "ready", "checks": checks})


@app.get("/health", tags=["system"], summary="Liveness probe")
async def health() -> dict:
    return ok(
        {
            "status": "ok",
            "env": settings.app_env,
            "version": app.version,
        }
    )
