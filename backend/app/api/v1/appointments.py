"""Appointment endpoints: availability, list, create, reschedule, cancel.

Every query filters on `clinic` (from `ActiveClinic`, which is derived from the
JWT). Single-appointment reads go through `scoped_get`, so an id belonging to
another clinic is a 404 rather than a leak.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Query, Request
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.core.deps import (
    ActiveClinic,
    CurrentUserDep,
    DbSession,
    Paging,
    scoped_get,
    write_audit_log,
)
from app.core.response import ok, paginated
from app.db.models import (
    Appointment,
    AppointmentStatus,
    Clinic,
    Doctor,
    Patient,
)
from app.schemas.appointment import (
    AppointmentCancel,
    AppointmentCreate,
    AppointmentReschedule,
)
from app.services import appointments as appointment_service
from app.services import google_calendar as gcal

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/appointments", tags=["appointments"])


async def _load_clinic(db, clinic_id: str) -> Clinic:
    return (await db.execute(select(Clinic).where(Clinic.id == clinic_id))).scalar_one()


def _serialize(appointment: Appointment, clinic: Clinic) -> dict:
    patient = appointment.patient
    doctor = appointment.doctor
    return {
        "id": appointment.id,
        "status": appointment.status.value,
        "starts_at": appointment.starts_at.isoformat(),
        "ends_at": appointment.ends_at.isoformat(),
        # Pre-rendered in the clinic's timezone so the browser never has to guess.
        "starts_at_local": appointment.starts_at.astimezone(
            ZoneInfo(clinic.timezone)
        ).strftime("%d %b %Y, %-I:%M %p"),
        "reason": appointment.reason,
        "notes": appointment.notes,
        "cancellation_reason": appointment.cancellation_reason,
        "patient": {
            "id": patient.id,
            "name": patient.name,
            "phone": patient.phone,
        }
        if patient
        else {"id": "", "name": "", "phone": ""},
        "doctor_id": appointment.doctor_id,
        "doctor_name": doctor.name if doctor else "",
        "call_id": appointment.call_id,
        "google_event_id": appointment.google_event_id,
        "synced_to_calendar": bool(appointment.google_event_id),
        "rescheduled_from_id": appointment.rescheduled_from_id,
        "created_at": appointment.created_at.isoformat(),
    }


@router.get("/availability", summary="Open slots on the clinic calendar")
async def availability(
    clinic_id: ActiveClinic,
    db: DbSession,
    _user: CurrentUserDep,
    date_from: Annotated[datetime | None, Query(description="ISO-8601 with offset.")] = None,
    date_to: Annotated[datetime | None, Query(description="ISO-8601 with offset.")] = None,
    doctor_id: Annotated[str | None, Query()] = None,
    duration_minutes: Annotated[int | None, Query(ge=5, le=240)] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> dict:
    """Live availability, computed from Google Calendar's busy windows."""
    clinic = await _load_clinic(db, clinic_id)
    now = datetime.now(timezone.utc)
    start = date_from or now
    end = date_to or (start + timedelta(days=14))

    if end <= start:
        from app.core.errors import BadRequestError

        raise BadRequestError("date_to must be after date_from.")
    if (end - start).days > 60:
        from app.core.errors import BadRequestError

        raise BadRequestError("Availability can be queried for at most 60 days at a time.")

    doctor = None
    if doctor_id:
        doctor = await scoped_get(db, Doctor, doctor_id, clinic_id, resource_name="Doctor")

    slots = await gcal.find_available_slots(
        db,
        clinic,
        start=start,
        end=end,
        doctor=doctor,
        duration_minutes=duration_minutes,
        limit=limit,
    )
    return ok(
        [
            {
                "starts_at": s.starts_at.isoformat(),
                "ends_at": s.ends_at.isoformat(),
                "label": s.label(clinic.timezone),
                "doctor_id": s.doctor_id,
                "doctor_name": s.doctor_name,
            }
            for s in slots
        ]
    )


