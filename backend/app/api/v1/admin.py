"""Platform administration: the operator's view across every tenant.

Distinct from `/businesses/me`, which is deliberately scoped to one tenant and
therefore unusable by a superadmin (who has no `business_id`). Everything here
is superadmin-only and crosses tenant boundaries by design, which is exactly why
it lives in its own module rather than being a flag on the tenant endpoints.
"""

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter
from sqlalchemy import func, select

from app.config import get_settings
from app.core.deps import DbSession, RequireSuperadmin
from app.core.response import ok
from app.db.models import (
    Appointment,
    AppointmentStatus,
    Business,
    CalendarCredential,
    Call,
    User,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/businesses", summary="Every tenant, with setup and activity status")
async def list_businesses(db: DbSession, _admin: RequireSuperadmin) -> dict:
    """One row per client, with enough status to see what still needs finishing.

    The counts are aggregated in two grouped queries rather than per business,
    so this stays one round trip regardless of how many clients there are.
    """
    settings = get_settings()
    businesses = (
        await db.execute(select(Business).order_by(Business.created_at.desc()))
    ).scalars().all()

    if not businesses:
        return ok([])

    ids = [b.id for b in businesses]

    call_counts = dict(
        (
            await db.execute(
                select(Call.business_id, func.count(Call.id))
                .where(Call.business_id.in_(ids))
                .group_by(Call.business_id)
            )
        ).all()
    )
    appointment_counts = dict(
        (
            await db.execute(
                select(Appointment.business_id, func.count(Appointment.id))
                .where(
                    Appointment.business_id.in_(ids),
                    Appointment.status.in_(
                        [AppointmentStatus.SCHEDULED, AppointmentStatus.RESCHEDULED]
                    ),
                )
                .group_by(Appointment.business_id)
            )
        ).all()
    )
    connected_calendars = {
        row[0]
        for row in (
            await db.execute(
                select(CalendarCredential.business_id).where(
                    CalendarCredential.business_id.in_(ids),
                    CalendarCredential.is_connected.is_(True),
                )
            )
        ).all()
    }
    owners = dict(
        (
            await db.execute(
                select(User.business_id, User.email).where(User.business_id.in_(ids))
            )
        ).all()
    )

    whatsapp_ready = bool(settings.whatsapp_access_token and settings.whatsapp_phone_number_id)
    recent_cutoff = datetime.now(timezone.utc) - timedelta(days=7)

    recent_calls = dict(
        (
            await db.execute(
                select(Call.business_id, func.count(Call.id))
                .where(Call.business_id.in_(ids), Call.created_at >= recent_cutoff)
                .group_by(Call.business_id)
            )
        ).all()
    )

    return ok(
        [
            {
                "id": b.id,
                "name": b.name,
                "slug": b.slug,
                "business_type": b.business_type,
                "phone_number": b.phone_number,
                "city": b.city,
                "owner_email": owners.get(b.id, ""),
                "is_active": b.is_active,
                "created_at": b.created_at.isoformat(),
                "calls_total": call_counts.get(b.id, 0),
                "calls_last_7d": recent_calls.get(b.id, 0),
                "appointments_upcoming": appointment_counts.get(b.id, 0),
                # The three things that must all be true before a client is live.
                "setup": {
                    "voice_agent": bool(b.vapi_assistant_id),
                    "google_calendar": b.id in connected_calendars,
                    "whatsapp": whatsapp_ready and b.whatsapp_enabled,
                },
            }
            for b in businesses
        ]
    )


@router.get("/stats", summary="Platform totals across all tenants")
async def platform_stats(db: DbSession, _admin: RequireSuperadmin) -> dict:
    async def count(stmt) -> int:
        return (await db.execute(stmt)).scalar_one() or 0

    total = await count(select(func.count(Business.id)))
    active = await count(select(func.count(Business.id)).where(Business.is_active.is_(True)))
    live = await count(
        select(func.count(Business.id)).where(Business.vapi_assistant_id != "")
    )
    calls = await count(select(func.count(Call.id)))
    appointments = await count(select(func.count(Appointment.id)))

    # Usage cost matters here: VAPI bills per minute, so a heavy client can be
    # unprofitable on a flat plan. Surfacing the total early makes that visible.
    cost = await count(select(func.coalesce(func.sum(Call.cost_paise), 0)))

    return ok(
        {
            "businesses_total": total,
            "businesses_active": active,
            "businesses_live": live,
            "calls_total": calls,
            "appointments_total": appointments,
            "call_cost_paise": cost,
        }
    )
