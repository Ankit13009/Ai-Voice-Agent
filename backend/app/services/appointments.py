"""Appointment business logic, shared by the dashboard API and the voice agent.

Both entry points (a receptionist clicking in the dashboard, and the AI calling
a tool mid-conversation) must produce identical results: the same calendar
write, the same WhatsApp queue, the same audit trail. So the rules live here
once and both callers are thin wrappers.

Ordering matters in `book_appointment`. The calendar event is created *before*
the row is committed, because a calendar write that fails after we have promised
the caller a time is far worse than a database row we can roll back.
"""

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError, SlotUnavailableError
from app.db.models import (
    Appointment,
    AppointmentStatus,
    AppointmentType,
    Business,
    StaffMember,
    Language,
    MessageKind,
    Customer,
)
from app.services import google_calendar as gcal
from app.services import whatsapp

logger = logging.getLogger(__name__)

# Reminders closer than this to the appointment are pointless: the customer is
# already travelling. The row is skipped rather than queued in the past.
MIN_REMINDER_LEAD = timedelta(minutes=10)


async def get_or_create_patient(
    db: AsyncSession,
    *,
    business_id: str,
    phone: str,
    name: str = "",
    language: Language = Language.MIXED,
) -> Customer:
    """Look up a customer by phone within the business, creating them if new.

    Scoped to `business_id` by the unique constraint on (business_id, phone), so two
    businesses can hold the same phone number as separate customer records, which is
    both correct and required for tenant isolation.
    """
    customer = (
        await db.execute(
            select(Customer).where(Customer.business_id == business_id, Customer.phone == phone)
        )
    ).scalar_one_or_none()

    if customer is None:
        customer = Customer(
            business_id=business_id, phone=phone, name=name, preferred_language=language
        )
        db.add(customer)
        await db.flush()
        logger.info("Created customer %s for business %s", customer.id, business_id)
        return customer

    # Fill in a name we didn't have before; never overwrite one we did.
    if name and not customer.name:
        customer.name = name
    if language != Language.MIXED:
        customer.preferred_language = language
    await db.flush()
    return customer


async def _resolve_doctor(db: AsyncSession, business_id: str, staff_member_id: str | None) -> StaffMember | None:
    if not staff_member_id:
        return None
    staff_member = (
        await db.execute(
            select(StaffMember).where(StaffMember.id == staff_member_id, StaffMember.business_id == business_id)
        )
    ).scalar_one_or_none()
    if staff_member is None:
        raise NotFoundError("StaffMember")
    return staff_member


async def _resolve_duration(
    db: AsyncSession,
    business: Business,
    staff_member: StaffMember | None,
    appointment_type_id: str | None,
    explicit_minutes: int | None,
) -> int:
    if explicit_minutes:
        return explicit_minutes
    if appointment_type_id:
        appt_type = (
            await db.execute(
                select(AppointmentType).where(
                    AppointmentType.id == appointment_type_id,
                    AppointmentType.business_id == business.id,
                )
            )
        ).scalar_one_or_none()
        if appt_type:
            return appt_type.duration_minutes
    if staff_member:
        return staff_member.consultation_duration_minutes
    return business.slot_duration_minutes


async def _check_local_conflict(
    db: AsyncSession,
    business_id: str,
    start: datetime,
    end: datetime,
    staff_member_id: str | None,
    exclude_appointment_id: str | None = None,
) -> None:
    """Reject a double-booking against our own rows.

    The Google freeBusy check catches events already on the calendar, but two
    appointments created seconds apart can both pass that check before either
    reaches Google. This is the guard that closes that window.
    """
    stmt = select(Appointment).where(
        Appointment.business_id == business_id,
        Appointment.status.in_([AppointmentStatus.SCHEDULED, AppointmentStatus.RESCHEDULED]),
        Appointment.starts_at < end,
        Appointment.ends_at > start,
    )
    if staff_member_id:
        stmt = stmt.where(Appointment.staff_member_id == staff_member_id)
    if exclude_appointment_id:
        stmt = stmt.where(Appointment.id != exclude_appointment_id)

    clash = (await db.execute(stmt.limit(1))).scalar_one_or_none()
    if clash is not None:
        raise SlotUnavailableError(
            "That time was just taken. Please choose another slot.",
            log_context={"conflicting_appointment_id": clash.id},
        )


