"""Business settings and staff_member management.

Any change that the agent's prompt embeds (hours, greeting, language, staff_members)
re-pushes the VAPI assistant, so a settings save takes effect on the very next
call instead of leaving the agent quoting last week's timings.
"""

import logging

from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import update
from sqlalchemy import select

from app.config import get_settings
from app.core.deps import (
    ActiveBusiness,
    CurrentUserDep,
    DbSession,
    RequireOwner,
    scoped_get,
    write_audit_log,
)
from app.core.errors import AlreadyExistsError, IntegrationNotConfiguredError, UpstreamError
from app.core.response import ok
from app.core.security import generate_temporary_password, hash_password
from app.db.models import (
    Business,
    CalendarCredential,
    RefreshToken,
    StaffMember,
    User,
    UserRole,
)
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
    # Adds or removes the transferCall tool on the assistant.
    "handoff_enabled",
    "handoff_phone",
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
        "handoff_enabled": business.handoff_enabled,
        "handoff_phone": business.handoff_phone,
        "owner_notify_phone": business.owner_notify_phone,
        "notify_on_booking": business.notify_on_booking,
        "daily_summary_enabled": business.daily_summary_enabled,
        "daily_summary_hour": business.daily_summary_hour,
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
        "transcript_retention_days": business.transcript_retention_days,
        "recording_retention_days": business.recording_retention_days,
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
    """Create or re-push this business's VAPI assistant.

    Creating when one is missing is what makes "save Settings to retry" work.
    Onboarding provisions the assistant, but that call can fail (VAPI down, key
    not yet configured) and degrades to a checklist item; without create-on-save
    that item would be impossible to action, and a business seeded outside the
    onboarding flow could never get an agent at all.

    A VAPI failure must not roll back a settings save the user just made, so it
    is surfaced as a returned warning rather than raised.
    """
    settings = get_settings()
    if not settings.vapi_api_key:
        return " The voice agent was not updated: VAPI is not configured on this server."

    staff_members = (
        await db.execute(
            select(StaffMember).where(
                StaffMember.business_id == business.id, StaffMember.is_active.is_(True)
            )
        )
    ).scalars().all()

    try:
        if business.vapi_assistant_id:
            recreated_id = await vapi.update_assistant(business, list(staff_members))
            if recreated_id:
                # The stored id was not in this VAPI account, so a fresh
                # assistant was made. Persist it or the next save recreates
                # again, littering the account with orphans.
                business.vapi_assistant_id = recreated_id
                await db.flush()
            return ""

        assistant_id = await vapi.create_assistant(business, list(staff_members))
        business.vapi_assistant_id = assistant_id
        await db.flush()

        # Attach the platform's inbound number if one is configured, so a newly
        # created assistant can actually receive calls rather than sitting idle.
        if settings.vapi_phone_number_id and not business.vapi_phone_number_id:
            try:
                await vapi.attach_phone_number(settings.vapi_phone_number_id, assistant_id)
                business.vapi_phone_number_id = settings.vapi_phone_number_id
                await db.flush()
            except UpstreamError:
                logger.warning(
                    "Created assistant %s but could not attach the phone number.", assistant_id
                )
                return " The voice agent was created, but the phone number could not be attached."

        logger.info("Created VAPI assistant %s for business %s", assistant_id, business.id)
        return " The voice agent has been created."
    except UpstreamError:
        logger.exception("Could not sync VAPI assistant for business %s", business.id)
        return " The voice agent could not be updated; it will keep using the previous settings."


@router.get("/me", summary="The current business and its settings")
async def get_my_business(business_id: ActiveBusiness, db: DbSession, _user: CurrentUserDep) -> dict:
    business = (await db.execute(select(Business).where(Business.id == business_id))).scalar_one()
    return ok(await _serialize_business(db, business))


@router.get("/me/test-call", summary="Config for a browser test call to this agent")
async def test_call_config(
    business_id: ActiveBusiness, db: DbSession, _user: CurrentUserDep
) -> dict:
    """What the browser needs to talk to this business's agent over WebRTC.

    Only the PUBLIC VAPI key is returned. It can start a web call against an
    assistant and nothing else, so it is safe in the browser; the private key
    never leaves the server.

    The assistant id comes from the caller's own tenant row, so a user cannot
    dial into another business's agent by guessing an id.
    """
    settings = get_settings()
    business = (await db.execute(select(Business).where(Business.id == business_id))).scalar_one()

    if not settings.vapi_public_key:
        raise IntegrationNotConfiguredError(
            "VAPI is not configured on this server. Add VAPI_PUBLIC_KEY to the backend .env."
        )
    if not business.vapi_assistant_id:
        raise IntegrationNotConfiguredError(
            "This business has no voice assistant yet. Save settings once to create it."
        )

    return ok(
        {
            "public_key": settings.vapi_public_key,
            "assistant_id": business.vapi_assistant_id,
            "agent_name": business.agent_name,
            "business_name": business.name,
        }
    )


class OutboundTestCallRequest(BaseModel):
    """Where to ring. Validated here because a typo becomes a real, billed call."""

    phone_number: str = Field(
        ..., pattern=r"^\+[1-9]\d{7,14}$", description="E.164, e.g. +919876543210."
    )


