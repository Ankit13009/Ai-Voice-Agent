"""Google Calendar connect/disconnect (OAuth 2.0 authorization code flow).

CSRF protection: the `state` parameter is a short-lived signed JWT carrying the
business id, not a raw id. Without a signature, anyone could hit the callback with
`state=<victim business id>` and bind their own Google account to that business's
calendar.

The callback is opened by Google in the owner's browser, so it cannot carry an
Authorization header. The signed state is what authenticates it, which is why it
is issued from an authenticated endpoint and expires in ten minutes.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Annotated

import jwt
from fastapi import APIRouter, Query, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.config import get_settings
from app.core.deps import (
    ActiveBusiness,
    DbSession,
    RequireOwner,
    write_audit_log,
)
from app.core.errors import BadRequestError, IntegrationNotConfiguredError, NotFoundError
from app.core.response import ok
from app.db.models import CalendarCredential, Business
from app.services import google_calendar as gcal

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/integrations", tags=["integrations"])


class ServiceAccountConnectRequest(BaseModel):
    """Which calendar to use. Usually the business's Google address.

    Not defaulted to "primary": with a service account, "primary" means the
    service account's own empty calendar, which would accept every booking and
    show the business nothing.
    """

    calendar_id: str = Field(..., min_length=3, max_length=255)

OAUTH_STATE_TTL = timedelta(minutes=10)


def _issue_state(business_id: str, user_id: str) -> str:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "business_id": business_id,
            "user_id": user_id,
            "purpose": "google_oauth",
            "iat": int(now.timestamp()),
            "exp": int((now + OAUTH_STATE_TTL).timestamp()),
        },
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )


def _read_state(state: str) -> dict:
    settings = get_settings()
    try:
        payload = jwt.decode(state, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except jwt.InvalidTokenError as exc:
        logger.warning("Rejected Google OAuth callback with an invalid state: %s", exc)
        raise BadRequestError("This connection link is invalid or has expired.") from exc

    if payload.get("purpose") != "google_oauth":
        raise BadRequestError("This connection link is invalid.")
    return payload


@router.get("/google/authorize", summary="Start connecting Google Calendar")
async def google_authorize(business_id: ActiveBusiness, owner: RequireOwner) -> dict:
    """Return the URL the owner should open to grant calendar access."""
    state = _issue_state(business_id, owner.id)
    return ok(
        {"authorization_url": gcal.build_authorization_url(business_id, state)},
        message="Open this URL to connect the business's Google Calendar.",
    )


@router.get("/google/callback", include_in_schema=False)
async def google_callback(
    db: DbSession,
    code: Annotated[str | None, Query()] = None,
    state: Annotated[str | None, Query()] = None,
    error: Annotated[str | None, Query()] = None,
) -> RedirectResponse:
    """Google redirects the owner's browser here after they approve.

    Always redirects back to the dashboard with a status flag rather than
    rendering JSON, because a human is looking at this page, not a client.
    """
    settings = get_settings()
    frontend = settings.cors_origin_list[0] if settings.cors_origin_list else ""
    done_url = f"{frontend}/settings?google="

    if error:
        logger.info("Google OAuth denied: %s", error)
        return RedirectResponse(f"{done_url}denied", status_code=302)

    if not code or not state:
        return RedirectResponse(f"{done_url}invalid", status_code=302)

    try:
        payload = _read_state(state)
    except BadRequestError:
        return RedirectResponse(f"{done_url}invalid", status_code=302)

    business_id = payload["business_id"]
    business = (await db.execute(select(Business).where(Business.id == business_id))).scalar_one_or_none()
    if business is None:
        return RedirectResponse(f"{done_url}invalid", status_code=302)

    try:
        tokens = await gcal.exchange_code_for_tokens(code)
        email = await gcal.fetch_google_email(tokens.get("access_token", ""))
        await gcal.save_credentials(db, business_id, tokens, email)
        await db.commit()
    except Exception:  # noqa: BLE001
        await db.rollback()
        logger.exception("Google Calendar connection failed for business %s", business_id)
        return RedirectResponse(f"{done_url}failed", status_code=302)

    logger.info("Connected Google Calendar (%s) for business %s", email, business_id)
    return RedirectResponse(f"{done_url}connected", status_code=302)


@router.get("/google/status", summary="Google Calendar connection status")
async def google_status(business_id: ActiveBusiness, db: DbSession, _owner: RequireOwner) -> dict:
    credential = (
        await db.execute(
            select(CalendarCredential).where(CalendarCredential.business_id == business_id)
        )
    ).scalar_one_or_none()

    if credential is None:
        return ok({"connected": False, "email": "", "calendar_id": "", "last_error": ""})

    return ok(
        {
            "connected": credential.is_connected,
            "email": credential.connected_email,
            "calendar_id": credential.calendar_id,
            "last_error": credential.last_error,
        }
    )


@router.delete("/google", summary="Disconnect Google Calendar")
async def google_disconnect(
    business_id: ActiveBusiness, db: DbSession, request: Request, _owner: RequireOwner
) -> dict:
    """Delete the stored credentials.

    The row is removed rather than flagged, so the encrypted refresh token stops
    existing at all. Existing calendar events are left in place: they are the
    business's own data.
    """
    credential = (
        await db.execute(
            select(CalendarCredential).where(CalendarCredential.business_id == business_id)
        )
    ).scalar_one_or_none()
    if credential is None:
        raise NotFoundError("Google Calendar connection")

    await db.delete(credential)
    await write_audit_log(
        db,
        request,
        action="integration.google_disconnected",
        business_id=business_id,
        resource_type="calendar_credential",
    )
    await db.commit()

    return ok(
        None,
        message="Google Calendar disconnected. The agent can no longer check or book slots.",
    )


@router.get("/google/service-account", summary="Service account details for sharing")
async def google_service_account_info(
    business_id: ActiveBusiness, db: DbSession, _owner: RequireOwner
) -> dict:
    """The address a business shares its calendar with, and whether it is in use.

    Offered as an alternative to signing in with Google. Sharing a calendar has
    no expiry, needs no consent screen, and is unaffected by Google's
    verification queue or its 7-day limit on unverified apps, all of which make
    the OAuth route fragile for clients we cannot support hands-on.
    """
    email = gcal.service_account_email()
    credential = (
        await db.execute(
            select(CalendarCredential).where(CalendarCredential.business_id == business_id)
        )
    ).scalar_one_or_none()

    return ok(
        {
            "available": bool(email),
            "service_account_email": email,
            "in_use": bool(credential and credential.auth_mode == "service_account"),
            "calendar_id": credential.calendar_id if credential else "",
        }
    )


@router.post("/google/service-account", summary="Use a shared calendar instead of signing in")
async def connect_service_account(
    payload: ServiceAccountConnectRequest,
    business_id: ActiveBusiness,
    db: DbSession,
    request: Request,
    _owner: RequireOwner,
) -> dict:
    """Point this business at a calendar that has been shared with our service account.

    Verified immediately rather than trusted: without a check, a typo in the
    address or a calendar shared with the wrong permission would be reported as
    connected and would then fail on the first real call, which is the worst
    time to discover it.
    """
    email = gcal.service_account_email()
    if not email:
        raise IntegrationNotConfiguredError(
            "No Google service account is configured on this server."
        )

    credential = (
        await db.execute(
            select(CalendarCredential).where(CalendarCredential.business_id == business_id)
        )
    ).scalar_one_or_none()
    if credential is None:
        credential = CalendarCredential(business_id=business_id)
        db.add(credential)

    credential.auth_mode = "service_account"
    credential.calendar_id = payload.calendar_id.strip()
    credential.connected_email = payload.calendar_id.strip()
    credential.is_connected = True
    credential.last_error = ""
    # No refresh token exists in this mode, and leaving a stale one behind would
    # be a live credential kept for no reason.
    credential.encrypted_refresh_token = ""
    credential.encrypted_access_token = ""
    credential.access_token_expires_at = None
    await db.flush()

    try:
        await gcal.get_busy_windows(
            db,
            business_id,
            credential.calendar_id,
            datetime.now(timezone.utc),
            datetime.now(timezone.utc) + timedelta(days=1),
        )
    except Exception as exc:  # noqa: BLE001
        credential.is_connected = False
        credential.last_error = (
            "Could not read that calendar. Check it is shared with "
            f"{email} with 'Make changes to events' permission."
        )
        await db.commit()
        logger.warning("Service account calendar check failed for %s: %s", business_id, exc)
        raise BadRequestError(credential.last_error) from exc

    await write_audit_log(
        db,
        request,
        action="integration.google_service_account_connected",
        business_id=business_id,
        resource_type="calendar_credential",
        resource_id=credential.id,
        metadata={"calendar_id": credential.calendar_id},
    )
    await db.commit()

    return ok(
        {"connected": True, "calendar_id": credential.calendar_id},
        message="Calendar connected. No sign-in needed and this will not expire.",
    )
