"""Owner-facing notifications and waitlist recovery.

These exist for retention rather than function. A business owner in their first
weeks is deciding whether this thing is working, and an AI that answers the
phone silently feels like nothing is happening. A message on every booking and a
summary each morning make the value visible on the days nothing goes wrong.

The waitlist is the one that pays for itself: a caller who wanted a full slot is
otherwise simply lost, and matching them to a cancellation turns that into a
booking without anyone doing anything.
"""

import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Appointment,
    AppointmentStatus,
    Business,
    Call,
    CallOutcome,
    Customer,
    MessageKind,
    MessageStatus,
    WaitlistEntry,
    WhatsAppMessage,
)
from app.config import get_settings
from app.db.session import SessionLocal
from app.services import whatsapp

logger = logging.getLogger(__name__)


def _owner_number(business: Business) -> str:
    """Where owner notices go. Falls back to the business contact number."""
    return business.owner_notify_phone or business.contact_phone


async def notify_owner_of_booking(
    db: AsyncSession, business: Business, appointment: Appointment, customer: Customer
) -> None:
    """Tell the owner a booking just happened. Never raises: a failed notice
    must not roll back the booking it is describing."""
    if not (business.notify_on_booking and business.whatsapp_enabled):
        return
    number = _owner_number(business)
    if not number:
        logger.info("No owner number set for %s; skipping booking alert.", business.slug)
        return

    spec = whatsapp.resolve_template(MessageKind.OWNER_BOOKING_ALERT, business.primary_language)
    variables = {
        "business_name": business.name,
        "customer_name": customer.name or customer.phone,
        "appointment_time": whatsapp.format_appointment_time(
            appointment.starts_at, business.timezone
        ),
        "service_reason": appointment.reason or "not given",
    }

    try:
        db.add(
            WhatsAppMessage(
                business_id=business.id,
                appointment_id=appointment.id,
                to_phone=number,
                kind=MessageKind.OWNER_BOOKING_ALERT,
                status=MessageStatus.PENDING,
                template_name=spec.name,
                language_code=spec.language_code,
                payload=variables,
                rendered_preview=spec.render(variables),
                scheduled_for=datetime.now(timezone.utc),
            )
        )
        await db.flush()
    except Exception:  # noqa: BLE001
        logger.exception("Could not queue owner booking alert for %s", business.slug)


async def notify_owner_calendar_disconnected(db: AsyncSession, business: Business) -> bool:
    """Tell the owner their calendar connection died.

    This is the one owner message that is urgent rather than informational: with
    no calendar the agent cannot read availability, so it refuses every booking
    request. Without this the first sign of trouble is a caller being turned
    away, which the owner only learns about if that caller complains.

    Deliberately not gated on `notify_on_booking`: that switch is for the
    routine per-booking alerts an owner may reasonably mute, and muting them
    should not also silence an outage. Never raises, because it runs inside the
    token-refresh failure path and must not mask the original error.
    """
    if not business.whatsapp_enabled:
        logger.warning(
            "Calendar disconnected for %s but WhatsApp is off; owner not alerted.",
            business.slug,
        )
        return False

    number = _owner_number(business)
    if not number:
        logger.warning(
            "Calendar disconnected for %s but no owner number is set.", business.slug
        )
        return False

    spec = whatsapp.resolve_template(
        MessageKind.OWNER_CALENDAR_DISCONNECTED, business.primary_language
    )
    variables = {
        "business_name": business.name,
        "dashboard_url": get_settings().dashboard_url or "your dashboard",
    }

    try:
        db.add(
            WhatsAppMessage(
                business_id=business.id,
                to_phone=number,
                kind=MessageKind.OWNER_CALENDAR_DISCONNECTED,
                status=MessageStatus.PENDING,
                template_name=spec.name,
                language_code=spec.language_code,
                payload=variables,
                rendered_preview=spec.render(variables),
                scheduled_for=datetime.now(timezone.utc),
            )
        )
        await db.flush()
        logger.info("Queued calendar-disconnected alert for %s", business.slug)
        return True
    except Exception:  # noqa: BLE001
        logger.exception("Could not queue calendar-disconnected alert for %s", business.slug)
        return False


