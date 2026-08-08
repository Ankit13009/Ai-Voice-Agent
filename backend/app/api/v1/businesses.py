"""Business settings and staff_member management.

Any change that the agent's prompt embeds (hours, greeting, language, staff_members)
re-pushes the VAPI assistant, so a settings save takes effect on the very next
call instead of leaving the agent quoting last week's timings.
"""

import logging

from fastapi import APIRouter, Request
from sqlalchemy import select

from app.core.deps import (
    ActiveBusiness,
    CurrentUserDep,
    DbSession,
    RequireOwner,
    scoped_get,
    write_audit_log,
)
from app.core.errors import UpstreamError
from app.core.response import ok
from app.db.models import CalendarCredential, Business, StaffMember
from app.schemas.business import BusinessUpdate, StaffMemberCreate, StaffMemberUpdate
from app.services import vapi

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/businesses", tags=["businesses"])

# Changing any of these alters what the agent says, so the assistant is re-pushed.
# The business-type fields are in here because they are the agent's persona,
# vocabulary, and rules: editing them without re-pushing would leave the phone
# agent behaving as the previous business type until something else saved.
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
    "business_type",
    "business_descriptor",
    "labels",
    "intake_fields",
    "agent_rules",
    "escalation_instructions",
}


def _staff_member_dict(staff_member: StaffMember) -> dict:
    return {
        "id": staff_member.id,
        "name": staff_member.name,
        "specialization": staff_member.specialization,
        "google_calendar_id": staff_member.google_calendar_id,
        "consultation_duration_minutes": staff_member.consultation_duration_minutes,
        "is_active": staff_member.is_active,
    }


async def _serialize_business(db, business: Business) -> dict:
    staff_members = (
        await db.execute(
            select(StaffMember).where(StaffMember.business_id == business.id).order_by(StaffMember.name)
        )
    ).scalars().all()

    credential = (
        await db.execute(
            select(CalendarCredential).where(CalendarCredential.business_id == business.id)
        )
    ).scalar_one_or_none()

    from app.config import get_settings

    settings = get_settings()

    return {
        "id": business.id,
        "name": business.name,
        "slug": business.slug,
        "address": business.address,
        "city": business.city,
        "contact_phone": business.contact_phone,
        "contact_email": business.contact_email,
        "timezone": business.timezone,
        # --- business type configuration ---
        # Sent in full so the dashboard can relabel itself and the settings form
        # can edit the agent's rules without a second request.
        "business_type": business.business_type,
        "business_descriptor": business.business_descriptor,
        "labels": {
            key: business.label(key)
            for key in (
                "customer_singular",
                "customer_plural",
                "staff_singular",
                "staff_plural",
                "booking_singular",
                "booking_plural",
            )
        },
        "intake_fields": business.intake_fields or [],
        "agent_rules": business.agent_rules or [],
        "escalation_instructions": business.escalation_instructions,
        "agent_name": business.agent_name,
        "phone_number": business.phone_number,
        "primary_language": business.primary_language.value,
        "greeting_en": business.greeting_en,
        "greeting_hi": business.greeting_hi,
        "agent_notes": business.agent_notes,
        "opens_at": business.opens_at.strftime("%H:%M:%S"),
        "closes_at": business.closes_at.strftime("%H:%M:%S"),
        "working_days": business.working_days,
        "slot_duration_minutes": business.slot_duration_minutes,
        "whatsapp_enabled": business.whatsapp_enabled,
        "reminder_24h_enabled": business.reminder_24h_enabled,
        "reminder_2h_enabled": business.reminder_2h_enabled,
        "is_active": business.is_active,
        # Connection status only. Tokens are never serialized.
        "integrations": {
            "google_calendar_connected": bool(credential and credential.is_connected),
            "google_calendar_email": credential.connected_email if credential else "",
            "google_calendar_error": credential.last_error if credential else "",
            "vapi_assistant_configured": bool(business.vapi_assistant_id),
            "whatsapp_configured": bool(
                business.whatsapp_enabled
                and (business.whatsapp_phone_number_id or settings.whatsapp_phone_number_id)
                and settings.whatsapp_access_token
            ),
        },
        "staff_members": [_staff_member_dict(d) for d in staff_members],
    }


async def _sync_assistant(db, business: Business) -> str:
    """Re-push the assistant. Returns a warning string, or "" on success.

    A VAPI failure must not roll back a settings save the user just made, so it
    is surfaced as a message rather than raised.
    """
    if not business.vapi_assistant_id:
        return ""
    staff_members = (
        await db.execute(
            select(StaffMember).where(StaffMember.business_id == business.id, StaffMember.is_active.is_(True))
        )
    ).scalars().all()
    try:
        await vapi.update_assistant(business, list(staff_members))
        return ""
    except UpstreamError:
        logger.exception("Could not sync VAPI assistant for business %s", business.id)
        return " The voice agent could not be updated; it will keep using the previous settings."


