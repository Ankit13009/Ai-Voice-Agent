"""Platform administration: the operator's view across every tenant.

Distinct from `/businesses/me`, which is deliberately scoped to one tenant and
therefore unusable by a superadmin (who has no `business_id`). Everything here
is superadmin-only and crosses tenant boundaries by design, which is exactly why
it lives in its own module rather than being a flag on the tenant endpoints.
"""

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update

from app.config import get_settings
from app.core.deps import DbSession, RequireSuperadmin, write_audit_log
from app.core.errors import BadRequestError, NotFoundError
from app.core.response import ok
from app.core.security import generate_temporary_password, hash_password
from app.db.models import (
    Appointment,
    AppointmentStatus,
    Business,
    CalendarCredential,
    Call,
    RefreshToken,
    User,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/businesses", summary="Every tenant, with setup and activity status")
async def list_businesses(db: DbSession, _admin: RequireSuperadmin) -> dict:
    """One row per client, with enough status to see what still needs finishing.

    The counts are aggregated in two grouped queries rather than per business,
    so this stays one round trip regardless of how many clients there are.
    """
    settings = get_settings()
    businesses = (
        await db.execute(select(Business).order_by(Business.created_at.desc()))
    ).scalars().all()

    if not businesses:
        return ok([])

    ids = [b.id for b in businesses]

    call_counts = dict(
        (
            await db.execute(
                select(Call.business_id, func.count(Call.id))
                .where(Call.business_id.in_(ids))
                .group_by(Call.business_id)
            )
        ).all()
    )
    appointment_counts = dict(
        (
            await db.execute(
                select(Appointment.business_id, func.count(Appointment.id))
                .where(
                    Appointment.business_id.in_(ids),
                    Appointment.status.in_(
                        [AppointmentStatus.SCHEDULED, AppointmentStatus.RESCHEDULED]
                    ),
                )
                .group_by(Appointment.business_id)
            )
        ).all()
    )
    connected_calendars = {
        row[0]
        for row in (
            await db.execute(
                select(CalendarCredential.business_id).where(
                    CalendarCredential.business_id.in_(ids),
                    CalendarCredential.is_connected.is_(True),
                )
            )
        ).all()
    }
    owners = dict(
        (
            await db.execute(
                select(User.business_id, User.email).where(User.business_id.in_(ids))
            )
        ).all()
    )

    whatsapp_ready = bool(settings.whatsapp_access_token and settings.whatsapp_phone_number_id)
    recent_cutoff = datetime.now(timezone.utc) - timedelta(days=7)

    recent_calls = dict(
        (
            await db.execute(
                select(Call.business_id, func.count(Call.id))
                .where(Call.business_id.in_(ids), Call.created_at >= recent_cutoff)
                .group_by(Call.business_id)
            )
        ).all()
    )

    return ok(
        [
            {
                "id": b.id,
                "name": b.name,
                "slug": b.slug,
                "business_type": b.business_type,
                "phone_number": b.phone_number,
                "city": b.city,
                "owner_email": owners.get(b.id, ""),
                "is_active": b.is_active,
                "created_at": b.created_at.isoformat(),
                "calls_total": call_counts.get(b.id, 0),
                "calls_last_7d": recent_calls.get(b.id, 0),
                "appointments_upcoming": appointment_counts.get(b.id, 0),
                # The three things that must all be true before a client is live.
                "setup": {
                    "voice_agent": bool(b.vapi_assistant_id),
                    "google_calendar": b.id in connected_calendars,
                    "whatsapp": whatsapp_ready and b.whatsapp_enabled,
                },
            }
            for b in businesses
        ]
    )


@router.get("/stats", summary="Platform totals across all tenants")
async def platform_stats(db: DbSession, _admin: RequireSuperadmin) -> dict:
    async def count(stmt) -> int:
        return (await db.execute(stmt)).scalar_one() or 0

    total = await count(select(func.count(Business.id)))
    active = await count(select(func.count(Business.id)).where(Business.is_active.is_(True)))
    live = await count(
        select(func.count(Business.id)).where(Business.vapi_assistant_id != "")
    )
    calls = await count(select(func.count(Call.id)))
    appointments = await count(select(func.count(Appointment.id)))

    # Usage cost matters here: VAPI bills per minute, so a heavy client can be
    # unprofitable on a flat plan. Surfacing the total early makes that visible.
    cost = await count(select(func.coalesce(func.sum(Call.cost_paise), 0)))

    return ok(
        {
            "businesses_total": total,
            "businesses_active": active,
            "businesses_live": live,
            "calls_total": calls,
            "appointments_total": appointments,
            "call_cost_paise": cost,
        }
    )


@router.get("/businesses/{business_id}/users", summary="Users of one tenant")
async def list_business_users(
    business_id: str, db: DbSession, _admin: RequireSuperadmin
) -> dict:
    """Who can sign in to a given tenant.

    Needed before a reset: the operator taking the phone call knows the clinic's
    name, not the user id of whoever is locked out.
    """
    business = (
        await db.execute(select(Business).where(Business.id == business_id))
    ).scalar_one_or_none()
    if business is None:
        raise NotFoundError("Business")

    users = (
        await db.execute(
            select(User).where(User.business_id == business_id).order_by(User.created_at)
        )
    ).scalars().all()

    return ok(
        {
            "business": {"id": business.id, "name": business.name, "slug": business.slug},
            "users": [
                {
                    "id": u.id,
                    "email": u.email,
                    "full_name": u.full_name,
                    "role": u.role,
                    "last_login_at": u.last_login_at.isoformat() if u.last_login_at else None,
                    "must_change_password": u.must_change_password,
                    "is_locked": bool(u.locked_until and u.locked_until > datetime.now(timezone.utc)),
                }
                for u in users
            ],
        }
    )


@router.post("/users/{user_id}/reset-password", summary="Reset any user's password")
async def admin_reset_password(
    user_id: str, db: DbSession, request: Request, admin: RequireSuperadmin
) -> dict:
    """The operator's escalation path when an owner is locked out of their own tenant.

    An owner can already reset anyone in their business, but only while they can
    still sign in. With no email service there is no self-serve recovery, so an
    owner who forgets their password has no route back in and the only remedy
    was editing the database by hand.

    Unlike the owner-facing reset this deliberately crosses tenant boundaries,
    which is why it is superadmin-only and writes an audit entry naming the
    admin who did it.
    """
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if user is None:
        raise NotFoundError("User")

    temporary = generate_temporary_password()
    user.password_hash = hash_password(temporary)
    user.must_change_password = True
    user.failed_login_attempts = 0
    user.locked_until = None

    # A reset that leaves old sessions alive is not a reset: whoever holds a
    # stolen refresh token keeps the account.
    revoked = await db.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=datetime.now(timezone.utc))
    )

    await write_audit_log(
        db,
        request,
        action="user.password_reset_by_admin",
        business_id=user.business_id,
        resource_type="user",
        resource_id=user.id,
        metadata={"by": admin.email, "sessions_revoked": revoked.rowcount or 0},
    )
    await db.commit()

    logger.info("Superadmin %s reset the password for user %s", admin.email, user.id)
    return ok(
        {"id": user.id, "email": user.email, "temporary_password": temporary},
        message="Password reset. Give them this password; it cannot be shown again.",
    )


