"""VAPI webhooks: live tool calls, and the end-of-call report.

Security model. These endpoints are unauthenticated in the JWT sense (VAPI has
no user token), so two things stand in for that:

1. Every request must carry the shared secret configured on the assistant, and
   it is compared in constant time. Without this, anyone who learns the URL can
   book, move, and cancel appointments for any business.

2. The business is resolved from the *call*, never from the tool arguments. The
   model relays whatever the caller says, and a caller can say anything, so a
   `business_id` argument would be a prompt-injection path straight through tenant
   isolation.

Tool responses are shaped for a model to read aloud, not for a UI: a `status`
the prompt knows how to branch on, plus short human-readable strings.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Header, Request
from sqlalchemy import select

from app.config import get_settings
from app.core.deps import DbSession
from app.core.errors import AppError, WebhookSignatureError
from app.core.security import verify_shared_secret
from app.db.models import (
    Appointment,
    AppointmentStatus,
    Call,
    CallOutcome,
    Business,
    StaffMember,
    Language,
    Customer,
)
from app.services import appointments as appointment_service
from app.services import google_calendar as gcal

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks/vapi", tags=["webhooks"])

# How far ahead to look when the caller does not name a day.
DEFAULT_SEARCH_DAYS = 14
MAX_SLOTS_TO_OFFER = 3

TIME_WINDOWS = {
    "morning": (6, 12),
    "afternoon": (12, 17),
    "evening": (17, 22),
}


def _verify(secret_header: str | None) -> None:
    settings = get_settings()
    if not settings.vapi_webhook_secret:
        # Refuse rather than fall open. An unauthenticated write endpoint is a
        # worse outcome than a misconfigured integration.
        logger.error("VAPI_WEBHOOK_SECRET is not set; rejecting webhook.")
        raise WebhookSignatureError("Webhook authentication is not configured.")
    if not verify_shared_secret(secret_header or "", settings.vapi_webhook_secret):
        logger.warning("Rejected VAPI webhook with an invalid secret.")
        raise WebhookSignatureError()


async def _resolve_business(db, message: dict) -> Business | None:
    """Find the business for this call.

    Tries the assistant id first (set when we provisioned the assistant), then
    the dialed number. Both come from VAPI's own call object, not from anything
    the caller or the model can influence.
    """
    call = message.get("call", {}) or {}
    assistant_id = call.get("assistantId") or message.get("assistant", {}).get("id", "")

    if assistant_id:
        business = (
            await db.execute(select(Business).where(Business.vapi_assistant_id == assistant_id))
        ).scalar_one_or_none()
        if business:
            return business

    dialed = (call.get("phoneNumber") or {}).get("number") or call.get("phoneNumberId", "")
    if dialed:
        business = (
            await db.execute(select(Business).where(Business.phone_number == dialed))
        ).scalar_one_or_none()
        if business:
            return business

    logger.error("Could not resolve a business for VAPI call %s", call.get("id", "?"))
    return None


def _caller_number(message: dict) -> str:
    """The number the caller is dialling from.

    Browser test calls (`webCall`) have none: nothing was dialled. A configured
    stand-in keeps the test path identical to a real call, so testing exercises
    the flow customers will actually get rather than the "ask for a number"
    branch that only exists because the test harness lacks caller ID.
    """
    call = message.get("call", {}) or {}
    number = (call.get("customer") or {}).get("number", "")
    if number:
        return number

    if call.get("type") == "webCall":
        from app.config import get_settings

        stand_in = get_settings().test_caller_number
        if stand_in:
            logger.info("Browser test call: using the configured stand-in caller number.")
            return stand_in
    return ""


def _tool_result(status: str, **fields: Any) -> dict[str, Any]:
    """Every tool returns `status` plus context. The prompt branches on `status`."""
    return {"status": status, **fields}


# --------------------------------------------------------------------------- #
# Tool handlers
# --------------------------------------------------------------------------- #
async def _handle_check_availability(db, business: Business, args: dict, caller: str) -> dict:
    zone = ZoneInfo(business.timezone)
    now = datetime.now(timezone.utc)

    date_str = (args.get("date") or "").strip()
    requested_day = None
    if date_str:
        try:
            requested_day = datetime.strptime(date_str, "%Y-%m-%d").date()
            start = datetime.combine(requested_day, business.opens_at, tzinfo=zone).astimezone(timezone.utc)
            end = datetime.combine(requested_day, business.closes_at, tzinfo=zone).astimezone(timezone.utc)
        except ValueError:
            logger.info("Agent sent an unparseable date %r; falling back to open search.", date_str)
            start, end = now, now + timedelta(days=DEFAULT_SEARCH_DAYS)
    else:
        start, end = now, now + timedelta(days=DEFAULT_SEARCH_DAYS)

    # A closed day is not a full day. Without this the agent tells the caller
    # "we are fully booked", which is untrue, and then tries morning, afternoon
    # and evening in turn against a day the business is shut, burning three tool
    # calls and a minute of the caller's time.
    working_days = business.working_days or []
    if requested_day is not None and working_days and requested_day.isoweekday() not in working_days:
        next_open = requested_day
        for _ in range(7):
            next_open += timedelta(days=1)
            if next_open.isoweekday() in working_days:
                break

        open_start = datetime.combine(next_open, business.opens_at, tzinfo=zone).astimezone(timezone.utc)
        open_end = datetime.combine(next_open, business.closes_at, tzinfo=zone).astimezone(timezone.utc)
        try:
            slots = await gcal.find_available_slots(
                db, business, start=max(open_start, now), end=open_end, limit=MAX_SLOTS_TO_OFFER
            )
        except AppError:
            slots = []

        return _tool_result(
            "closed",
            closed_on=requested_day.strftime("%A"),
            next_open_day=next_open.strftime("%A %d %B"),
            available=[
                {"starts_at": s.starts_at.isoformat(), "label": s.label(business.timezone)}
                for s in slots
            ],
            message=(
                f"The business is closed on {requested_day.strftime('%A')}. "
                f"The next open day is {next_open.strftime('%A %d %B')}."
            ),
        )

    staff_member = None
    staff_member_id = (args.get("staff_member_id") or "").strip()
    if staff_member_id:
        staff_member = (
            await db.execute(
                select(StaffMember).where(StaffMember.id == staff_member_id, StaffMember.business_id == business.id)
            )
        ).scalar_one_or_none()
        if staff_member is None:
            # A hallucinated staff_member id must not silently book with the wrong one.
            return _tool_result(
                "not_found",
                message="I could not find that staff_member. Could you say the name again?",
            )

    try:
        slots = await gcal.find_available_slots(
            db, business, start=start, end=end, staff_member=staff_member, limit=40
        )
    except AppError as exc:
        logger.error("Availability lookup failed for business %s: %s", business.id, exc.message)
        return _tool_result(
            "error", message="I could not reach the business's calendar just now."
        )

    preference = (args.get("preferred_time_of_day") or "any").lower()
    window = TIME_WINDOWS.get(preference)
    if window:
        start_hour, end_hour = window
        preferred = [
            s for s in slots if start_hour <= s.starts_at.astimezone(zone).hour < end_hour
        ]
        # Fall back to any slot rather than telling the caller nothing is free.
        slots = preferred or slots

    if not slots:
        return _tool_result(
            "unavailable",
            available=[],
            message="There are no free slots in that period.",
        )

    offered = slots[:MAX_SLOTS_TO_OFFER]
    return _tool_result(
        "success",
        available=[
            {
                # The model must echo this verbatim into book_appointment, which
                # is why it is a full offset-bearing ISO string.
                "starts_at": s.starts_at.isoformat(),
                "label": s.label(business.timezone),
                "staff_member_id": s.staff_member_id or "",
                "staff_member_name": s.staff_member_name,
            }
            for s in offered
        ],
    )


async def _handle_book_appointment(db, business: Business, args: dict, caller: str) -> dict:
    starts_raw = (args.get("starts_at") or "").strip()
    try:
        starts_at = datetime.fromisoformat(starts_raw.replace("Z", "+00:00"))
    except ValueError:
        logger.warning("Agent sent an unparseable starts_at %r", starts_raw)
        return _tool_result(
            "error", message="I did not catch that time correctly. Could you repeat it?"
        )
    if starts_at.tzinfo is None:
        starts_at = starts_at.replace(tzinfo=ZoneInfo(business.timezone))

    # Prefer the number given on the call; fall back to the caller ID.
    phone = (args.get("customer_phone") or "").strip() or caller
    if not phone:
        # Distinct from "error": nothing is broken, the phone system simply did
        # not pass a caller ID (browser test calls, withheld numbers). The agent
        # should ask for one rather than apologise for a fault.
        return _tool_result(
            "need_phone",
            message="No number was provided by the phone system. Ask the caller for one.",
        )
    if not phone.startswith("+"):
        phone = "+" + phone.lstrip("0")

    try:
        appointment = await appointment_service.book_appointment(
            db,
            business,
            customer_name=(args.get("customer_name") or "").strip(),
            customer_phone=phone,
            starts_at=starts_at,
            staff_member_id=(args.get("staff_member_id") or "").strip() or None,
            reason=(args.get("reason") or "").strip(),
            language=Language.MIXED,
        )
    except AppError as exc:
        if exc.code.value == "SLOT_UNAVAILABLE":
            alternatives = await gcal.find_available_slots(
                db,
                business,
                start=datetime.now(timezone.utc),
                end=datetime.now(timezone.utc) + timedelta(days=DEFAULT_SEARCH_DAYS),
                limit=MAX_SLOTS_TO_OFFER,
            )
            return _tool_result(
                "unavailable",
                available=[
                    {"starts_at": s.starts_at.isoformat(), "label": s.label(business.timezone)}
                    for s in alternatives
                ],
                message="That slot was just taken.",
            )
        logger.error("Booking failed for business %s: %s", business.id, exc.message)
        return _tool_result("error", message="I could not complete the booking just now.")

    return _tool_result(
        "success",
        appointment_id=appointment.id,
        when=appointment.starts_at.astimezone(ZoneInfo(business.timezone)).strftime(
            "%A %d %B, %-I:%M %p"
        ),
        message="The appointment is confirmed and a WhatsApp confirmation is on its way.",
    )


async def _handle_find_appointment(db, business: Business, args: dict, caller: str) -> dict:
    phone = (args.get("customer_phone") or "").strip() or caller
    if phone and not phone.startswith("+"):
        phone = "+" + phone.lstrip("0")

    appointment = await appointment_service.find_customer_appointment(db, business.id, phone)
    if appointment is None:
        return _tool_result(
            "not_found",
            message="I could not find an upcoming appointment for this number.",
        )

    customer = (
        await db.execute(select(Customer).where(Customer.id == appointment.customer_id))
    ).scalar_one()
    return _tool_result(
        "success",
        appointment_id=appointment.id,
        customer_name=customer.name,
        when=appointment.starts_at.astimezone(ZoneInfo(business.timezone)).strftime(
            "%A %d %B, %-I:%M %p"
        ),
    )


async def _load_scoped_appointment(db, business: Business, appointment_id: str) -> Appointment | None:
    """Load by id *and* business, so a hallucinated or guessed id from another
    tenant resolves to nothing rather than to someone else's appointment."""
    return (
        await db.execute(
            select(Appointment).where(
                Appointment.id == appointment_id, Appointment.business_id == business.id
            )
        )
    ).scalar_one_or_none()


