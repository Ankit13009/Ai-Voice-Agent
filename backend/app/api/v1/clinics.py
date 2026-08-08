"""Clinic settings and doctor management.

Any change that the agent's prompt embeds (hours, greeting, language, doctors)
re-pushes the VAPI assistant, so a settings save takes effect on the very next
call instead of leaving the agent quoting last week's timings.
"""

import logging

from fastapi import APIRouter, Request
from sqlalchemy import select

from app.core.deps import (
    ActiveClinic,
    CurrentUserDep,
    DbSession,
    RequireOwner,
    scoped_get,
    write_audit_log,
)
from app.core.errors import UpstreamError
from app.core.response import ok
from app.db.models import CalendarCredential, Clinic, Doctor
from app.schemas.clinic import ClinicUpdate, DoctorCreate, DoctorUpdate
from app.services import vapi

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/clinics", tags=["clinics"])

# Changing any of these alters what the agent says, so the assistant is re-pushed.
PROMPT_AFFECTING_FIELDS = {
    "name",
    "address",
    "agent_name",
    "primary_language",
    "greeting_en",
    "greeting_hi",
    "agent_notes",
    "opens_at",
    "closes_at",
    "working_days",
    "timezone",
    "contact_phone",
}


def _doctor_dict(doctor: Doctor) -> dict:
    return {
        "id": doctor.id,
        "name": doctor.name,
        "specialization": doctor.specialization,
        "google_calendar_id": doctor.google_calendar_id,
        "consultation_duration_minutes": doctor.consultation_duration_minutes,
        "is_active": doctor.is_active,
    }


async def _serialize_clinic(db, clinic: Clinic) -> dict:
    doctors = (
        await db.execute(
            select(Doctor).where(Doctor.clinic_id == clinic.id).order_by(Doctor.name)
        )
    ).scalars().all()

    credential = (
        await db.execute(
            select(CalendarCredential).where(CalendarCredential.clinic_id == clinic.id)
        )
    ).scalar_one_or_none()

    from app.config import get_settings

    settings = get_settings()

    return {
        "id": clinic.id,
        "name": clinic.name,
        "slug": clinic.slug,
        "address": clinic.address,
        "city": clinic.city,
        "contact_phone": clinic.contact_phone,
        "contact_email": clinic.contact_email,
        "timezone": clinic.timezone,
        "agent_name": clinic.agent_name,
        "phone_number": clinic.phone_number,
        "primary_language": clinic.primary_language.value,
        "greeting_en": clinic.greeting_en,
        "greeting_hi": clinic.greeting_hi,
        "agent_notes": clinic.agent_notes,
        "opens_at": clinic.opens_at.strftime("%H:%M:%S"),
        "closes_at": clinic.closes_at.strftime("%H:%M:%S"),
        "working_days": clinic.working_days,
        "slot_duration_minutes": clinic.slot_duration_minutes,
        "whatsapp_enabled": clinic.whatsapp_enabled,
        "reminder_24h_enabled": clinic.reminder_24h_enabled,
        "reminder_2h_enabled": clinic.reminder_2h_enabled,
        "is_active": clinic.is_active,
        # Connection status only. Tokens are never serialized.
        "integrations": {
            "google_calendar_connected": bool(credential and credential.is_connected),
            "google_calendar_email": credential.connected_email if credential else "",
            "google_calendar_error": credential.last_error if credential else "",
            "vapi_assistant_configured": bool(clinic.vapi_assistant_id),
            "whatsapp_configured": bool(
                clinic.whatsapp_enabled
                and (clinic.whatsapp_phone_number_id or settings.whatsapp_phone_number_id)
                and settings.whatsapp_access_token
            ),
        },
        "doctors": [_doctor_dict(d) for d in doctors],
    }


async def _sync_assistant(db, clinic: Clinic) -> str:
    """Re-push the assistant. Returns a warning string, or "" on success.

    A VAPI failure must not roll back a settings save the user just made, so it
    is surfaced as a message rather than raised.
    """
    if not clinic.vapi_assistant_id:
        return ""
    doctors = (
        await db.execute(
            select(Doctor).where(Doctor.clinic_id == clinic.id, Doctor.is_active.is_(True))
        )
    ).scalars().all()
    try:
        await vapi.update_assistant(clinic, list(doctors))
        return ""
    except UpstreamError:
        logger.exception("Could not sync VAPI assistant for clinic %s", clinic.id)
        return " The voice agent could not be updated; it will keep using the previous settings."