@router.post("/me/test-call/outbound", summary="Ring a phone with this business's agent")
async def outbound_test_call(
    payload: OutboundTestCallRequest,
    business_id: ActiveBusiness,
    db: DbSession,
    request: Request,
    _owner: RequireOwner,
) -> dict:
    """Place a real outbound call so the agent can be tested on a phone line.

    Owner-only: this spends money on every press, unlike the browser test call.
    """
    settings = get_settings()
    business = (await db.execute(select(Business).where(Business.id == business_id))).scalar_one()

    if not settings.vapi_api_key:
        raise IntegrationNotConfiguredError("VAPI is not configured on this server.")
    if not business.vapi_assistant_id:
        raise IntegrationNotConfiguredError(
            "This business has no voice agent yet. Save settings once to create it."
        )

    # Prefer the configured number, otherwise take the first on the account, so
    # this works immediately after buying one without another config step.
    phone_number_id = business.vapi_phone_number_id or settings.vapi_phone_number_id
    if not phone_number_id:
        numbers = await vapi.list_phone_numbers()
        if not numbers:
            raise IntegrationNotConfiguredError(
                "No phone number on the VAPI account. Buy one in the VAPI dashboard "
                "under Phone Numbers, then try again."
            )

        # Prefer a number from a real carrier over a Vapi-issued free one.
        # Free Vapi numbers cannot place international calls at all, so on an
        # account holding both, picking the first would fail with
        # 'error-vapi-number-international' even though a working number exists.
        byo = [n for n in numbers if n.get("provider") != "vapi"]
        chosen = (byo or numbers)[0]
        phone_number_id = chosen["id"]
        logger.info(
            "Outbound test call using %s number %s",
            chosen.get("provider"),
            chosen.get("number"),
        )

    call = await vapi.start_outbound_call(
        assistant_id=business.vapi_assistant_id,
        phone_number_id=phone_number_id,
        to_number=payload.phone_number,
    )

    await write_audit_log(
        db,
        request,
        action="business.outbound_test_call",
        business_id=business_id,
        resource_type="call",
        resource_id=call.get("id", ""),
        metadata={"to": payload.phone_number},
    )
    await db.commit()

    return ok(
        {"call_id": call.get("id", ""), "status": call.get("status", "queued")},
        message=f"Calling {payload.phone_number} now. Answer to talk to {business.agent_name}.",
    )


# --------------------------------------------------------------------------- #
# Users of this business
# --------------------------------------------------------------------------- #
class UserCreateRequest(BaseModel):
    email: EmailStr
    full_name: str = Field(default="", max_length=255)
    role: Literal["owner", "staff"] = "staff"


@router.get("/me/users", summary="List users of this business")
async def list_users(
    business_id: ActiveBusiness, db: DbSession, _owner: RequireOwner
) -> dict:
    rows = (
        await db.execute(
            select(User).where(User.business_id == business_id).order_by(User.email)
        )
    ).scalars().all()
    return ok(
        [
            {
                "id": u.id,
                "email": u.email,
                "full_name": u.full_name,
                "role": u.role.value,
                "is_active": u.is_active,
                "last_login_at": u.last_login_at.isoformat() if u.last_login_at else None,
                "is_locked": bool(u.locked_until and u.locked_until > datetime.now(timezone.utc)),
                "must_change_password": u.must_change_password,
            }
            for u in rows
        ]
    )


@router.post("/me/users", status_code=201, summary="Add a user to this business")
async def create_user(
    payload: UserCreateRequest,
    business_id: ActiveBusiness,
    db: DbSession,
    request: Request,
    _owner: RequireOwner,
) -> dict:
    """Create a staff or owner account, returning a one-time password.

    The password is generated rather than chosen by the creator, and shown once.
    Letting one person set another's password means the creator knows it, which
    quietly breaks the assumption that an account belongs to one human.
    """
    email = payload.email.lower()
    if (await db.execute(select(User).where(User.email == email))).scalar_one_or_none():
        raise AlreadyExistsError("A user with that email address already exists.")

    temporary = generate_temporary_password()
    user = User(
        email=email,
        password_hash=hash_password(temporary),
        full_name=payload.full_name,
        role=UserRole(payload.role),
        business_id=business_id,
        must_change_password=True,
    )
    db.add(user)

    await write_audit_log(
        db, request, action="user.created", business_id=business_id,
        resource_type="user", resource_id=user.id, metadata={"role": payload.role},
    )
    await db.commit()

    return ok(
        {"id": user.id, "email": user.email, "temporary_password": temporary},
        message="User created. Give them this password; it cannot be shown again.",
    )


@router.post("/me/users/{user_id}/reset-password", summary="Reset a user's password")
async def reset_user_password(
    user_id: str,
    business_id: ActiveBusiness,
    db: DbSession,
    request: Request,
    owner: RequireOwner,
) -> dict:
    """Issue a new one-time password for a user of this business.

    There is no email service, and a self-serve "forgot password" link that
    cannot be delivered is worse than none. This matches how the product is
    actually operated: the owner phones you, or their staff phones them, and a
    new password is read out. It is scoped to the caller's own business, resets
    the lockout, forces a change at next login, and revokes every existing
    session so a stolen one cannot outlive the reset.
    """
    user = await scoped_get(db, User, user_id, business_id, resource_name="User")

    temporary = generate_temporary_password()
    user.password_hash = hash_password(temporary)
    user.must_change_password = True
    user.failed_login_attempts = 0
    user.locked_until = None

    revoked = await db.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=datetime.now(timezone.utc))
    )

    await write_audit_log(
        db, request, action="user.password_reset", business_id=business_id,
        resource_type="user", resource_id=user.id,
        metadata={"by": owner.email, "sessions_revoked": revoked.rowcount or 0},
    )
    await db.commit()

    return ok(
        {"id": user.id, "email": user.email, "temporary_password": temporary},
        message="Password reset. Give them this password; it cannot be shown again.",
    )


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

    # Sync when the agent's wording changed, and also whenever no assistant
    # exists yet, so any save provisions one. Otherwise a business could only be
    # rescued by editing a field that happens to be in the prompt.
    warning = ""
    if PROMPT_AFFECTING_FIELDS & set(changes) or not business.vapi_assistant_id:
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
