"""Google Calendar integration: OAuth, availability, book, reschedule, cancel.

Two-way sync, without a scheduling middleman:

* Calendar -> us: `freebusy.query` returns everything blocking the calendar,
  including events the business created by hand in Google. Those windows are
  removed from the slots we offer, so a staff_member blocking 3-4pm in their own
  calendar is immediately unbookable by the phone agent.
* us -> Calendar: `events.insert/patch/delete` write the appointment into the
  business's real calendar, and we keep the returned `google_event_id` so later
  reschedules and cancellations act on the same event rather than orphaning it.

Implemented against the REST API with httpx rather than google-api-python-client:
that library is synchronous and would block the event loop during a live call.

Token handling: only the refresh token is durable, and it is Fernet-encrypted at
rest. Access tokens are cached in the row with an expiry and refreshed 60s early
to avoid a race where a token expires mid-request.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.errors import (
    IntegrationNotConfiguredError,
    UpstreamError,
)
from app.core.security import decrypt_secret, encrypt_secret
from app.db.models import Appointment, CalendarCredential, Business, StaffMember

logger = logging.getLogger(__name__)

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"
CALENDAR_API = "https://www.googleapis.com/calendar/v3"

SCOPES = [
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/userinfo.email",
]

# Without these two the integration cannot read availability or write an event,
# so their absence is a failed connection, not a degraded one.
REQUIRED_SCOPES = [
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/calendar.readonly",
]

# Refresh this far before actual expiry, so a token can't die mid-request.
TOKEN_REFRESH_MARGIN = timedelta(seconds=60)
HTTP_TIMEOUT = httpx.Timeout(15.0, connect=5.0)


@dataclass
class Slot:
    starts_at: datetime  # UTC
    ends_at: datetime  # UTC
    staff_member_id: str | None = None
    staff_member_name: str = ""

    def label(self, tz: str) -> str:
        """Speech- and UI-friendly rendering in the business's timezone."""
        local = self.starts_at.astimezone(ZoneInfo(tz))
        # %-I is platform-specific but correct on Linux/macOS, our deploy targets.
        return local.strftime("%A %d %b, %-I:%M %p")


# --------------------------------------------------------------------------- #
# OAuth
# --------------------------------------------------------------------------- #
def build_authorization_url(business_id: str, state: str) -> str:
    """URL the business owner visits to grant calendar access.

    `access_type=offline` + `prompt=consent` is what makes Google return a
    refresh token. Without `prompt=consent`, a re-authorising account gets an
    access token only and the integration silently dies an hour later.
    """
    settings = get_settings()
    if not (settings.google_client_id and settings.google_oauth_redirect_uri):
        raise IntegrationNotConfiguredError(
            "Google Calendar is not configured on this server.",
            log_context={"business_id": business_id},
        )
    params = {
        "client_id": settings.google_client_id,
        "redirect_uri": settings.google_oauth_redirect_uri,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
        "state": state,
    }
    return f"{GOOGLE_AUTH_URL}?{urlencode(params)}"


async def exchange_code_for_tokens(code: str) -> dict:
    settings = get_settings()
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        response = await client.post(
            GOOGLE_TOKEN_URL,
            data={
                "code": code,
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "redirect_uri": settings.google_oauth_redirect_uri,
                "grant_type": "authorization_code",
            },
        )
    if response.status_code != 200:
        logger.error("Google token exchange failed %s: %s", response.status_code, response.text[:400])
        raise UpstreamError("Google Calendar", "Could not complete the Google Calendar connection.")
    return response.json()


async def fetch_google_email(access_token: str) -> str:
    """Best-effort: which account got connected, for the settings page."""
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            response = await client.get(
                GOOGLE_USERINFO_URL, headers={"Authorization": f"Bearer {access_token}"}
            )
        if response.status_code == 200:
            return response.json().get("email", "")
    except Exception:  # noqa: BLE001
        logger.warning("Could not fetch Google account email", exc_info=True)
    return ""