@router.get("/me", summary="The current business and its settings")
async def get_my_business(business_id: ActiveBusiness, db: DbSession, _user: CurrentUserDep) -> dict:
    business = (await db.execute(select(Business).where(Business.id == business_id))).scalar_one()
    return ok(await _serialize_business(db, business))


@router.patch("/me", summary="Update business settings")
async def update_my_business(
    payload: BusinessUpdate,
    business_id: ActiveBusiness,
    db: DbSession,
    request: Request,
    _owner: RequireOwner,
) -> dict:
    business = (await db.execute(select(Business).where(Business.id == business_id))).scalar_one()

    # mode="json" so nested models (labels, intake_fields) land as plain dicts
    # the JSON columns can store, not as pydantic objects.
    changes = payload.model_dump(exclude_unset=True, mode="json")
    for field, value in changes.items():
        setattr(business, field, value)
    await db.flush()

    warning = ""
    if PROMPT_AFFECTING_FIELDS & set(changes):
        warning = await _sync_assistant(db, business)

    await write_audit_log(
        db,
        request,
        action="business.updated",
        business_id=business_id,
        resource_type="business",
        resource_id=business.id,
        metadata={"fields": sorted(changes.keys())},
    )
    await db.commit()
    await db.refresh(business)

    return ok(await _serialize_business(db, business), message=f"Settings saved.{warning}")


# --------------------------------------------------------------------------- #
# StaffMembers
# --------------------------------------------------------------------------- #
@router.get("/me/staff", summary="List staff_members")
async def list_staff_members(business_id: ActiveBusiness, db: DbSession, _user: CurrentUserDep) -> dict:
    rows = (
        await db.execute(select(StaffMember).where(StaffMember.business_id == business_id).order_by(StaffMember.name))
    ).scalars().all()
    return ok([_staff_member_dict(d) for d in rows])


@router.post("/me/staff", status_code=201, summary="Add a staff_member")
async def create_staff_member(
    payload: StaffMemberCreate,
    business_id: ActiveBusiness,
    db: DbSession,
    request: Request,
    _owner: RequireOwner,
) -> dict:
    staff_member = StaffMember(business_id=business_id, **payload.model_dump())
    db.add(staff_member)
    await db.flush()

    business = (await db.execute(select(Business).where(Business.id == business_id))).scalar_one()
    warning = await _sync_assistant(db, business)

    await write_audit_log(
        db,
        request,
        action="staff_member.created",
        business_id=business_id,
        resource_type="staff_member",
        resource_id=staff_member.id,
    )
    await db.commit()
    await db.refresh(staff_member)

    return ok(_staff_member_dict(staff_member), message=f"StaffMember added.{warning}")


@router.patch("/me/staff/{staff_member_id}", summary="Update a staff_member")
async def update_staff_member(
    staff_member_id: str,
    payload: StaffMemberUpdate,
    business_id: ActiveBusiness,
    db: DbSession,
    request: Request,
    _owner: RequireOwner,
) -> dict:
    staff_member = await scoped_get(db, StaffMember, staff_member_id, business_id, resource_name="StaffMember")

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(staff_member, field, value)
    await db.flush()

    business = (await db.execute(select(Business).where(Business.id == business_id))).scalar_one()
    warning = await _sync_assistant(db, business)

    await write_audit_log(
        db,
        request,
        action="staff_member.updated",
        business_id=business_id,
        resource_type="staff_member",
        resource_id=staff_member.id,
    )
    await db.commit()
    await db.refresh(staff_member)

    return ok(_staff_member_dict(staff_member), message=f"StaffMember updated.{warning}")


@router.delete("/me/staff/{staff_member_id}", summary="Deactivate a staff_member")
async def deactivate_staff_member(
    staff_member_id: str,
    business_id: ActiveBusiness,
    db: DbSession,
    request: Request,
    _owner: RequireOwner,
) -> dict:
    """Soft delete. A hard delete would orphan every past appointment's staff_member
    reference and destroy the business's history."""
    staff_member = await scoped_get(db, StaffMember, staff_member_id, business_id, resource_name="StaffMember")
    staff_member.is_active = False
    await db.flush()

    business = (await db.execute(select(Business).where(Business.id == business_id))).scalar_one()
    warning = await _sync_assistant(db, business)

    await write_audit_log(
        db,
        request,
        action="staff_member.deactivated",
        business_id=business_id,
        resource_type="staff_member",
        resource_id=staff_member.id,
    )
    await db.commit()

    return ok(None, message=f"StaffMember deactivated.{warning}")
