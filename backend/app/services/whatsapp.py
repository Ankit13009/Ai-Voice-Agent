"""WhatsApp via Meta's Cloud API, used directly instead of a reseller like WATI.

Cost model, which is the reason for going direct: Meta charges per *template*
message, with no platform subscription. Appointment confirmations and reminders
are all "utility" templates (roughly ₹0.115 each in India), and utility
templates sent inside an open 24-hour service window are free. A business running
500 appointments a month pays on the order of ₹175, against a WATI plan starting
around ₹2,500.

The operational cost is that templates must be pre-approved by Meta and their
wording cannot be changed on the fly. Only the placeholder variables vary, which
is why the message bodies live in `TEMPLATES` here and every send resolves to
(template_name, language, ordered variables).

Sends are recorded as `WhatsAppMessage` rows before they leave, so the dashboard
message log and the reminder scheduler read from the same table.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.errors import IntegrationNotConfiguredError
from app.db.models import (
    Appointment,
    Business,
    Language,
    MessageKind,
    MessageStatus,
    Customer,
    WhatsAppMessage,
)

logger = logging.getLogger(__name__)

GRAPH_API_VERSION = "v21.0"
GRAPH_BASE = f"https://graph.facebook.com/{GRAPH_API_VERSION}"
HTTP_TIMEOUT = httpx.Timeout(15.0, connect=5.0)


@dataclass(frozen=True)
class TemplateSpec:
    """A Meta-approved template.

    `body` is never sent to Meta; it exists so the dashboard can show what the
    customer received, and so the wording submitted for approval is version
    controlled next to the code that fills it in.
    """

    name: str
    language_code: str
    body: str
    variable_order: tuple[str, ...]

    def render(self, variables: dict[str, str]) -> str:
        text = self.body
        for index, key in enumerate(self.variable_order, start=1):
            text = text.replace("{{%d}}" % index, variables.get(key, ""))
        return text

    def ordered_values(self, variables: dict[str, str]) -> list[str]:
        return [variables.get(key, "") for key in self.variable_order]


# Submit these to Meta under Business Manager -> WhatsApp Manager -> Templates,
# category "Utility". Approval typically takes a few hours to two days.
TEMPLATES: dict[tuple[MessageKind, str], TemplateSpec] = {
    (MessageKind.CONFIRMATION, "en"): TemplateSpec(
        name="appointment_confirmation_en",
        language_code="en",
        body=(
            "Hello {{1}}, your appointment at {{2}} is confirmed for {{3}}. "
            "Reply CANCEL to cancel or call {{4}} to reschedule."
        ),
        variable_order=("customer_name", "business_name", "appointment_time", "business_phone"),
    ),
    (MessageKind.CONFIRMATION, "hi"): TemplateSpec(
        name="appointment_confirmation_hi",
        language_code="hi",
        body=(
            "नमस्ते {{1}}, {{2}} में आपका अपॉइंटमेंट {{3}} के लिए कन्फर्म हो गया है। "
            "रद्द करने के लिए CANCEL भेजें या {{4}} पर कॉल करें।"
        ),
        variable_order=("customer_name", "business_name", "appointment_time", "business_phone"),
    ),
    (MessageKind.REMINDER_24H, "en"): TemplateSpec(
        name="appointment_reminder_24h_en",
        language_code="en",
        body=(
            "Reminder: {{1}}, you have an appointment at {{2}} tomorrow at {{3}}. "
            "Reply CANCEL if you cannot make it."
        ),
        variable_order=("customer_name", "business_name", "appointment_time"),
    ),
    (MessageKind.REMINDER_24H, "hi"): TemplateSpec(
        name="appointment_reminder_24h_hi",
        language_code="hi",
        body=(
            "याद दिलाने के लिए: {{1}}, कल {{3}} बजे {{2}} में आपका अपॉइंटमेंट है। "
            "अगर नहीं आ सकते तो CANCEL भेजें।"
        ),
        variable_order=("customer_name", "business_name", "appointment_time"),
    ),
    (MessageKind.REMINDER_2H, "en"): TemplateSpec(
        name="appointment_reminder_2h_en",
        language_code="en",
        body="{{1}}, your appointment at {{2}} is in about 2 hours, at {{3}}. See you soon.",
        variable_order=("customer_name", "business_name", "appointment_time"),
    ),
    (MessageKind.REMINDER_2H, "hi"): TemplateSpec(
        name="appointment_reminder_2h_hi",
        language_code="hi",
        body="{{1}}, {{2}} में आपका अपॉइंटमेंट लगभग 2 घंटे में, {{3}} बजे है। जल्द मिलते हैं।",
        variable_order=("customer_name", "business_name", "appointment_time"),
    ),
    (MessageKind.CANCELLATION, "en"): TemplateSpec(
        name="appointment_cancelled_en",
        language_code="en",
        body=(
            "Hello {{1}}, your appointment at {{2}} on {{3}} has been cancelled. "
            "Call {{4}} to book a new time."
        ),
        variable_order=("customer_name", "business_name", "appointment_time", "business_phone"),
    ),
    (MessageKind.CANCELLATION, "hi"): TemplateSpec(
        name="appointment_cancelled_hi",
        language_code="hi",
        body=(
            "नमस्ते {{1}}, {{3}} को {{2}} में आपका अपॉइंटमेंट रद्द कर दिया गया है। "
            "नया समय बुक करने के लिए {{4}} पर कॉल करें।"
        ),
        variable_order=("customer_name", "business_name", "appointment_time", "business_phone"),
    ),
    (MessageKind.RESCHEDULE, "en"): TemplateSpec(
        name="appointment_rescheduled_en",
        language_code="en",
        body="Hello {{1}}, your appointment at {{2}} has been moved to {{3}}. Reply CANCEL if this does not work.",
        variable_order=("customer_name", "business_name", "appointment_time"),
    ),
    (MessageKind.RESCHEDULE, "hi"): TemplateSpec(
        name="appointment_rescheduled_hi",
        language_code="hi",
        body="नमस्ते {{1}}, {{2}} में आपका अपॉइंटमेंट {{3}} पर बदल दिया गया है। अगर यह ठीक नहीं है तो CANCEL भेजें।",
        variable_order=("customer_name", "business_name", "appointment_time"),
    ),
}


def resolve_template(kind: MessageKind, language: Language) -> TemplateSpec:
    """Pick the template for this message kind and language.

    Hindi and mixed-language customers both get the Hindi template: someone who
    conducted the call in Hindi should not receive an English-only reminder.
    """
    code = "hi" if language in (Language.HINDI, Language.MIXED) else "en"
    spec = TEMPLATES.get((kind, code))
    if spec is None:  # pragma: no cover - every kind has both languages
        spec = TEMPLATES[(kind, "en")]
    return spec


def format_appointment_time(starts_at: datetime, tz: str) -> str:
    return starts_at.astimezone(ZoneInfo(tz)).strftime("%d %b %Y, %-I:%M %p")


def normalize_phone(phone: str) -> str:
    """Meta wants digits only, no '+' and no separators."""
    return "".join(ch for ch in phone if ch.isdigit())


# --------------------------------------------------------------------------- #
# Sending
# --------------------------------------------------------------------------- #
async def _post_template(
    *,
    phone_number_id: str,
    access_token: str,
    to_phone: str,
    spec: TemplateSpec,
    values: list[str],
) -> tuple[bool, str, str]:
    """POST one template message. Returns (ok, wa_message_id, error)."""
    url = f"{GRAPH_BASE}/{phone_number_id}/messages"
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": normalize_phone(to_phone),
        "type": "template",
        "template": {
            "name": spec.name,
            "language": {"code": spec.language_code},
            "components": [
                {
                    "type": "body",
                    "parameters": [{"type": "text", "text": v} for v in values],
                }
            ],
        },
    }
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            response = await client.post(
                url,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
    except httpx.HTTPError as exc:
        logger.warning("WhatsApp request error for template %s: %s", spec.name, exc)
        return False, "", f"network_error: {exc}"

    if response.status_code == 200:
        data = response.json()
        messages = data.get("messages", []) or []
        return True, (messages[0].get("id", "") if messages else ""), ""

    # Meta's error body carries the actionable reason (template not approved,
    # number not on WhatsApp, out of quota). Keep it on the row for the log.
    detail = response.text[:400]
    logger.error("WhatsApp send failed %s for %s: %s", response.status_code, spec.name, detail)
    try:
        err = response.json().get("error", {})
        reason = f"{err.get('code', response.status_code)}: {err.get('message', 'Unknown error')}"
    except Exception:  # noqa: BLE001
        reason = f"http_{response.status_code}"
    return False, "", reason


def _credentials_for(business: Business) -> tuple[str, str]:
    settings = get_settings()
    phone_number_id = business.whatsapp_phone_number_id or settings.whatsapp_phone_number_id
    access_token = settings.whatsapp_access_token
    if not (phone_number_id and access_token):
        raise IntegrationNotConfiguredError(
            "WhatsApp is not configured for this business.",
            log_context={"business_id": business.id},
        )
    return phone_number_id, access_token


async def queue_message(
    db: AsyncSession,
    *,
    business: Business,
    customer: Customer,
    appointment: Appointment | None,
    kind: MessageKind,
    scheduled_for: datetime | None,
) -> WhatsAppMessage | None:
    """Create a pending message row. Sending happens later, via the scheduler.

    Returns None when the business has this message kind switched off, so callers
    can treat "disabled" and "queued" uniformly.
    """
    if not business.whatsapp_enabled:
        return None
    if kind == MessageKind.REMINDER_24H and not business.reminder_24h_enabled:
        return None
    if kind == MessageKind.REMINDER_2H and not business.reminder_2h_enabled:
        return None

    spec = resolve_template(kind, customer.preferred_language)
    variables = {
        "customer_name": customer.name or "there",
        "business_name": business.name,
        "appointment_time": (
            format_appointment_time(appointment.starts_at, business.timezone) if appointment else ""
        ),
        "business_phone": business.contact_phone or business.phone_number,
    }

    message = WhatsAppMessage(
        business_id=business.id,
        appointment_id=appointment.id if appointment else None,
        to_phone=customer.phone,
        kind=kind,
        status=MessageStatus.PENDING,
        template_name=spec.name,
        language_code=spec.language_code,
        payload=variables,
        rendered_preview=spec.render(variables),
        scheduled_for=scheduled_for,
    )
    db.add(message)
    await db.flush()
    return message


async def send_message(db: AsyncSession, message: WhatsAppMessage, business: Business) -> bool:
    """Send one queued message and record the outcome on its row."""
    spec = TEMPLATES.get((message.kind, message.language_code))
    if spec is None:
        spec = resolve_template(message.kind, Language.ENGLISH)

    message.attempt_count += 1
    try:
        phone_number_id, access_token = _credentials_for(business)
    except IntegrationNotConfiguredError as exc:
        message.status = MessageStatus.FAILED
        message.last_error = exc.message
        await db.flush()
        return False

    ok, wa_id, error = await _post_template(
        phone_number_id=phone_number_id,
        access_token=access_token,
        to_phone=message.to_phone,
        spec=spec,
        values=spec.ordered_values(message.payload or {}),
    )

    if ok:
        message.status = MessageStatus.SENT
        message.wa_message_id = wa_id
        message.sent_at = datetime.now(timezone.utc)
        message.last_error = ""
    else:
        message.last_error = error
        # Three attempts, then stop. Most failures here are permanent (template
        # rejected, number not on WhatsApp) and retrying just burns quota.
        message.status = (
            MessageStatus.FAILED if message.attempt_count >= 3 else MessageStatus.PENDING
        )

    await db.flush()
    return ok


async def cancel_pending_messages(
    db: AsyncSession, appointment_id: str, *, kinds: list[MessageKind] | None = None
) -> int:
    """Cancel not-yet-sent messages for an appointment.

    Called on cancellation and reschedule. Without it, a customer who cancelled
    still receives "your appointment is in 2 hours", which is the single most
    damaging failure mode of an automated reminder system.
    """
    from sqlalchemy import select, update

    stmt = (
        update(WhatsAppMessage)
        .where(
            WhatsAppMessage.appointment_id == appointment_id,
            WhatsAppMessage.status == MessageStatus.PENDING,
        )
        .values(status=MessageStatus.CANCELLED)
    )
    if kinds:
        stmt = stmt.where(WhatsAppMessage.kind.in_(kinds))

    result = await db.execute(stmt)
    await db.flush()
    count = result.rowcount or 0
    if count:
        logger.info("Cancelled %d pending WhatsApp message(s) for appointment %s", count, appointment_id)
    return count