@router.get("/me", summary="The current clinic and its settings")
async def get_my_clinic(clinic_id: ActiveClinic, db: DbSession, _user: CurrentUserDep) -> dict:
    clinic = (await db.execute(select(Clinic).where(Clinic.id == clinic_id))).scalar_one()
    return ok(await _serialize_clinic(db, clinic))


@router.patch("/me", summary="Update clinic settings")
async def update_my_clinic(
    payload: ClinicUpdate,
    clinic_id: ActiveClinic,
    db: DbSession,
    request: Request,
    _owner: RequireOwner,
) -> dict:
    clinic = (await db.execute(select(Clinic).where(Clinic.id == clinic_id))).scalar_one()

    changes = payload.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(clinic, field, value)
    await db.flush()

    warning = ""
    if PROMPT_AFFECTING_FIELDS & set(changes):
        warning = await _sync_assistant(db, clinic)

    await write_audit_log(
        db,
        request,
        action="clinic.updated",
        clinic_id=clinic_id,
        resource_type="clinic",
        resource_id=clinic.id,
        metadata={"fields": sorted(changes.keys())},
    )
    await db.commit()
    await db.refresh(clinic)

    return ok(await _serialize_clinic(db, clinic), message=f"Settings saved.{warning}")


# --------------------------------------------------------------------------- #
# Doctors
# --------------------------------------------------------------------------- #
@router.get("/me/doctors", summary="List doctors")
async def list_doctors(clinic_id: ActiveClinic, db: DbSession, _user: CurrentUserDep) -> dict:
    rows = (
        await db.execute(select(Doctor).where(Doctor.clinic_id == clinic_id).order_by(Doctor.name))
    ).scalars().all()
    return ok([_doctor_dict(d) for d in rows])


@router.post("/me/doctors", status_code=201, summary="Add a doctor")
async def create_doctor(
    payload: DoctorCreate,
    clinic_id: ActiveClinic,
    db: DbSession,
    request: Request,
    _owner: RequireOwner,
) -> dict:
    doctor = Doctor(clinic_id=clinic_id, **payload.model_dump())
    db.add(doctor)
    await db.flush()

    clinic = (await db.execute(select(Clinic).where(Clinic.id == clinic_id))).scalar_one()
    warning = await _sync_assistant(db, clinic)

    await write_audit_log(
        db,
        request,
        action="doctor.created",
        clinic_id=clinic_id,
        resource_type="doctor",
        resource_id=doctor.id,
    )
    await db.commit()
    await db.refresh(doctor)

    return ok(_doctor_dict(doctor), message=f"Doctor added.{warning}")


@router.patch("/me/doctors/{doctor_id}", summary="Update a doctor")
async def update_doctor(
    doctor_id: str,
    payload: DoctorUpdate,
    clinic_id: ActiveClinic,
    db: DbSession,
    request: Request,
    _owner: RequireOwner,
) -> dict:
    doctor = await scoped_get(db, Doctor, doctor_id, clinic_id, resource_name="Doctor")

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(doctor, field, value)
    await db.flush()

    clinic = (await db.execute(select(Clinic).where(Clinic.id == clinic_id))).scalar_one()
    warning = await _sync_assistant(db, clinic)

    await write_audit_log(
        db,
        request,
        action="doctor.updated",
        clinic_id=clinic_id,
        resource_type="doctor",
        resource_id=doctor.id,
    )
    await db.commit()
    await db.refresh(doctor)

    return ok(_doctor_dict(doctor), message=f"Doctor updated.{warning}")


@router.delete("/me/doctors/{doctor_id}", summary="Deactivate a doctor")
async def deactivate_doctor(
    doctor_id: str,
    clinic_id: ActiveClinic,
    db: DbSession,
    request: Request,
    _owner: RequireOwner,
) -> dict:
    """Soft delete. A hard delete would orphan every past appointment's doctor
    reference and destroy the clinic's history."""
    doctor = await scoped_get(db, Doctor, doctor_id, clinic_id, resource_name="Doctor")
    doctor.is_active = False
    await db.flush()

    clinic = (await db.execute(select(Clinic).where(Clinic.id == clinic_id))).scalar_one()
    warning = await _sync_assistant(db, clinic)

    await write_audit_log(
        db,
        request,
        action="doctor.deactivated",
        clinic_id=clinic_id,
        resource_type="doctor",
        resource_id=doctor.id,
    )
    await db.commit()

    return ok(None, message=f"Doctor deactivated.{warning}")