async def _handle_reschedule(db, business: Business, args: dict, caller: str) -> dict:
    appointment = await _load_scoped_appointment(db, business, (args.get("appointment_id") or "").strip())
    if appointment is None:
        return _tool_result("not_found", message="I could not find that appointment.")

    try:
        starts_at = datetime.fromisoformat((args.get("starts_at") or "").replace("Z", "+00:00"))
    except ValueError:
        return _tool_result("error", message="I did not catch the new time correctly.")
    if starts_at.tzinfo is None:
        starts_at = starts_at.replace(tzinfo=ZoneInfo(business.timezone))

    try:
        updated = await appointment_service.reschedule_appointment(
            db, business, appointment, starts_at=starts_at, reason=(args.get("reason") or "").strip()
        )
    except AppError as exc:
        if exc.code.value == "SLOT_UNAVAILABLE":
            return _tool_result("unavailable", available=[], message="That new time is not free.")
        logger.error("Reschedule failed for business %s: %s", business.id, exc.message)
        return _tool_result("error", message="I could not move the appointment just now.")

    return _tool_result(
        "success",
        appointment_id=updated.id,
        when=updated.starts_at.astimezone(ZoneInfo(business.timezone)).strftime("%A %d %B, %-I:%M %p"),
    )


async def _handle_cancel(db, business: Business, args: dict, caller: str) -> dict:
    appointment = await _load_scoped_appointment(db, business, (args.get("appointment_id") or "").strip())
    if appointment is None:
        return _tool_result("not_found", message="I could not find that appointment.")

    try:
        await appointment_service.cancel_appointment(
            db, business, appointment, reason=(args.get("reason") or "").strip()
        )
    except AppError as exc:
        logger.error("Cancellation failed for business %s: %s", business.id, exc.message)
        return _tool_result("error", message="I could not cancel the appointment just now.")

    return _tool_result("success", message="The appointment has been cancelled.")