async def save_credentials(
    db: AsyncSession, business_id: str, token_payload: dict, email: str
) -> CalendarCredential:
    refresh_token = token_payload.get("refresh_token", "")
    access_token = token_payload.get("access_token", "")
    expires_in = int(token_payload.get("expires_in", 3600))

    cred = (
        await db.execute(
            select(CalendarCredential).where(CalendarCredential.business_id == business_id)
        )
    ).scalar_one_or_none()
    if cred is None:
        cred = CalendarCredential(business_id=business_id)
        db.add(cred)

    # Google omits refresh_token when re-consenting an already-authorised app.
    # Overwriting with "" there would break the connection an hour later.
    if refresh_token:
        cred.encrypted_refresh_token = encrypt_secret(refresh_token)
    cred.encrypted_access_token = encrypt_secret(access_token)
    cred.access_token_expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
    cred.connected_email = email or cred.connected_email
    granted = token_payload.get("scope", "").split()
    cred.scopes = granted or SCOPES

    # Google grants only the scopes registered on the OAuth consent screen and
    # silently drops the rest. Without this check the connection reports success
    # and then every availability lookup fails with an opaque 403.
    missing = [s for s in REQUIRED_SCOPES if s not in granted] if granted else []
    if missing:
        cred.is_connected = False
        cred.last_error = (
            "Google did not grant calendar access. Add the Calendar scopes to your "
            "OAuth consent screen in Google Cloud Console, then reconnect."
        )
        logger.error("Google granted %s but calendar scopes are missing: %s", granted, missing)
    else:
        cred.is_connected = bool(cred.encrypted_refresh_token)
        cred.last_error = ""

    await db.flush()
    return cred


async def _get_access_token(db: AsyncSession, business_id: str) -> tuple[str, CalendarCredential]:
    """Return a valid access token, refreshing it if needed."""
    cred = (
        await db.execute(
            select(CalendarCredential).where(CalendarCredential.business_id == business_id)
        )
    ).scalar_one_or_none()

    if cred is None or not cred.is_connected:
        raise IntegrationNotConfiguredError(
            "This business has not connected a Google Calendar yet.",
            log_context={"business_id": business_id},
        )

    cached = decrypt_secret(cred.encrypted_access_token)
    expires_at = cred.access_token_expires_at
    if cached and expires_at and expires_at - TOKEN_REFRESH_MARGIN > datetime.now(timezone.utc):
        return cached, cred

    refresh_token = decrypt_secret(cred.encrypted_refresh_token)
    if not refresh_token:
        cred.is_connected = False
        cred.last_error = "Stored refresh token is unreadable. Reconnect Google Calendar."
        await db.flush()
        raise IntegrationNotConfiguredError(
            "The Google Calendar connection needs to be re-authorised."
        )

    settings = get_settings()
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        response = await client.post(
            GOOGLE_TOKEN_URL,
            data={
                "refresh_token": refresh_token,
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "grant_type": "refresh_token",
            },
        )

    if response.status_code != 200:
        # invalid_grant means the user revoked access or the token was rotated.
        # Mark it disconnected so the UI prompts a reconnect instead of the agent
        # failing one call at a time forever.
        body = response.text[:300]
        logger.error("Google token refresh failed %s: %s", response.status_code, body)
        if "invalid_grant" in body:
            # Alert only on the transition. A disconnected calendar is hit again
            # on every subsequent call, and re-alerting each time would bury the
            # owner in identical messages for a single outage.
            was_connected = cred.is_connected
            cred.is_connected = False
            cred.last_error = "Google access was revoked. Reconnect the calendar."
            await db.flush()

            if was_connected:
                business = await db.get(Business, business_id)
                if business is not None:
                    # Imported here: notifications imports this module for the
                    # waitlist path, and a module-level import would be circular.
                    from app.services.notifications import (
                        notify_owner_calendar_disconnected,
                    )

                    await notify_owner_calendar_disconnected(db, business)

            raise IntegrationNotConfiguredError(
                "The Google Calendar connection was revoked. Please reconnect it."
            )
        raise UpstreamError("Google Calendar")

    payload = response.json()
    access_token = payload.get("access_token", "")
    cred.encrypted_access_token = encrypt_secret(access_token)
    cred.access_token_expires_at = datetime.now(timezone.utc) + timedelta(
        seconds=int(payload.get("expires_in", 3600))
    )
    cred.last_error = ""
    await db.flush()
    return access_token, cred


async def _api_request(
    db: AsyncSession,
    business_id: str,
    method: str,
    path: str,
    *,
    json_body: dict | None = None,
    params: dict | None = None,
) -> dict:
    token, _cred = await _get_access_token(db, business_id)
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        response = await client.request(
            method,
            f"{CALENDAR_API}{path}",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json=json_body,
            params=params,
        )

    if response.status_code in (200, 201, 204):
        return response.json() if response.content else {}

    # 410 Gone: the event was already deleted in Google. Treat as success at the
    # call site rather than failing a cancellation the user has already made.
    logger.error(
        "Google Calendar %s %s failed %s: %s",
        method,
        path,
        response.status_code,
        response.text[:600],
    )

    # 403 here is almost always missing scopes rather than an outage. Telling the
    # user Google is "unavailable" sends them to check a status page instead of
    # the setting that is actually wrong.
    if response.status_code == 403 and "scope" in response.text.lower():
        raise IntegrationNotConfiguredError(
            "Google did not grant calendar access. Add the Calendar scopes to the "
            "OAuth consent screen, then disconnect and reconnect Google Calendar.",
            log_context={"path": path, "business_id": business_id},
        )

    raise UpstreamError(
        "Google Calendar",
        log_context={"status": response.status_code, "path": path, "business_id": business_id},
    )