class WhatsAppConfigRequest(BaseModel):
    """All optional: an operator usually fixes one field, not all five.

    An empty string clears the stored value and lets the environment variable
    take over again, which is the only way out of a bad paste without a deploy.
    """

    whatsapp_access_token: str | None = Field(default=None, max_length=1000)
    whatsapp_phone_number_id: str | None = Field(default=None, max_length=64)
    whatsapp_business_account_id: str | None = Field(default=None, max_length=64)
    whatsapp_app_secret: str | None = Field(default=None, max_length=200)
    whatsapp_verify_token: str | None = Field(default=None, max_length=200)


@router.get("/whatsapp", summary="WhatsApp connection status")
async def whatsapp_status(_admin: RequireSuperadmin) -> dict:
    """Whether each credential is set, and where it came from.

    Never returns a token. Setting one up involves pasting five values from
    three different Meta screens, so the useful thing is knowing which are
    present and which of two tokens is in use, not the values themselves.
    """
    from app.services import platform_config

    return ok({"settings": await platform_config.describe()})


@router.put("/whatsapp", summary="Save WhatsApp credentials")
async def save_whatsapp_config(
    payload: WhatsAppConfigRequest,
    db: DbSession,
    request: Request,
    admin: RequireSuperadmin,
) -> dict:
    from app.services import platform_config

    updates = {k: v for k, v in payload.model_dump(exclude_unset=True).items() if v is not None}
    if not updates:
        raise BadRequestError("Nothing to save.")

    changed = await platform_config.set_values(db, updates, updated_by=admin.email)

    await write_audit_log(
        db,
        request,
        action="platform.whatsapp_configured",
        business_id=None,
        resource_type="platform_setting",
        resource_id="whatsapp",
        # Names only. The values are credentials and an audit log is read by
        # more people than a credential store should be.
        metadata={"fields": sorted(changed), "by": admin.email},
    )
    await db.commit()

    return ok(
        {"settings": await platform_config.describe()},
        message="Saved. Use Test connection to check it works.",
    )