async def _queue_appointment_messages(
    db: AsyncSession,
    business: Business,
    customer: Customer,
    appointment: Appointment,
    *,
    confirmation_kind: MessageKind = MessageKind.CONFIRMATION,
) -> None:
    """Queue the confirmation (immediately) and both reminders (scheduled)."""
    now = datetime.now(timezone.utc)

    await whatsapp.queue_message(
        db,
        business=business,
        customer=customer,
        appointment=appointment,
        kind=confirmation_kind,
        scheduled_for=now,  # due immediately; the scheduler picks it up next tick
    )

    for kind, lead in (
        (MessageKind.REMINDER_24H, timedelta(hours=24)),
        (MessageKind.REMINDER_2H, timedelta(hours=2)),
    ):
        send_at = appointment.starts_at - lead
        if send_at - now < MIN_REMINDER_LEAD:
            logger.info(
                "Skipping %s for appointment %s: too close to the appointment.",
                kind.value,
                appointment.id,
            )
            continue
        await whatsapp.queue_message(
            db,
            business=business,
            customer=customer,
            appointment=appointment,
            kind=kind,
            scheduled_for=send_at,
        )


async def book_appointment(
    db: AsyncSession,
    business: Business,
    *,
    customer_name: str,
    customer_phone: str,
    starts_at: datetime,
    staff_member_id: str | None = None,
    appointment_type_id: str | None = None,
    duration_minutes: int | None = None,
    reason: str = "",
    notes: str = "",
    language: Language = Language.MIXED,
    call_id: str | None = None,
) -> Appointment:
    """Book an appointment: verify the slot, write to Google, persist, queue WhatsApp."""
    if starts_at.tzinfo is None:
        raise ValueError("starts_at must be timezone-aware.")
    starts_at = starts_at.astimezone(timezone.utc)

    if starts_at <= datetime.now(timezone.utc):
        raise ConflictError("That appointment time is in the past.")

    staff_member = await _resolve_doctor(db, business.id, staff_member_id)
    minutes = await _resolve_duration(db, business, staff_member, appointment_type_id, duration_minutes)
    ends_at = starts_at + timedelta(minutes=minutes)

    await _check_local_conflict(db, business.id, starts_at, ends_at, staff_member_id)

    # Re-verify against the live calendar. The slot may have been offered on the
    # call a minute ago and taken by a walk-in since.
    if not await gcal.is_slot_free(db, business, starts_at, ends_at, staff_member):
        raise SlotUnavailableError()

    customer = await get_or_create_patient(
        db, business_id=business.id, phone=customer_phone, name=customer_name, language=language
    )

    # Calendar first: a failure here must abort the booking, not leave a row that
    # claims an appointment nobody's calendar knows about.
    event_id, calendar_id = await gcal.create_event(
        db,
        business,
        customer_name=customer.name or customer_name,
        customer_phone=customer.phone,
        reason=reason,
        start=starts_at,
        end=ends_at,
        staff_member=staff_member,
    )

    appointment = Appointment(
        business_id=business.id,
        customer_id=customer.id,
        staff_member_id=staff_member.id if staff_member else None,
        appointment_type_id=appointment_type_id,
        call_id=call_id,
        starts_at=starts_at,
        ends_at=ends_at,
        status=AppointmentStatus.SCHEDULED,
        google_event_id=event_id,
        google_calendar_id=calendar_id,
        reason=reason,
        notes=notes,
    )
    db.add(appointment)
    await db.flush()

    await _queue_appointment_messages(db, business, customer, appointment)

    logger.info(
        "Booked appointment %s for business %s at %s", appointment.id, business.id, starts_at
    )
    return appointment