# --------------------------------------------------------------------------- #
# Availability
# --------------------------------------------------------------------------- #
async def get_busy_windows(
    db: AsyncSession,
    business_id: str,
    calendar_id: str,
    start: datetime,
    end: datetime,
) -> list[tuple[datetime, datetime]]:
    """Everything already blocking the calendar in [start, end).

    This is the "calendar -> us" half of the two-way sync, and it is why an
    event a receptionist typed straight into Google still blocks the phone agent.
    """
    payload = {
        "timeMin": start.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "timeMax": end.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "items": [{"id": calendar_id}],
    }
    data = await _api_request(db, business_id, "POST", "/freeBusy", json_body=payload)
    calendars = data.get("calendars", {}) or {}
    entry = calendars.get(calendar_id, {}) or {}

    if entry.get("errors"):
        logger.error("freeBusy returned errors for %s: %s", calendar_id, entry["errors"])
        raise UpstreamError(
            "Google Calendar",
            "Could not read the business calendar. Check that it is shared with the connected account.",
        )

    windows: list[tuple[datetime, datetime]] = []
    for block in entry.get("busy", []) or []:
        try:
            windows.append(
                (
                    datetime.fromisoformat(block["start"].replace("Z", "+00:00")),
                    datetime.fromisoformat(block["end"].replace("Z", "+00:00")),
                )
            )
        except (KeyError, ValueError):
            logger.warning("Skipping malformed busy window: %r", block)
    return windows


def _overlaps(start: datetime, end: datetime, windows: list[tuple[datetime, datetime]]) -> bool:
    # Touching endpoints are not an overlap: a slot may start exactly when the
    # previous appointment ends.
    return any(start < busy_end and end > busy_start for busy_start, busy_end in windows)


def generate_candidate_slots(
    *,
    start: datetime,
    end: datetime,
    tz: str,
    opens_at: time,
    closes_at: time,
    working_days: list[int],
    duration_minutes: int,
    step_minutes: int,
) -> list[tuple[datetime, datetime]]:
    """Every in-hours slot in the range, before availability is considered.

    Walks day by day in the business's local timezone rather than adding fixed
    24-hour offsets in UTC, so a DST shift moves the working window with the
    wall clock instead of sliding every slot by an hour.
    """
    zone = ZoneInfo(tz)
    local_start = start.astimezone(zone)
    local_end = end.astimezone(zone)
    duration = timedelta(minutes=duration_minutes)
    step = timedelta(minutes=step_minutes)

    slots: list[tuple[datetime, datetime]] = []
    day = local_start.date()
    last_day = local_end.date()

    while day <= last_day:
        if day.isoweekday() in working_days:
            cursor = datetime.combine(day, opens_at, tzinfo=zone)
            day_close = datetime.combine(day, closes_at, tzinfo=zone)
            while cursor + duration <= day_close:
                slot_start = cursor.astimezone(timezone.utc)
                slot_end = (cursor + duration).astimezone(timezone.utc)
                if slot_start >= start and slot_end <= end:
                    slots.append((slot_start, slot_end))
                cursor += step
        day += timedelta(days=1)

    return slots


async def find_available_slots(
    db: AsyncSession,
    business: Business,
    *,
    start: datetime,
    end: datetime,
    staff_member: StaffMember | None = None,
    duration_minutes: int | None = None,
    limit: int = 20,
) -> list[Slot]:
    """Bookable openings, honouring working hours and the live calendar."""
    calendar_id = _calendar_id_for(business, staff_member)
    duration = duration_minutes or (
        staff_member.consultation_duration_minutes if staff_member else business.slot_duration_minutes
    )

    # Never offer a slot in the past, even if the caller asks for today.
    now = datetime.now(timezone.utc)
    effective_start = max(start, now)
    if effective_start >= end:
        return []

    candidates = generate_candidate_slots(
        start=effective_start,
        end=end,
        tz=business.timezone,
        opens_at=(staff_member.opens_at if staff_member and staff_member.opens_at else business.opens_at),
        closes_at=(staff_member.closes_at if staff_member and staff_member.closes_at else business.closes_at),
        working_days=(
            staff_member.working_days if staff_member and staff_member.working_days else business.working_days
        ),
        duration_minutes=duration,
        step_minutes=business.slot_duration_minutes,
    )
    if not candidates:
        return []

    busy = await get_busy_windows(db, business.id, calendar_id, effective_start, end)

    available: list[Slot] = []
    for slot_start, slot_end in candidates:
        if _overlaps(slot_start, slot_end, busy):
            continue
        available.append(
            Slot(
                starts_at=slot_start,
                ends_at=slot_end,
                staff_member_id=staff_member.id if staff_member else None,
                staff_member_name=staff_member.name if staff_member else "",
            )
        )
        if len(available) >= limit:
            break

    return available