# --------------------------------------------------------------------------- #
# Daily summary
# --------------------------------------------------------------------------- #
async def send_due_daily_summaries() -> int:
    """Send yesterday's numbers to each owner, once per local morning.

    Guarded by a stored date rather than a timestamp, so a restart cannot cause
    a second send, and a missed hour still goes out later the same day rather
    than being skipped entirely.
    """
    sent = 0
    async with SessionLocal() as db:
        try:
            businesses = (
                await db.execute(
                    select(Business).where(
                        Business.is_active.is_(True),
                        Business.daily_summary_enabled.is_(True),
                        Business.whatsapp_enabled.is_(True),
                    )
                )
            ).scalars().all()

            for business in businesses:
                zone = ZoneInfo(business.timezone)
                local_now = datetime.now(zone)
                today = local_now.strftime("%Y-%m-%d")

                if business.last_daily_summary_on == today:
                    continue
                if local_now.hour < business.daily_summary_hour:
                    continue

                number = _owner_number(business)
                if not number:
                    continue

                day_start = local_now.replace(
                    hour=0, minute=0, second=0, microsecond=0
                ).astimezone(timezone.utc)
                yesterday_start = day_start - timedelta(days=1)

                async def count(stmt) -> int:
                    return (await db.execute(stmt)).scalar_one() or 0

                calls = await count(
                    select(func.count(Call.id)).where(
                        Call.business_id == business.id,
                        Call.created_at >= yesterday_start,
                        Call.created_at < day_start,
                    )
                )
                booked = await count(
                    select(func.count(Appointment.id)).where(
                        Appointment.business_id == business.id,
                        Appointment.created_at >= yesterday_start,
                        Appointment.created_at < day_start,
                    )
                )
                cancelled = await count(
                    select(func.count(Appointment.id)).where(
                        Appointment.business_id == business.id,
                        Appointment.status == AppointmentStatus.CANCELLED,
                        Appointment.updated_at >= yesterday_start,
                        Appointment.updated_at < day_start,
                    )
                )
                today_count = await count(
                    select(func.count(Appointment.id)).where(
                        Appointment.business_id == business.id,
                        Appointment.status.in_(
                            [AppointmentStatus.SCHEDULED, AppointmentStatus.RESCHEDULED]
                        ),
                        Appointment.starts_at >= day_start,
                        Appointment.starts_at < day_start + timedelta(days=1),
                    )
                )

                # A day with no activity at all is not worth a message. Sending
                # "0 calls, 0 bookings" every morning trains the owner to ignore
                # the channel, which then hides the messages that matter.
                if calls == 0 and booked == 0 and today_count == 0:
                    business.last_daily_summary_on = today
                    continue

                spec = whatsapp.resolve_template(
                    MessageKind.OWNER_DAILY_SUMMARY, business.primary_language
                )
                variables = {
                    "business_name": business.name,
                    "calls_total": str(calls),
                    "booked": str(booked),
                    "cancelled": str(cancelled),
                    "today_count": str(today_count),
                }
                db.add(
                    WhatsAppMessage(
                        business_id=business.id,
                        to_phone=number,
                        kind=MessageKind.OWNER_DAILY_SUMMARY,
                        status=MessageStatus.PENDING,
                        template_name=spec.name,
                        language_code=spec.language_code,
                        payload=variables,
                        rendered_preview=spec.render(variables),
                        scheduled_for=datetime.now(timezone.utc),
                    )
                )
                business.last_daily_summary_on = today
                sent += 1

            await db.commit()
        except Exception:
            await db.rollback()
            logger.exception("Daily summary run failed; rolled back.")
            raise

    if sent:
        logger.info("Queued %d daily summary message(s).", sent)
    return sent


# --------------------------------------------------------------------------- #
# Waitlist
# --------------------------------------------------------------------------- #
async def notify_waitlist_for_freed_slot(
    db: AsyncSession, business: Business, starts_at: datetime, ends_at: datetime
) -> int:
    """Tell people waiting for this window that it has opened.

    Called when an appointment is cancelled. Notifies in the order people asked,
    capped so one cancellation does not message a large group who then all call
    at once and mostly find it taken.
    """
    if not business.whatsapp_enabled:
        return 0

    candidates = (
        await db.execute(
            select(WaitlistEntry)
            .where(
                WaitlistEntry.business_id == business.id,
                WaitlistEntry.status == "waiting",
                # Their window overlaps the freed slot.
                WaitlistEntry.preferred_from < ends_at,
                WaitlistEntry.preferred_to > starts_at,
            )
            .order_by(WaitlistEntry.created_at.asc())
            .limit(3)
        )
    ).scalars().all()

    notified = 0
    for entry in candidates:
        customer = (
            await db.execute(select(Customer).where(Customer.id == entry.customer_id))
        ).scalar_one_or_none()
        if customer is None:
            continue

        spec = whatsapp.resolve_template(
            MessageKind.WAITLIST_SLOT_OPEN, customer.preferred_language
        )
        variables = {
            "customer_name": customer.name or "there",
            "business_name": business.name,
            "appointment_time": whatsapp.format_appointment_time(starts_at, business.timezone),
            "business_phone": business.contact_phone or business.phone_number,
        }
        db.add(
            WhatsAppMessage(
                business_id=business.id,
                to_phone=customer.phone,
                kind=MessageKind.WAITLIST_SLOT_OPEN,
                status=MessageStatus.PENDING,
                template_name=spec.name,
                language_code=spec.language_code,
                payload=variables,
                rendered_preview=spec.render(variables),
                scheduled_for=datetime.now(timezone.utc),
            )
        )
        entry.status = "notified"
        entry.notified_at = datetime.now(timezone.utc)
        notified += 1

    if notified:
        await db.flush()
        logger.info(
            "Waitlist: notified %d customer(s) about a freed slot at %s", notified, starts_at
        )
    return notified
