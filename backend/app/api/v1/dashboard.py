"""Dashboard overview statistics."""

import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter
from sqlalchemy import func, select

from app.core.deps import ActiveBusiness, CurrentUserDep, DbSession
from app.core.response import ok
from app.db.models import (
    Appointment,
    AppointmentStatus,
    Call,
    CallOutcome,
    Business,
    MessageStatus,
    WhatsAppMessage,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

ACTIVE_STATUSES = (AppointmentStatus.SCHEDULED, AppointmentStatus.RESCHEDULED)


@router.get("/stats", summary="Overview counters")
async def stats(business_id: ActiveBusiness, db: DbSession, _user: CurrentUserDep) -> dict:
    """Counters for the overview page.

    "Today" is the business's local day, not UTC. In IST that is a 5.5-hour
    difference, so a UTC day boundary would show the wrong number for most of
    the business's evening.
    """
    business = (await db.execute(select(Business).where(Business.id == business_id))).scalar_one()
    zone = ZoneInfo(business.timezone)
    now = datetime.now(timezone.utc)
    local_now = now.astimezone(zone)

    day_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
    day_end = day_start + timedelta(days=1)

    async def count(stmt) -> int:
        return (await db.execute(stmt)).scalar_one() or 0

    calls_total = await count(
        select(func.count(Call.id)).where(Call.business_id == business_id)
    )
    calls_today = await count(
        select(func.count(Call.id)).where(
            Call.business_id == business_id, Call.created_at >= day_start, Call.created_at < day_end
        )
    )
    booked_by_agent = await count(
        select(func.count(Call.id)).where(
            Call.business_id == business_id, Call.outcome == CallOutcome.BOOKED
        )
    )
    no_details = await count(
        select(func.count(Call.id)).where(
            Call.business_id == business_id, Call.outcome == CallOutcome.NO_DETAILS
        )
    )
    appointments_upcoming = await count(
        select(func.count(Appointment.id)).where(
            Appointment.business_id == business_id,
            Appointment.status.in_(ACTIVE_STATUSES),
            Appointment.starts_at >= now,
        )
    )
    appointments_today = await count(
        select(func.count(Appointment.id)).where(
            Appointment.business_id == business_id,
            Appointment.status.in_(ACTIVE_STATUSES),
            Appointment.starts_at >= day_start,
            Appointment.starts_at < day_end,
        )
    )
    cancelled = await count(
        select(func.count(Appointment.id)).where(
            Appointment.business_id == business_id, Appointment.status == AppointmentStatus.CANCELLED
        )
    )
    whatsapp_sent = await count(
        select(func.count(WhatsAppMessage.id)).where(
            WhatsAppMessage.business_id == business_id,
            WhatsAppMessage.status.in_(
                [MessageStatus.SENT, MessageStatus.DELIVERED, MessageStatus.READ]
            ),
        )
    )
    whatsapp_failed = await count(
        select(func.count(WhatsAppMessage.id)).where(
            WhatsAppMessage.business_id == business_id, WhatsAppMessage.status == MessageStatus.FAILED
        )
    )

    avg_duration = (
        await db.execute(
            select(func.avg(Call.duration_seconds)).where(
                Call.business_id == business_id, Call.duration_seconds > 0
            )
        )
    ).scalar_one_or_none()

    return ok(
        {
            "calls_total": calls_total,
            "calls_today": calls_today,
            "appointments_upcoming": appointments_upcoming,
            "appointments_today": appointments_today,
            "booked_by_agent": booked_by_agent,
            "cancelled": cancelled,
            "no_details": no_details,
            "conversion_rate": round(booked_by_agent / calls_total * 100, 1) if calls_total else 0.0,
            "whatsapp_sent": whatsapp_sent,
            "whatsapp_failed": whatsapp_failed,
            "avg_call_duration_seconds": int(avg_duration or 0),
        }
    )
