"""Meta WhatsApp webhooks: delivery receipts and inbound patient replies.

Verification uses `X-Hub-Signature-256`, an HMAC over the raw request body with
the app secret. The raw bytes are read via `request.body()` before any parsing,
because re-serializing the parsed JSON changes whitespace and key ordering and
the HMAC would never match.

Inbound replies matter for cost as well as UX: a patient message opens a
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
from app.db.models import (
    Appointment,
    Clinic,
    MessageStatus,
    Patient,
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
    if hub_mode == "subscribe" and hub_verify_token and hub_verify_token == settings.whatsapp_verify_token:
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
    would delay every other clinic's receipts.
    """
    settings = get_settings()
    raw_body = await request.body()

    if not verify_meta_signature(raw_body, x_hub_signature_256 or "", settings.whatsapp_app_secret):
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
    """Handle inbound patient messages, currently just CANCEL."""
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

        clinic = await _resolve_clinic_for_patient(db, business_phone_id, normalized)
        if clinic is None:
            logger.warning("Inbound cancel from %s but no clinic matched.", normalized)
            continue

        appointment = await appointment_service.find_patient_appointment(
            db, clinic.id, normalized
        )
        if appointment is None:
            logger.info("Cancel request from %s but no upcoming appointment.", normalized)
            continue

        await appointment_service.cancel_appointment(
            db,
            clinic,
            appointment,
            reason="Cancelled by patient over WhatsApp",
            notify_patient=True,
        )
        logger.info(
            "Cancelled appointment %s for clinic %s via WhatsApp reply.",
            appointment.id,
            clinic.id,
        )


async def _resolve_clinic_for_patient(db, business_phone_id: str, patient_phone: str) -> Clinic | None:
    """Work out which clinic an inbound message belongs to.

    The per-clinic WhatsApp number is the reliable signal. When clinics share the
    platform number, fall back to the patient's most recent appointment, and
    refuse to guess if that is ambiguous: cancelling the wrong clinic's
    appointment is far worse than doing nothing.
    """
    if business_phone_id:
        clinic = (
            await db.execute(
                select(Clinic).where(Clinic.whatsapp_phone_number_id == business_phone_id)
            )
        ).scalar_one_or_none()
        if clinic:
            return clinic

    rows = (
        await db.execute(
            select(Clinic)
            .join(Patient, Patient.clinic_id == Clinic.id)
            .join(Appointment, Appointment.patient_id == Patient.id)
            .where(Patient.phone == patient_phone)
            .order_by(Appointment.starts_at.desc())
            .limit(2)
        )
    ).scalars().all()

    if len(rows) == 1:
        return rows[0]
    if len(rows) > 1:
        logger.warning(
            "Ambiguous inbound WhatsApp cancel from %s: matches multiple clinics.", patient_phone
        )
    return None
