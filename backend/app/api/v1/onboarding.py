"""One-request onboarding for a business of any type.

This is the endpoint that makes the product a platform rather than one vertical.
A single POST creates the tenant, its owner login, its staff, its appointment
types, and a VAPI assistant, with the vocabulary and behaviour seeded from a
business-type preset and then owned by the tenant.

Onboarding business #100 runs the identical code path as #1. Supporting a trade
nobody anticipated means either picking the `general` preset and editing the
fields, or adding one entry to `app/agent/presets.py`. Neither is a schema
change and neither touches this file.

Only a superadmin can call this: it creates a tenant and an account with full
access to it.

Partial success is deliberate. The database work is one transaction; VAPI is
external and may be down. Rather than rolling back a perfectly good business
because a third party failed, the gap comes back as `next_steps`.
"""

import logging

from fastapi import APIRouter, Request
from sqlalchemy import select

from app.agent.presets import get_preset, list_presets
from app.core.deps import DbSession, RequireSuperadmin, write_audit_log
from app.core.errors import AlreadyExistsError
from app.core.response import ok
from app.core.security import hash_password
from app.db.models import AppointmentType, Business, StaffMember, User, UserRole
from app.schemas.business import OnboardBusinessRequest
from app.services import vapi

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/onboarding", tags=["onboarding"])


@router.get("/business-types", summary="Available business type presets")
async def business_types(_admin: RequireSuperadmin) -> dict:
    """Presets for the onboarding form's dropdown.

    Served from code rather than the database because a preset is a starting
    point, not tenant state. Picking one pre-fills the form; every field remains
    editable before and after submission.
    """
    return ok(list_presets())


@router.post("/businesses", status_code=201, summary="Create a fully configured business")
async def onboard_business(
    payload: OnboardBusinessRequest,
    db: DbSession,
    request: Request,
    admin: RequireSuperadmin,
) -> dict:
    # Check uniqueness up front so the caller gets a clear 409 rather than a
    # constraint violation surfacing as a 500 halfway through.
    for column, value, message in (
        (Business.slug, payload.slug, f"A business with the slug '{payload.slug}' already exists."),
        (
            Business.phone_number,
            payload.phone_number,
            f"The number {payload.phone_number} is already assigned to another business.",
        ),
    ):
        if (await db.execute(select(Business).where(column == value))).scalar_one_or_none():
            raise AlreadyExistsError(message)

    email = payload.owner_email.lower()
    if (await db.execute(select(User).where(User.email == email))).scalar_one_or_none():
        raise AlreadyExistsError("A user with that email address already exists.")

    # The preset supplies defaults; anything the caller sent wins over it.
    preset = get_preset(payload.business_type)

    business = Business(
        name=payload.name,
        slug=payload.slug,
        phone_number=payload.phone_number,
        address=payload.address,
        city=payload.city,
        contact_phone=payload.contact_phone,
        contact_email=email,
        timezone=payload.timezone,
        # --- business type configuration ---
        business_type=preset.slug,
        business_descriptor=payload.business_descriptor or preset.business_descriptor,
        labels=(payload.labels.model_dump() if payload.labels else preset.label_map()),
        intake_fields=(
            [field.model_dump() for field in payload.intake_fields]
            if payload.intake_fields is not None
            else preset.intake_payload()
        ),
        agent_rules=(
            payload.agent_rules if payload.agent_rules is not None else list(preset.rules)
        ),
        escalation_instructions=(
            payload.escalation_instructions
            if payload.escalation_instructions is not None
            else preset.escalation
        ),
        # --- agent + schedule ---
        agent_name=payload.agent_name or preset.default_agent_name,
        primary_language=payload.primary_language,
        greeting_en=payload.greeting_en,
        greeting_hi=payload.greeting_hi,
        opens_at=payload.opens_at,
        closes_at=payload.closes_at,
        working_days=payload.working_days,
        slot_duration_minutes=payload.slot_duration_minutes or preset.default_slot_minutes,
    )
    db.add(business)
    await db.flush()

    owner = User(
        email=email,
        password_hash=hash_password(payload.owner_password),
        full_name=payload.owner_name or payload.name,
        role=UserRole.OWNER,
        business_id=business.id,
    )
    db.add(owner)

    staff = [
        StaffMember(business_id=business.id, **member.model_dump())
        for member in payload.staff_members
    ]
    for member in staff:
        db.add(member)

    service_names = payload.appointment_types or list(preset.example_services) or ["Appointment"]
    for name in service_names:
        db.add(
            AppointmentType(
                business_id=business.id,
                name=name,
                duration_minutes=business.slot_duration_minutes,
            )
        )

    await db.flush()

    # --- External provisioning. Failures degrade to a checklist item. ---
    next_steps: list[str] = []
    assistant_id = ""

    if payload.create_vapi_assistant:
        try:
            assistant_id = await vapi.create_assistant(business, staff)
            business.vapi_assistant_id = assistant_id

            from app.config import get_settings

            settings = get_settings()
            if settings.vapi_phone_number_id:
                await vapi.attach_phone_number(settings.vapi_phone_number_id, assistant_id)
                business.vapi_phone_number_id = settings.vapi_phone_number_id
            else:
                next_steps.append(
                    "Attach a VAPI phone number to the new assistant, then point "
                    f"{payload.phone_number} at it."
                )
        except Exception:  # noqa: BLE001
            logger.exception("VAPI provisioning failed for business %s", business.id)
            next_steps.append(
                "Voice agent setup failed. Open settings and save once to retry."
            )
    else:
        next_steps.append("Create the VAPI assistant for this business.")

    next_steps.append("Connect the business's Google Calendar from Settings, using the owner login.")
    next_steps.append("Confirm the WhatsApp utility templates are approved in Meta Business Manager.")

    await write_audit_log(
        db,
        request,
        action="business.onboarded",
        business_id=business.id,
        resource_type="business",
        resource_id=business.id,
        metadata={"slug": business.slug, "type": business.business_type, "by": admin.email},
    )
    await db.commit()
    await db.refresh(business)

    from app.api.v1.businesses import _serialize_business

    return ok(
        {
            "business": await _serialize_business(db, business),
            "owner_user_id": owner.id,
            "vapi_assistant_id": assistant_id,
            "next_steps": next_steps,
        },
        message=f"{business.name} is set up and ready.",
    )