async def _handle_lookup_caller(db, business: Business, args: dict, caller: str) -> dict:
    """Tell the agent who is calling, so it does not ask a returning customer
    for a name the business already has.

    Called once immediately after the greeting. The greeting itself is a static
    string spoken while this runs, so the lookup costs no perceptible time.
    """
    if not caller:
        return _tool_result("not_found", message="No caller ID available.")

    customer = (
        await db.execute(
            select(Customer).where(
                Customer.business_id == business.id, Customer.phone == caller
            )
        )
    ).scalar_one_or_none()

    if customer is None or not customer.name:
        return _tool_result("not_found", message="This number has not called before.")

    upcoming = (
        await db.execute(
            select(Appointment)
            .where(
                Appointment.business_id == business.id,
                Appointment.customer_id == customer.id,
                Appointment.status.in_(
                    [AppointmentStatus.SCHEDULED, AppointmentStatus.RESCHEDULED]
                ),
                Appointment.starts_at > datetime.now(timezone.utc),
            )
            .order_by(Appointment.starts_at.asc())
            .limit(1)
        )
    ).scalar_one_or_none()

    result = {
        "customer_name": customer.name,
        "first_name": customer.name.split()[0],
    }
    if upcoming:
        result["existing_appointment_id"] = upcoming.id
        result["existing_appointment_when"] = upcoming.starts_at.astimezone(
            ZoneInfo(business.timezone)
        ).strftime("%A %d %B, %-I:%M %p")

    return _tool_result("success", **result)


