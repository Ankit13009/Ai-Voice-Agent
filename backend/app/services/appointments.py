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
    Clinic,
    Doctor,
    Language,
    MessageKind,
    Patient,
)
from app.services import google_calendar as gcal
from app.services import whatsapp

logger = logging.getLogger(__name__)

# Reminders closer than this to the appointment are pointless: the patient is
# already travelling. The row is skipped rather than queued in the past.
MIN_REMINDER_LEAD = timedelta(minutes=10)


async def get_or_create_patient(
    db: AsyncSession,
    *,
    clinic_id: str,
    phone: str,
    name: str = "",
    language: Language = Language.MIXED,
) -> Patient:
    """Look up a patient by phone within the clinic, creating them if new.

    Scoped to `clinic_id` by the unique constraint on (clinic_id, phone), so two
    clinics can hold the same phone number as separate patient records, which is
    both correct and required for tenant isolation.
    """
    patient = (
        await db.execute(
            select(Patient).where(Patient.clinic_id == clinic_id, Patient.phone == phone)
        )
    ).scalar_one_or_none()

    if patient is None:
        patient = Patient(
            clinic_id=clinic_id, phone=phone, name=name, preferred_language=language
        )
        db.add(patient)
        await db.flush()
        logger.info("Created patient %s for clinic %s", patient.id, clinic_id)
        return patient

    # Fill in a name we didn't have before; never overwrite one we did.
    if name and not patient.name:
        patient.name = name
    if language != Language.MIXED:
        patient.preferred_language = language
    await db.flush()
    return patient


async def _resolve_doctor(db: AsyncSession, clinic_id: str, doctor_id: str | None) -> Doctor | None:
    if not doctor_id:
        return None
    doctor = (
        await db.execute(
            select(Doctor).where(Doctor.id == doctor_id, Doctor.clinic_id == clinic_id)
        )
    ).scalar_one_or_none()
    if doctor is None:
        raise NotFoundError("Doctor")
    return doctor


async def _resolve_duration(
    db: AsyncSession,
    clinic: Clinic,
    doctor: Doctor | None,
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
                    AppointmentType.clinic_id == clinic.id,
                )
            )
        ).scalar_one_or_none()
        if appt_type:
            return appt_type.duration_minutes
    if doctor:
        return doctor.consultation_duration_minutes
    return clinic.slot_duration_minutes


async def _check_local_conflict(
    db: AsyncSession,
    clinic_id: str,
    start: datetime,
    end: datetime,
    doctor_id: str | None,
    exclude_appointment_id: str | None = None,
) -> None:
    """Reject a double-booking against our own rows.

    The Google freeBusy check catches events already on the calendar, but two
    appointments created seconds apart can both pass that check before either
    reaches Google. This is the guard that closes that window.
    """
    stmt = select(Appointment).where(
        Appointment.clinic_id == clinic_id,
        Appointment.status.in_([AppointmentStatus.SCHEDULED, AppointmentStatus.RESCHEDULED]),
        Appointment.starts_at < end,
        Appointment.ends_at > start,
    )
    if doctor_id:
        stmt = stmt.where(Appointment.doctor_id == doctor_id)
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
    clinic: Clinic,
    patient: Patient,
    appointment: Appointment,
    *,
    confirmation_kind: MessageKind = MessageKind.CONFIRMATION,
) -> None:
    """Queue the confirmation (immediately) and both reminders (scheduled)."""
    now = datetime.now(timezone.utc)

    await whatsapp.queue_message(
        db,
        clinic=clinic,
        patient=patient,
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
            clinic=clinic,
            patient=patient,
            appointment=appointment,
            kind=kind,
            scheduled_for=send_at,
        )


