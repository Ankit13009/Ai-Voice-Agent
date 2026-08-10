"""Meta WhatsApp webhooks: delivery receipts and inbound customer replies.

Verification uses `X-Hub-Signature-256`, an HMAC over the raw request body with
the app secret. The raw bytes are read via `request.body()` before any parsing,
because re-serializing the parsed JSON changes whitespace and key ordering and
the HMAC would never match.

Inbound replies matter for cost as well as UX: a customer message opens a
24-hour customer service window, during which utility templates to that number
are free.
"""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Header, Query, Request, Response
from sqlalchemy import select

from app.config import get_settings
from app.core.deps import DbSession
from app.core.errors import WebhookSignatureError
from app.core.security import verify_meta_signature
from app.services import platform_config
from app.db.models import (
    Appointment,
    Business,
    MessageStatus,
    Customer,
    WhatsAppMessage,
)
from app.services import appointments as appointment_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks/whatsapp", tags=["webhooks"])

# Words that cancel an appointment, in English, Hindi, and transliterated Hindi.
CANCEL_KEYWORDS = {"cancel", "स्टॉप", "रद्द", "cancel karo", "radd", "cancel kar do"}

_STATUS_MAP = {
    "sent": MessageStatus.SENT,
    "delivered": MessageStatus.DELIVERED,
    "read": MessageStatus.READ,
    "failed": MessageStatus.FAILED,
}


@router.get("")
async def verify_webhook(
    hub_mode: str = Query(default="", alias="hub.mode"),
    hub_challenge: str = Query(default="", alias="hub.challenge"),
    hub_verify_token: str = Query(default="", alias="hub.verify_token"),
) -> Response:
    """Meta's one-time subscription handshake.

    Echoes `hub.challenge` as plain text only when the token matches. Returning
    the challenge unconditionally would let anyone bind their app to this URL.
    """
    settings = get_settings()
    expected_verify = await platform_config.get_value("whatsapp_verify_token")
    if hub_mode == "subscribe" and hub_verify_token and hub_verify_token == expected_verify:
        logger.info("WhatsApp webhook verified.")
        return Response(content=hub_challenge, media_type="text/plain")
    logger.warning("Rejected WhatsApp webhook verification (bad token).")
    return Response(content="Forbidden", status_code=403, media_type="text/plain")


@router.post("")
async def handle_webhook(
    request: Request,
    db: DbSession,
    x_hub_signature_256: str | None = Header(default=None, alias="X-Hub-Signature-256"),
) -> dict:
    """Process delivery receipts and inbound messages.

    Always returns 200 once the signature checks out. Meta retries non-2xx
    responses with backoff, and a retry storm caused by one malformed entry
    would delay every other business's receipts.
    """
    settings = get_settings()
    raw_body = await request.body()

    app_secret = await platform_config.get_value("whatsapp_app_secret")
    if not verify_meta_signature(raw_body, x_hub_signature_256 or "", app_secret):
        logger.warning("Rejected WhatsApp webhook with an invalid signature.")
        raise WebhookSignatureError()

    payload = await request.json()

    for entry in payload.get("entry", []) or []:
        for change in entry.get("changes", []) or []:
            value = change.get("value", {}) or {}
            try:
                await _process_statuses(db, value.get("statuses", []) or [])
                await _process_messages(db, value)
            except Exception:  # noqa: BLE001
                logger.exception("Failed to process a WhatsApp webhook entry; continuing.")

    await db.commit()
    return {"received": True}


async def _process_statuses(db, statuses: list[dict]) -> None:
    """Update our message rows from Meta's delivery receipts."""
    for status in statuses:
        wa_id = status.get("id", "")
        state = status.get("status", "")
        if not wa_id:
            continue

        message = (
            await db.execute(select(WhatsAppMessage).where(WhatsAppMessage.wa_message_id == wa_id))
        ).scalar_one_or_none()
        if message is None:
            continue

        mapped = _STATUS_MAP.get(state)
        if mapped is None:
            continue

        # Never move backwards: an out-of-order "sent" must not undo a "read".
        rank = {
            MessageStatus.SENT: 1,
            MessageStatus.DELIVERED: 2,
            MessageStatus.READ: 3,
        }
        if mapped == MessageStatus.FAILED:
            message.status = MessageStatus.FAILED
            errors = status.get("errors", []) or []
            if errors:
                message.last_error = f"{errors[0].get('code')}: {errors[0].get('title', '')}"
        elif rank.get(mapped, 0) > rank.get(message.status, 0):
            message.status = mapped
            if mapped == MessageStatus.DELIVERED:
                message.delivered_at = datetime.now(timezone.utc)


async def _process_messages(db, value: dict) -> None:
    """Handle inbound customer messages, currently just CANCEL."""
    messages = value.get("messages", []) or []
    if not messages:
        return

    business_phone_id = (value.get("metadata", {}) or {}).get("phone_number_id", "")

    for message in messages:
        if message.get("type") != "text":
            continue

        text = ((message.get("text") or {}).get("body") or "").strip().lower()
        from_phone = message.get("from", "")
        if not from_phone:
            continue
        normalized = from_phone if from_phone.startswith("+") else f"+{from_phone}"

        if not any(keyword in text for keyword in CANCEL_KEYWORDS):
            logger.info("Inbound WhatsApp message from %s (no action matched).", normalized)
            continue

        business = await _resolve_business_for_customer(db, business_phone_id, normalized)
        if business is None:
            logger.warning("Inbound cancel from %s but no business matched.", normalized)
            continue

        appointment = await appointment_service.find_customer_appointment(
            db, business.id, normalized
        )
        if appointment is None:
            logger.info("Cancel request from %s but no upcoming appointment.", normalized)
            continue

        await appointment_service.cancel_appointment(
            db,
            business,
            appointment,
            reason="Cancelled by customer over WhatsApp",
            notify_customer=True,
        )
        logger.info(
            "Cancelled appointment %s for business %s via WhatsApp reply.",
            appointment.id,
            business.id,
        )


async def _resolve_business_for_customer(db, business_phone_id: str, customer_phone: str) -> Business | None:
    """Work out which business an inbound message belongs to.

    The per-business WhatsApp number is the reliable signal. When businesses share the
    platform number, fall back to the customer's most recent appointment, and
    refuse to guess if that is ambiguous: cancelling the wrong business's
    appointment is far worse than doing nothing.
    """
    if business_phone_id:
        business = (
            await db.execute(
                select(Business).where(Business.whatsapp_phone_number_id == business_phone_id)
            )
        ).scalar_one_or_none()
        if business:
            return business

    rows = (
        await db.execute(
            select(Business)
            .join(Customer, Customer.business_id == Business.id)
            .join(Appointment, Appointment.customer_id == Customer.id)
            .where(Customer.phone == customer_phone)
            .order_by(Appointment.starts_at.desc())
            .limit(2)
        )
    ).scalars().all()

    if len(rows) == 1:
        return rows[0]
    if len(rows) > 1:
        logger.warning(
            "Ambiguous inbound WhatsApp cancel from %s: matches multiple businesses.", customer_phone
        )
    return None