async def _handle_join_waitlist(db, business: Business, args: dict, caller: str) -> dict:
    from app.db.models import WaitlistEntry

    phone = caller
    if phone and not phone.startswith("+"):
        phone = "+" + phone.lstrip("0")
    if not phone:
        return _tool_result("need_phone", message="No number was provided by the phone system.")

    zone = ZoneInfo(business.timezone)
    try:
        start_day = datetime.strptime((args.get("date_from") or "").strip(), "%Y-%m-%d").date()
        end_day = datetime.strptime(
            (args.get("date_to") or args.get("date_from") or "").strip(), "%Y-%m-%d"
        ).date()
    except ValueError:
        return _tool_result("error", message="I did not catch those dates correctly.")

    customer = await appointment_service.get_or_create_customer(
        db,
        business_id=business.id,
        phone=phone,
        name=(args.get("customer_name") or "").strip(),
        language=Language.MIXED,
    )

    db.add(
        WaitlistEntry(
            business_id=business.id,
            customer_id=customer.id,
            preferred_from=datetime.combine(start_day, business.opens_at, tzinfo=zone).astimezone(
                timezone.utc
            ),
            preferred_to=datetime.combine(end_day, business.closes_at, tzinfo=zone).astimezone(
                timezone.utc
            ),
            reason=(args.get("reason") or "").strip(),
        )
    )
    await db.flush()

    return _tool_result(
        "success",
        message=(
            "Added to the waiting list. They will get a WhatsApp message if a slot "
            "opens in that period."
        ),
    )


TOOL_HANDLERS = {
    "lookup_caller": _handle_lookup_caller,
    "check_availability": _handle_check_availability,
    "book_appointment": _handle_book_appointment,
    "find_appointment": _handle_find_appointment,
    "reschedule_appointment": _handle_reschedule,
    "cancel_appointment": _handle_cancel,
    "join_waitlist": _handle_join_waitlist,
}


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #
@router.post("/tool")
async def handle_tool_call(
    request: Request,
    db: DbSession,
    x_vapi_secret: str | None = Header(default=None, alias="X-Vapi-Secret"),
) -> dict:
    """Execute the tool(s) VAPI is calling mid-conversation.

    This endpoint intentionally does NOT use the standard response envelope:
    VAPI requires `{"results": [{"toolCallId": ..., "result": ...}]}` and would
    not understand our wrapper. It is a machine-to-machine contract with a fixed
    external shape, not part of the dashboard API.
    """
    _verify(x_vapi_secret)

    body = await request.json()
    message = body.get("message", {}) or {}
    tool_calls = message.get("toolCallList") or message.get("toolCalls") or []

    business = await _resolve_business(db, message)
    caller = _caller_number(message)

    results = []
    for tool_call in tool_calls:
        call_id = tool_call.get("id", "")
        function = tool_call.get("function", {}) or {}
        name = function.get("name", "")
        arguments = function.get("arguments", {}) or {}
        if isinstance(arguments, str):
            import json

            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError:
                arguments = {}

        if business is None:
            results.append(
                {
                    "toolCallId": call_id,
                    "result": _tool_result(
                        "error", message="I am not able to access the business system right now."
                    ),
                }
            )
            continue

        handler = TOOL_HANDLERS.get(name)
        if handler is None:
            logger.warning("VAPI called unknown tool %r", name)
            results.append(
                {"toolCallId": call_id, "result": _tool_result("error", message="Unknown action.")}
            )
            continue

        try:
            result = await handler(db, business, arguments, caller)
            await db.commit()
        except Exception:  # noqa: BLE001
            await db.rollback()
            logger.exception("Tool %s crashed for business %s", name, business.id)
            result = _tool_result("error", message="Something went wrong on the business's system.")

        results.append({"toolCallId": call_id, "result": result})

    return {"results": results}