async def book_appointment(
    db: AsyncSession,
    clinic: Clinic,
    *,
    patient_name: str,
    patient_phone: str,
    starts_at: datetime,
    doctor_id: str | None = None,
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

    doctor = await _resolve_doctor(db, clinic.id, doctor_id)
    minutes = await _resolve_duration(db, clinic, doctor, appointment_type_id, duration_minutes)
    ends_at = starts_at + timedelta(minutes=minutes)

    await _check_local_conflict(db, clinic.id, starts_at, ends_at, doctor_id)

    # Re-verify against the live calendar. The slot may have been offered on the
    # call a minute ago and taken by a walk-in since.
    if not await gcal.is_slot_free(db, clinic, starts_at, ends_at, doctor):
        raise SlotUnavailableError()

    patient = await get_or_create_patient(
        db, clinic_id=clinic.id, phone=patient_phone, name=patient_name, language=language
    )

    # Calendar first: a failure here must abort the booking, not leave a row that
    # claims an appointment nobody's calendar knows about.
    event_id, calendar_id = await gcal.create_event(
        db,
        clinic,
        patient_name=patient.name or patient_name,
        patient_phone=patient.phone,
        reason=reason,
        start=starts_at,
        end=ends_at,
        doctor=doctor,
    )

    appointment = Appointment(
        clinic_id=clinic.id,
        patient_id=patient.id,
        doctor_id=doctor.id if doctor else None,
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

    await _queue_appointment_messages(db, clinic, patient, appointment)

    logger.info(
        "Booked appointment %s for clinic %s at %s", appointment.id, clinic.id, starts_at
    )
    return appointment


async def reschedule_appointment(
    db: AsyncSession,
    clinic: Clinic,
    appointment: Appointment,
    *,
    starts_at: datetime,
    doctor_id: str | None = None,
    reason: str = "",
) -> Appointment:
    """Move an appointment to a new time, in our records and on the calendar."""
    if appointment.status in (AppointmentStatus.CANCELLED, AppointmentStatus.COMPLETED):
        raise ConflictError(f"This appointment is already {appointment.status.value}.")

    starts_at = starts_at.astimezone(timezone.utc)
    if starts_at <= datetime.now(timezone.utc):
        raise ConflictError("The new appointment time is in the past.")

    target_doctor_id = doctor_id or appointment.doctor_id
    doctor = await _resolve_doctor(db, clinic.id, target_doctor_id)
    minutes = int((appointment.ends_at - appointment.starts_at).total_seconds() // 60)
    ends_at = starts_at + timedelta(minutes=minutes)

    await _check_local_conflict(
        db, clinic.id, starts_at, ends_at, target_doctor_id, exclude_appointment_id=appointment.id
    )
    if not await gcal.is_slot_free(db, clinic, starts_at, ends_at, doctor):
        raise SlotUnavailableError()

    await gcal.update_event_time(db, clinic, appointment, start=starts_at, end=ends_at)

    previous_start = appointment.starts_at
    appointment.starts_at = starts_at
    appointment.ends_at = ends_at
    appointment.doctor_id = target_doctor_id
    appointment.status = AppointmentStatus.RESCHEDULED
    if reason:
        appointment.notes = f"{appointment.notes}\nRescheduled: {reason}".strip()
    await db.flush()

    # The queued reminders point at the old time, so drop them and queue fresh
    # ones for the new time.
    await whatsapp.cancel_pending_messages(db, appointment.id)

    patient = (
        await db.execute(select(Patient).where(Patient.id == appointment.patient_id))
    ).scalar_one()
    await _queue_appointment_messages(
        db, clinic, patient, appointment, confirmation_kind=MessageKind.RESCHEDULE
    )

    logger.info(
        "Rescheduled appointment %s from %s to %s", appointment.id, previous_start, starts_at
    )
    return appointment


async def cancel_appointment(
    db: AsyncSession,
    clinic: Clinic,
    appointment: Appointment,
    *,
    reason: str = "",
    notify_patient: bool = True,
) -> Appointment:
    """Cancel: remove the calendar event, kill pending reminders, notify."""
    if appointment.status == AppointmentStatus.CANCELLED:
        # Idempotent: re-cancelling is a no-op, not an error. The dashboard and
        # the agent can both act on stale state without failing the user.
        return appointment

    await gcal.delete_event(db, clinic, appointment)

    appointment.status = AppointmentStatus.CANCELLED
    appointment.cancellation_reason = reason
    await db.flush()

    # Always kill the reminders, even when not notifying. A cancelled patient
    # must never get "your appointment is tomorrow".
    await whatsapp.cancel_pending_messages(db, appointment.id)

    if notify_patient and clinic.whatsapp_enabled:
        patient = (
            await db.execute(select(Patient).where(Patient.id == appointment.patient_id))
        ).scalar_one()
        await whatsapp.queue_message(
            db,
            clinic=clinic,
            patient=patient,
            appointment=appointment,
            kind=MessageKind.CANCELLATION,
            scheduled_for=datetime.now(timezone.utc),
        )

    logger.info("Cancelled appointment %s for clinic %s", appointment.id, clinic.id)
    return appointment


async def find_patient_appointment(
    db: AsyncSession, clinic_id: str, phone: str
) -> Appointment | None:
    """The caller's next upcoming appointment, for "cancel my appointment".

    Matched on the calling number within the clinic, so the agent never needs to
    read an appointment id aloud, and can never reach another clinic's records.
    """
    stmt = (
        select(Appointment)
        .join(Patient, Appointment.patient_id == Patient.id)
        .where(
            Appointment.clinic_id == clinic_id,
            Patient.phone == phone,
            Appointment.status.in_([AppointmentStatus.SCHEDULED, AppointmentStatus.RESCHEDULED]),
            Appointment.starts_at > datetime.now(timezone.utc),
        )
        .order_by(Appointment.starts_at.asc())
        .limit(1)
    )
    return (await db.execute(stmt)).scalar_one_or_none()