async def is_slot_free(
    db: AsyncSession,
    business: Business,
    start: datetime,
    end: datetime,
    staff_member: StaffMember | None = None,
) -> bool:
    """Re-check one specific slot immediately before writing to the calendar.

    The gap between offering a time on the call and the caller accepting it is
    long enough for a walk-in to take the slot, so the check is repeated at
    write time rather than trusted from the earlier availability query.
    """
    calendar_id = _calendar_id_for(business, staff_member)
    busy = await get_busy_windows(db, business.id, calendar_id, start, end)
    return not _overlaps(start, end, busy)


def _calendar_id_for(business: Business, staff_member: StaffMember | None) -> str:
    """Per-staff_member calendar when set, otherwise the business's connected calendar."""
    if staff_member and staff_member.google_calendar_id:
        return staff_member.google_calendar_id
    return "primary"


# --------------------------------------------------------------------------- #
# Event write operations
# --------------------------------------------------------------------------- #
def _event_body(
    *,
    business: Business,
    customer_name: str,
    customer_phone: str,
    reason: str,
    start: datetime,
    end: datetime,
    staff_member_name: str = "",
) -> dict:
    title = f"{customer_name or 'Customer'}"
    if staff_member_name:
        title += f" with {staff_member_name}"
    description_lines = [
        f"Customer: {customer_name or 'Not given'}",
        f"Phone: {customer_phone or 'Not given'}",
        f"Reason: {reason or 'Not given'}",
        "",
        f"Booked by the {business.agent_name} AI receptionist.",
    ]
    return {
        "summary": title,
        "description": "\n".join(description_lines),
        "start": {"dateTime": start.astimezone(timezone.utc).isoformat(), "timeZone": "UTC"},
        "end": {"dateTime": end.astimezone(timezone.utc).isoformat(), "timeZone": "UTC"},
        # Reminders are handled over WhatsApp, so Google's own popups are off to
        # avoid the business getting two sets of notifications.
        "reminders": {"useDefault": False, "overrides": []},
        "extendedProperties": {
            "private": {"source": "business-ai-receptionist", "business_id": business.id}
        },
    }


async def create_event(
    db: AsyncSession,
    business: Business,
    *,
    customer_name: str,
    customer_phone: str,
    reason: str,
    start: datetime,
    end: datetime,
    staff_member: StaffMember | None = None,
) -> tuple[str, str]:
    """Create the calendar event. Returns (event_id, calendar_id)."""
    calendar_id = _calendar_id_for(business, staff_member)
    body = _event_body(
        business=business,
        customer_name=customer_name,
        customer_phone=customer_phone,
        reason=reason,
        start=start,
        end=end,
        staff_member_name=staff_member.name if staff_member else "",
    )
    data = await _api_request(
        db, business.id, "POST", f"/calendars/{calendar_id}/events", json_body=body
    )
    event_id = data.get("id", "")
    logger.info("Created Google Calendar event %s for business %s", event_id, business.id)
    return event_id, calendar_id


async def update_event_time(
    db: AsyncSession,
    business: Business,
    appointment: Appointment,
    *,
    start: datetime,
    end: datetime,
) -> None:
    """Move an existing event (reschedule) rather than delete-and-recreate, so
    the customer's calendar invite keeps its identity."""
    if not appointment.google_event_id:
        logger.info(
            "Appointment %s has no calendar event to move; skipping.", appointment.id
        )
        return
    calendar_id = appointment.google_calendar_id or "primary"
    body = {
        "start": {"dateTime": start.astimezone(timezone.utc).isoformat(), "timeZone": "UTC"},
        "end": {"dateTime": end.astimezone(timezone.utc).isoformat(), "timeZone": "UTC"},
    }
    await _api_request(
        db,
        business.id,
        "PATCH",
        f"/calendars/{calendar_id}/events/{appointment.google_event_id}",
        json_body=body,
    )


async def delete_event(db: AsyncSession, business: Business, appointment: Appointment) -> None:
    """Remove the event on cancellation.

    An already-deleted event (404/410) is treated as success: the desired end
    state is "not on the calendar", which is exactly where we are.
    """
    if not appointment.google_event_id:
        return
    calendar_id = appointment.google_calendar_id or "primary"
    try:
        await _api_request(
            db,
            business.id,
            "DELETE",
            f"/calendars/{calendar_id}/events/{appointment.google_event_id}",
        )
    except UpstreamError as exc:
        status = exc.log_context.get("status")
        if status in (404, 410):
            logger.info(
                "Calendar event %s already gone; treating cancellation as done.",
                appointment.google_event_id,
            )
            return
        raise