async def reschedule_appointment(
    db: AsyncSession,
    business: Business,
    appointment: Appointment,
    *,
    starts_at: datetime,
    staff_member_id: str | None = None,
    reason: str = "",
) -> Appointment:
    """Move an appointment to a new time, in our records and on the calendar."""
    if appointment.status in (AppointmentStatus.CANCELLED, AppointmentStatus.COMPLETED):
        raise ConflictError(f"This appointment is already {appointment.status.value}.")

    starts_at = starts_at.astimezone(timezone.utc)
    if starts_at <= datetime.now(timezone.utc):
        raise ConflictError("The new appointment time is in the past.")

    target_doctor_id = staff_member_id or appointment.staff_member_id
    staff_member = await _resolve_doctor(db, business.id, target_doctor_id)
    minutes = int((appointment.ends_at - appointment.starts_at).total_seconds() // 60)
    ends_at = starts_at + timedelta(minutes=minutes)

    await _check_local_conflict(
        db, business.id, starts_at, ends_at, target_doctor_id, exclude_appointment_id=appointment.id
    )
    if not await gcal.is_slot_free(db, business, starts_at, ends_at, staff_member):
        raise SlotUnavailableError()

    await gcal.update_event_time(db, business, appointment, start=starts_at, end=ends_at)

    previous_start = appointment.starts_at
    appointment.starts_at = starts_at
    appointment.ends_at = ends_at
    appointment.staff_member_id = target_doctor_id
    appointment.status = AppointmentStatus.RESCHEDULED
    if reason:
        appointment.notes = f"{appointment.notes}\nRescheduled: {reason}".strip()
    await db.flush()

    # The queued reminders point at the old time, so drop them and queue fresh
    # ones for the new time.
    await whatsapp.cancel_pending_messages(db, appointment.id)

    customer = (
        await db.execute(select(Customer).where(Customer.id == appointment.customer_id))
    ).scalar_one()
    await _queue_appointment_messages(
        db, business, customer, appointment, confirmation_kind=MessageKind.RESCHEDULE
    )

    logger.info(
        "Rescheduled appointment %s from %s to %s", appointment.id, previous_start, starts_at
    )
    return appointment


async def cancel_appointment(
    db: AsyncSession,
    business: Business,
    appointment: Appointment,
    *,
    reason: str = "",
    notify_customer: bool = True,
) -> Appointment:
    """Cancel: remove the calendar event, kill pending reminders, notify."""
    if appointment.status == AppointmentStatus.CANCELLED:
        # Idempotent: re-cancelling is a no-op, not an error. The dashboard and
        # the agent can both act on stale state without failing the user.
        return appointment

    await gcal.delete_event(db, business, appointment)

    appointment.status = AppointmentStatus.CANCELLED
    appointment.cancellation_reason = reason
    await db.flush()

    # Always kill the reminders, even when not notifying. A cancelled customer
    # must never get "your appointment is tomorrow".
    await whatsapp.cancel_pending_messages(db, appointment.id)

    if notify_customer and business.whatsapp_enabled:
        customer = (
            await db.execute(select(Customer).where(Customer.id == appointment.customer_id))
        ).scalar_one()
        await whatsapp.queue_message(
            db,
            business=business,
            customer=customer,
            appointment=appointment,
            kind=MessageKind.CANCELLATION,
            scheduled_for=datetime.now(timezone.utc),
        )

    logger.info("Cancelled appointment %s for business %s", appointment.id, business.id)
    return appointment


async def find_customer_appointment(
    db: AsyncSession, business_id: str, phone: str
) -> Appointment | None:
    """The caller's next upcoming appointment, for "cancel my appointment".

    Matched on the calling number within the business, so the agent never needs to
    read an appointment id aloud, and can never reach another business's records.
    """
    stmt = (
        select(Appointment)
        .join(Customer, Appointment.customer_id == Customer.id)
        .where(
            Appointment.business_id == business_id,
            Customer.phone == phone,
            Appointment.status.in_([AppointmentStatus.SCHEDULED, AppointmentStatus.RESCHEDULED]),
            Appointment.starts_at > datetime.now(timezone.utc),
        )
        .order_by(Appointment.starts_at.asc())
        .limit(1)
    )
    return (await db.execute(stmt)).scalar_one_or_none()
