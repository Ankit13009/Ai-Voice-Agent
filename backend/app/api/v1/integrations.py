"""Google Calendar connect/disconnect (OAuth 2.0 authorization code flow).

CSRF protection: the `state` parameter is a short-lived signed JWT carrying the
clinic id, not a raw id. Without a signature, anyone could hit the callback with
`state=<victim clinic id>` and bind their own Google account to that clinic's
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
from sqlalchemy import select

from app.config import get_settings
from app.core.deps import (
    ActiveClinic,
    DbSession,
    RequireOwner,
    write_audit_log,
)
from app.core.errors import BadRequestError, NotFoundError
from app.core.response import ok
from app.db.models import CalendarCredential, Clinic
from app.services import google_calendar as gcal

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/integrations", tags=["integrations"])

OAUTH_STATE_TTL = timedelta(minutes=10)


def _issue_state(clinic_id: str, user_id: str) -> str:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "clinic_id": clinic_id,
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
async def google_authorize(clinic_id: ActiveClinic, owner: RequireOwner) -> dict:
    """Return the URL the owner should open to grant calendar access."""
    state = _issue_state(clinic_id, owner.id)
    return ok(
        {"authorization_url": gcal.build_authorization_url(clinic_id, state)},
        message="Open this URL to connect the clinic's Google Calendar.",
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

    clinic_id = payload["clinic_id"]
    clinic = (await db.execute(select(Clinic).where(Clinic.id == clinic_id))).scalar_one_or_none()
    if clinic is None:
        return RedirectResponse(f"{done_url}invalid", status_code=302)

    try:
        tokens = await gcal.exchange_code_for_tokens(code)
        email = await gcal.fetch_google_email(tokens.get("access_token", ""))
        await gcal.save_credentials(db, clinic_id, tokens, email)
        await db.commit()
    except Exception:  # noqa: BLE001
        await db.rollback()
        logger.exception("Google Calendar connection failed for clinic %s", clinic_id)
        return RedirectResponse(f"{done_url}failed", status_code=302)

    logger.info("Connected Google Calendar (%s) for clinic %s", email, clinic_id)
    return RedirectResponse(f"{done_url}connected", status_code=302)


@router.get("/google/status", summary="Google Calendar connection status")
async def google_status(clinic_id: ActiveClinic, db: DbSession, _owner: RequireOwner) -> dict:
    credential = (
        await db.execute(
            select(CalendarCredential).where(CalendarCredential.clinic_id == clinic_id)
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
    clinic_id: ActiveClinic, db: DbSession, request: Request, _owner: RequireOwner
) -> dict:
    """Delete the stored credentials.

    The row is removed rather than flagged, so the encrypted refresh token stops
    existing at all. Existing calendar events are left in place: they are the
    clinic's own data.
    """
    credential = (
        await db.execute(
            select(CalendarCredential).where(CalendarCredential.clinic_id == clinic_id)
        )
    ).scalar_one_or_none()
    if credential is None:
        raise NotFoundError("Google Calendar connection")

    await db.delete(credential)
    await write_audit_log(
        db,
        request,
        action="integration.google_disconnected",
        clinic_id=clinic_id,
        resource_type="calendar_credential",
    )
    await db.commit()

    return ok(
        None,
        message="Google Calendar disconnected. The agent can no longer check or book slots.",
    )