@router.post("/whatsapp/test", summary="Check the WhatsApp credentials actually work")
async def test_whatsapp_config(_admin: RequireSuperadmin) -> dict:
    """Ask Meta whether these credentials can see the account.

    Saved credentials that have never been used are a guess: a token pasted
    with a trailing space, or one scoped to the wrong account, looks identical
    in the form and fails on the first real appointment. Checking here means
    that is found during setup instead of by a customer who never got their
    reminder.
    """
    import httpx

    from app.services import platform_config

    token = await platform_config.get_value("whatsapp_access_token")
    waba_id = await platform_config.get_value("whatsapp_business_account_id")
    phone_id = await platform_config.get_value("whatsapp_phone_number_id")

    missing = [
        name
        for name, value in (
            ("access token", token),
            ("business account id", waba_id),
            ("phone number id", phone_id),
        )
        if not value
    ]
    if missing:
        return ok(
            {"ok": False, "detail": f"Still missing: {', '.join(missing)}."},
            message="Not configured yet.",
        )

    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            number = await client.get(
                f"https://graph.facebook.com/v21.0/{phone_id}",
                params={"fields": "display_phone_number,verified_name,quality_rating"},
                headers={"Authorization": f"Bearer {token}"},
            )
            templates = await client.get(
                f"https://graph.facebook.com/v21.0/{waba_id}/message_templates",
                params={"limit": 200},
                headers={"Authorization": f"Bearer {token}"},
            )
        except httpx.HTTPError as exc:
            logger.warning("WhatsApp test call failed: %s", exc)
            return ok({"ok": False, "detail": "Could not reach Meta. Try again."})

    if number.status_code != 200:
        detail = (number.json().get("error", {}) or {}).get("message", number.text[:160])
        return ok({"ok": False, "detail": detail}, message="Meta rejected these credentials.")

    info = number.json()
    approved = 0
    if templates.status_code == 200:
        approved = sum(
            1 for t in templates.json().get("data", []) if t.get("status") == "APPROVED"
        )

    return ok(
        {
            "ok": True,
            "phone_number": info.get("display_phone_number", ""),
            "verified_name": info.get("verified_name", ""),
            "quality_rating": info.get("quality_rating", ""),
            "templates_approved": approved,
            # Nothing sends until templates are approved, so a working token
            # with zero approved templates is still a non-working WhatsApp.
            "detail": (
                f"Connected as {info.get('verified_name', 'this account')}. "
                f"{approved} template(s) approved."
            ),
        },
        message="WhatsApp is connected.",
    )