@router.get("", summary="List appointments")
async def list_appointments(
    clinic_id: ActiveClinic,
    db: DbSession,
    paging: Paging,
    _user: CurrentUserDep,
    status: Annotated[AppointmentStatus | None, Query()] = None,
    doctor_id: Annotated[str | None, Query()] = None,
    date_from: Annotated[datetime | None, Query()] = None,
    date_to: Annotated[datetime | None, Query()] = None,
    search: Annotated[str | None, Query(max_length=120, description="Patient name or phone.")] = None,
) -> dict:
    clinic = await _load_clinic(db, clinic_id)

    filters = [Appointment.clinic_id == clinic_id]
    if status:
        filters.append(Appointment.status == status)
    if doctor_id:
        filters.append(Appointment.doctor_id == doctor_id)
    if date_from:
        filters.append(Appointment.starts_at >= date_from)
    if date_to:
        filters.append(Appointment.starts_at <= date_to)

    base = select(Appointment).where(*filters)
    if search:
        term = f"%{search.strip()}%"
        base = base.join(Patient, Appointment.patient_id == Patient.id).where(
            Patient.name.ilike(term) | Patient.phone.ilike(term)
        )

    total = (
        await db.execute(select(func.count()).select_from(base.subquery()))
    ).scalar_one()

    rows = (
        await db.execute(
            base.options(selectinload(Appointment.patient), selectinload(Appointment.doctor))
            .order_by(Appointment.starts_at.desc())
            .offset(paging.offset)
            .limit(paging.page_size)
        )
    ).scalars().all()

    return paginated(
        [_serialize(a, clinic) for a in rows],
        page=paging.page,
        page_size=paging.page_size,
        total=total,
    )


@router.get("/{appointment_id}", summary="One appointment")
async def get_appointment(
    appointment_id: str, clinic_id: ActiveClinic, db: DbSession, _user: CurrentUserDep
) -> dict:
    clinic = await _load_clinic(db, clinic_id)
    appointment = await scoped_get(
        db, Appointment, appointment_id, clinic_id, resource_name="Appointment"
    )
    await db.refresh(appointment, ["patient", "doctor"])
    return ok(_serialize(appointment, clinic))


@router.post("", status_code=201, summary="Book an appointment from the dashboard")
async def create_appointment(
    payload: AppointmentCreate,
    clinic_id: ActiveClinic,
    db: DbSession,
    request: Request,
    _user: CurrentUserDep,
) -> dict:
    """Same code path the voice agent uses, so both produce identical results."""
    clinic = await _load_clinic(db, clinic_id)

    appointment = await appointment_service.book_appointment(
        db,
        clinic,
        patient_name=payload.patient_name,
        patient_phone=payload.patient_phone,
        starts_at=payload.starts_at,
        doctor_id=payload.doctor_id,
        appointment_type_id=payload.appointment_type_id,
        duration_minutes=payload.duration_minutes,
        reason=payload.reason,
        notes=payload.notes,
        language=payload.preferred_language,
    )

    await write_audit_log(
        db,
        request,
        action="appointment.created",
        clinic_id=clinic_id,
        resource_type="appointment",
        resource_id=appointment.id,
        metadata={"starts_at": appointment.starts_at.isoformat()},
    )
    await db.commit()
    await db.refresh(appointment, ["patient", "doctor"])

    return ok(
        _serialize(appointment, clinic),
        message="Appointment booked and added to the calendar.",
    )


@router.patch("/{appointment_id}/reschedule", summary="Move an appointment")
async def reschedule(
    appointment_id: str,
    payload: AppointmentReschedule,
    clinic_id: ActiveClinic,
    db: DbSession,
    request: Request,
    _user: CurrentUserDep,
) -> dict:
    clinic = await _load_clinic(db, clinic_id)
    appointment = await scoped_get(
        db, Appointment, appointment_id, clinic_id, resource_name="Appointment"
    )
    previous = appointment.starts_at

    updated = await appointment_service.reschedule_appointment(
        db,
        clinic,
        appointment,
        starts_at=payload.starts_at,
        doctor_id=payload.doctor_id,
        reason=payload.reason,
    )

    await write_audit_log(
        db,
        request,
        action="appointment.rescheduled",
        clinic_id=clinic_id,
        resource_type="appointment",
        resource_id=updated.id,
        metadata={"from": previous.isoformat(), "to": updated.starts_at.isoformat()},
    )
    await db.commit()
    await db.refresh(updated, ["patient", "doctor"])

    return ok(_serialize(updated, clinic), message="Appointment rescheduled.")


@router.patch("/{appointment_id}/cancel", summary="Cancel an appointment")
async def cancel(
    appointment_id: str,
    payload: AppointmentCancel,
    clinic_id: ActiveClinic,
    db: DbSession,
    request: Request,
    _user: CurrentUserDep,
) -> dict:
    clinic = await _load_clinic(db, clinic_id)
    appointment = await scoped_get(
        db, Appointment, appointment_id, clinic_id, resource_name="Appointment"
    )

    updated = await appointment_service.cancel_appointment(
        db, clinic, appointment, reason=payload.reason, notify_patient=payload.notify_patient
    )

    await write_audit_log(
        db,
        request,
        action="appointment.cancelled",
        clinic_id=clinic_id,
        resource_type="appointment",
        resource_id=updated.id,
        metadata={"reason": payload.reason},
    )
    await db.commit()
    await db.refresh(updated, ["patient", "doctor"])

    return ok(
        _serialize(updated, clinic),
        message="Appointment cancelled and removed from the calendar.",
    )
