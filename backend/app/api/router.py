"""Aggregates every v1 route under a single `/api/v1` prefix.

Versioning the prefix from day one means a breaking change later can ship as
`/api/v2` while existing dashboards keep working, instead of forcing a
lockstep frontend deploy.
"""

from fastapi import APIRouter

from app.api.v1 import (
    admin,
    appointments,
    auth,
    calls,
    businesses,
    dashboard,
    integrations,
    messages,
    onboarding,
    customers,
)

api_router = APIRouter(prefix="/api/v1")

api_router.include_router(auth.router)
api_router.include_router(admin.router)
api_router.include_router(dashboard.router)
api_router.include_router(businesses.router)
api_router.include_router(appointments.router)
api_router.include_router(calls.router)
api_router.include_router(customers.router)
api_router.include_router(messages.router)
api_router.include_router(integrations.router)
api_router.include_router(onboarding.router)
