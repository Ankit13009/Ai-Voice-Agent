"""One-request clinic onboarding: the "master template, add a clinic in one click" flow.

A single POST creates the tenant, its owner login, its doctors, its appointment
types, and a VAPI assistant rendered from the master template in
`services/vapi.build_assistant_payload`. Nothing here is clinic-specific code:
adding the hundredth clinic runs exactly the same path as the first.

Only a superadmin can call this. It creates a tenant and an account with full
access to it, which is not an action any clinic user should be able to take.

Partial success is deliberate. The database work is one transaction; VAPI and
Google Calendar are external and may fail or need a human step. Rather than
rolling back a perfectly good clinic because VAPI was down, those come back as
`next_steps` for the operator to finish.
"""

import logging

from fastapi import APIRouter, Request
from sqlalchemy import select

from app.core.deps import DbSession, RequireSuperadmin, write_audit_log
from app.core.errors import AlreadyExistsError
from app.core.response import ok
from app.core.security import hash_password
from app.db.models import AppointmentType, Clinic, Doctor, User, UserRole
from app.schemas.clinic import OnboardClinicRequest
from app.services import vapi

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/onboarding", tags=["onboarding"])

DEFAULT_APPOINTMENT_TYPES = [
    ("First consultation", 30),
    ("Follow-up", 15),
]


@router.post("/clinics", status_code=201, summary="Create a fully configured clinic")
async def onboard_clinic(
    payload: OnboardClinicRequest,
    db: DbSession,
    request: Request,
    admin: RequireSuperadmin,
) -> dict:
    # Check uniqueness up front so the caller gets a clear 409 rather than a
    # constraint violation surfacing as a 500 halfway through.
    for column, value, label in (
        (Clinic.slug, payload.slug, f"A clinic with the slug '{payload.slug}' already exists."),
        (
            Clinic.phone_number,
            payload.phone_number,
            f"The number {payload.phone_number} is already assigned to another clinic.",
        ),
    ):
        if (await db.execute(select(Clinic).where(column == value))).scalar_one_or_none():
            raise AlreadyExistsError(label)

    email = payload.owner_email.lower()
    if (await db.execute(select(User).where(User.email == email))).scalar_one_or_none():
        raise AlreadyExistsError("A user with that email address already exists.")

    clinic = Clinic(
        name=payload.name,
        slug=payload.slug,
        phone_number=payload.phone_number,
        address=payload.address,
        city=payload.city,
        contact_phone=payload.contact_phone,
        contact_email=email,
        timezone=payload.timezone,
        agent_name=payload.agent_name,
        primary_language=payload.primary_language,
        greeting_en=payload.greeting_en,
        greeting_hi=payload.greeting_hi,
        opens_at=payload.opens_at,
        closes_at=payload.closes_at,
        working_days=payload.working_days,
        slot_duration_minutes=payload.slot_duration_minutes,
    )
    db.add(clinic)
    await db.flush()

    owner = User(
        email=email,
        password_hash=hash_password(payload.owner_password),
        full_name=payload.owner_name or payload.name,
        role=UserRole.OWNER,
        clinic_id=clinic.id,
    )
    db.add(owner)

    doctors = [
        Doctor(clinic_id=clinic.id, **doctor.model_dump()) for doctor in payload.doctors
    ]
    for doctor in doctors:
        db.add(doctor)

    type_specs = (
        [(name, payload.slot_duration_minutes) for name in payload.appointment_types]
        or DEFAULT_APPOINTMENT_TYPES
    )
    for name, duration in type_specs:
        db.add(
            AppointmentType(clinic_id=clinic.id, name=name, duration_minutes=duration)
        )

    await db.flush()

    # --- External provisioning. Failures degrade to a checklist item. ---
    next_steps: list[str] = []
    assistant_id = ""

    if payload.create_vapi_assistant:
        try:
            assistant_id = await vapi.create_assistant(clinic, doctors)
            clinic.vapi_assistant_id = assistant_id

            from app.config import get_settings

            settings = get_settings()
            if settings.vapi_phone_number_id:
                await vapi.attach_phone_number(settings.vapi_phone_number_id, assistant_id)
                clinic.vapi_phone_number_id = settings.vapi_phone_number_id
            else:
                next_steps.append(
                    "Attach a VAPI phone number to the new assistant, then point "
                    f"{payload.phone_number} at it."
                )
        except Exception:  # noqa: BLE001
            logger.exception("VAPI provisioning failed for clinic %s", clinic.id)
            next_steps.append(
                "Voice agent setup failed. Open clinic settings and save once to retry."
            )
    else:
        next_steps.append("Create the VAPI assistant for this clinic.")

    next_steps.append(
        "Connect the clinic's Google Calendar from Settings, using the owner login."
    )
    next_steps.append(
        "Confirm the WhatsApp utility templates are approved in Meta Business Manager."
    )

    await write_audit_log(
        db,
        request,
        action="clinic.onboarded",
        clinic_id=clinic.id,
        resource_type="clinic",
        resource_id=clinic.id,
        metadata={"slug": clinic.slug, "by": admin.email},
    )
    await db.commit()
    await db.refresh(clinic)

    from app.api.v1.clinics import _serialize_clinic

    return ok(
        {
            "clinic": await _serialize_clinic(db, clinic),
            "owner_user_id": owner.id,
            "vapi_assistant_id": assistant_id,
            "next_steps": next_steps,
        },
        message=f"{clinic.name} is set up and ready.",
    )