@router.post("/events")
async def handle_event(
    request: Request,
    db: DbSession,
    x_vapi_secret: str | None = Header(default=None, alias="X-Vapi-Secret"),
) -> dict:
    """Persist the end-of-call report: transcript, recording, duration, outcome."""
    _verify(x_vapi_secret)

    body = await request.json()
    message = body.get("message", {}) or {}
    event_type = message.get("type", "")

    if event_type != "end-of-call-report":
        # status-update and friends are acknowledged and ignored.
        return {"received": True}

    business = await _resolve_business(db, message)
    if business is None:
        return {"received": True}

    call_payload = message.get("call", {}) or {}
    vapi_call_id = call_payload.get("id", "")
    caller = _caller_number(message)

    existing = (
        await db.execute(
            select(Call).where(Call.vapi_call_id == vapi_call_id, Call.business_id == business.id)
        )
    ).scalar_one_or_none()
    call_row = existing or Call(business_id=business.id, vapi_call_id=vapi_call_id)
    if existing is None:
        db.add(call_row)

    call_row.caller_number = caller
    call_row.transcript = message.get("transcript", "") or ""
    call_row.summary = message.get("summary", "") or ""
    call_row.recording_url = message.get("recordingUrl", "") or ""
    call_row.ended_reason = message.get("endedReason", "") or ""

    started = message.get("startedAt") or call_payload.get("startedAt")
    ended = message.get("endedAt") or call_payload.get("endedAt")
    for raw, attr in ((started, "started_at"), (ended, "ended_at")):
        if raw:
            try:
                setattr(call_row, attr, datetime.fromisoformat(str(raw).replace("Z", "+00:00")))
            except ValueError:
                logger.warning("Unparseable %s timestamp %r", attr, raw)

    if call_row.started_at and call_row.ended_at:
        call_row.duration_seconds = max(
            0, int((call_row.ended_at - call_row.started_at).total_seconds())
        )

    cost = message.get("cost")
    if isinstance(cost, (int, float)):
        # VAPI reports USD; store paise so the dashboard can total without floats.
        call_row.cost_paise = int(round(float(cost) * 100 * 83))

    # Outcome is derived from what actually happened in the database during the
    # call, not from asking the model to self-report.
    appointment = (
        await db.execute(
            select(Appointment)
            .join(Customer, Appointment.customer_id == Customer.id)
            .where(Appointment.business_id == business.id, Customer.phone == caller)
            .order_by(Appointment.updated_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    if appointment and call_row.started_at and appointment.updated_at >= call_row.started_at:
        call_row.customer_id = appointment.customer_id
        if appointment.status == AppointmentStatus.CANCELLED:
            call_row.outcome = CallOutcome.CANCELLED
        elif appointment.status == AppointmentStatus.RESCHEDULED:
            call_row.outcome = CallOutcome.RESCHEDULED
        else:
            call_row.outcome = CallOutcome.BOOKED
        if appointment.call_id is None:
            appointment.call_id = call_row.id
    elif call_row.transcript.strip():
        call_row.outcome = CallOutcome.ENQUIRY
    else:
        call_row.outcome = CallOutcome.NO_DETAILS

    await db.commit()
    logger.info(
        "Stored call %s for business %s (outcome=%s)", vapi_call_id, business.id, call_row.outcome
    )
    return {"received": True}
