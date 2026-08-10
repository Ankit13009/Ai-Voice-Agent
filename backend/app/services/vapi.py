"""VAPI client: create and update the per-business assistant from one master template.

This is the "master template, one click to add a business" mechanism. There is a
single assistant *shape* defined in `build_assistant_payload`; onboarding a
business renders that shape with the business's own prompt, greeting, and voice, and
creates a VAPI assistant from it. Nothing about a new business requires touching
code or the VAPI dashboard.

Provider choices, and why:

* Transcriber: Deepgram nova-3 with `language: multi`, Deepgram's
  code-switching mode (nova-3 only). Hindi-English switching mid-sentence is the normal case
  for Indian business callers, and a single-language model transcribes the other
  half as noise.
* Voice: Azure `hi-IN-SwaraNeural` speaks both Hindi and English acceptably in
  one voice, so the agent does not change voice when the caller switches.
* Model: Claude, for instruction-following on the medical-boundary rules. Haiku
  keeps phone latency low; the model id is overridable per business.
"""

import logging
from typing import Any

import httpx

from app.config import get_settings
from app.core.errors import BadRequestError, IntegrationNotConfiguredError, UpstreamError
from app.agent.prompts import build_greeting, build_system_prompt
from app.agent.tools import build_vapi_tools
from app.db.models import Business, StaffMember, Language

logger = logging.getLogger(__name__)

VAPI_BASE = "https://api.vapi.ai"
HTTP_TIMEOUT = httpx.Timeout(20.0, connect=5.0)

# Low-latency default. Phone calls punish slow first tokens far more than they
# reward marginally better phrasing.
#
# VAPI validates this against its own allow-list and rejects undated aliases,
# so the dated model id is required even though the API accepts the alias.
DEFAULT_MODEL = "claude-haiku-4-5-20251001"

# VAPI rejects assistant names longer than this.
MAX_ASSISTANT_NAME = 40

VOICE_BY_LANGUAGE = {
    Language.HINDI: {"provider": "azure", "voiceId": "hi-IN-SwaraNeural"},
    Language.MIXED: {"provider": "azure", "voiceId": "hi-IN-SwaraNeural"},
    Language.ENGLISH: {"provider": "azure", "voiceId": "en-IN-NeerjaNeural"},
}


def _headers() -> dict[str, str]:
    settings = get_settings()
    if not settings.vapi_api_key:
        raise IntegrationNotConfiguredError("VAPI is not configured on this server.")
    return {
        "Authorization": f"Bearer {settings.vapi_api_key}",
        "Content-Type": "application/json",
    }


def _assistant_name(business: Business) -> str:
    """A name that fits VAPI's 40-character cap and stays unique.

    Prefers something readable in VAPI's dashboard, then falls back to the slug,
    which is unique per tenant. Truncating the business name instead would let
    two similarly-named clients collide onto one name.
    """
    preferred = f"{business.name} ({business.slug})"
    if len(preferred) <= MAX_ASSISTANT_NAME:
        return preferred
    return business.slug[:MAX_ASSISTANT_NAME]


def build_assistant_payload(business: Business, staff_members: list[StaffMember]) -> dict[str, Any]:
    """The master template, rendered for one business."""
    settings = get_settings()
    voice = VOICE_BY_LANGUAGE.get(business.primary_language, VOICE_BY_LANGUAGE[Language.MIXED])

    return {
        "name": _assistant_name(business),
        "firstMessage": build_greeting(business),
        # Speak first: a silent pickup makes callers think the line is dead.
        "firstMessageMode": "assistant-speaks-first",
        "model": {
            "provider": "anthropic",
            "model": DEFAULT_MODEL,
            "temperature": 0.4,  # low: this agent follows rules, it does not riff
            # Measured on a real call: the agent spoke for 28 of 58 seconds, more
            # than it spent waiting. 150 tokens is roughly 12 seconds of Hindi
            # speech, so the ceiling was never the constraint the comment claimed.
            "maxTokens": 80,
            "messages": [
                {"role": "system", "content": build_system_prompt(business, staff_members)}
            ],
            "tools": build_vapi_tools(business, staff_members),
        },
        "transcriber": {
            "provider": "deepgram",
            # nova-3 is required: `language: multi` (code-switching) is a nova-3
            # feature. VAPI does not validate the model/language pair, so
            # nova-2 + multi is accepted at create time and then silently fails
            # at runtime, producing a bot that greets you and never hears a word.
            "model": "nova-3",
            "language": "multi",  # Hindi/English code-switching
            # Phone numbers are the highest-stakes field the agent collects and
            # the one most often mangled: spoken Hindi digits came back as
            # "अठहत्तर बेहतर तेहतर" on the first real call. `numerals` transcribes
            # spoken numbers as digits instead of words.
            "numerals": True,
            "smartFormat": True,
        },
        # Azure's default pace is unhurried for a phone call, where the caller
        # wants an answer rather than a performance. 1.15 is noticeably brisker
        # while still clearly articulated in Hindi; past about 1.25 the digits in
        # a time ("नौ बजकर पैंतालीस") start to blur, which costs a re-ask and
        # therefore more time than it saved.
        "voice": {**voice, "speed": 1.15},
        "server": {
            "url": f"{settings.public_base_url.rstrip('/')}/webhooks/vapi/events",
            "secret": settings.vapi_webhook_secret,
        },
        "serverMessages": ["end-of-call-report", "status-update", "tool-calls"],
        # Callers pause mid-sentence when reading out a phone number or checking
        # a calendar. A short endpointing window cuts them off.
        # Measured on a real booking: the agent took 3.0s on average between the
        # caller finishing and speaking, 15s of a 66s call. Part of that is
        # simply this timer, and part is the model deciding.
        #
        # smartEndpointing uses a model to judge whether a sentence is actually
        # finished, instead of waiting a fixed period after any silence. That is
        # what lets the wait drop without cutting people off mid-sentence, which
        # is the failure that matters: a caller who gets interrupted repeats
        # themselves and the call gets longer, not shorter.
        "startSpeakingPlan": {
            "waitSeconds": 0.4,
            "smartEndpointingEnabled": True,
        },
        # A caller who trails off or is interrupted by background noise should
        # not have the agent talk over them.
        "stopSpeakingPlan": {"numWords": 2, "voiceSeconds": 0.2, "backoffSeconds": 1.0},
        # Both of these are cost ceilings as much as UX settings. Every second of
        # a call is billed across four vendors at once (VAPI, telephony, STT,
        # TTS), so a caller who puts the phone down without hanging up, or a
        # wedged conversation, bills until one of these fires. A booking takes
        # one to two minutes; 15 minutes was ten times more rope than any real
        # call needs and, on a small prepaid balance, a single stuck call could
        # eat most of it.
        "silenceTimeoutSeconds": 20,
        "maxDurationSeconds": 420,
        "endCallMessage": "Thank you for calling. Take care.",
        "backgroundDenoisingEnabled": True,
        "recordingEnabled": True,
        "metadata": {"business_id": business.id, "clinic_slug": business.slug},
    }


class VapiResourceMissing(Exception):
    """The stored VAPI id does not exist in the account the API key points at.

    Recoverable, unlike an outage: the resource can simply be recreated.
    """


async def _request(method: str, path: str, json_body: dict | None = None) -> dict:
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        response = await client.request(
            method, f"{VAPI_BASE}{path}", headers=_headers(), json=json_body
        )
    if response.status_code in (200, 201, 204):
        return response.json() if response.content else {}
    logger.error("VAPI %s %s failed %s: %s", method, path, response.status_code, response.text[:2000])

    # A 400 is our request being wrong, not VAPI being down. Reporting it as an
    # outage sends you to check a status page instead of the field you mistyped.
    # VAPI's validation messages describe the caller's input, so they are safe to
    # surface; nothing here echoes a credential.
    if response.status_code == 400:
        try:
            detail = response.json().get("message")
            if isinstance(detail, list):
                detail = "; ".join(str(d) for d in detail)
        except Exception:  # noqa: BLE001
            detail = None
        raise BadRequestError(
            str(detail) if detail else "VAPI rejected the request.",
            log_context={"path": path},
        )

    # A 404 means the assistant id we stored is not in the account the current
    # API key points at: it was deleted in the VAPI dashboard, or the key was
    # swapped to a different account. Distinguished from a real outage so the
    # caller can recreate rather than failing forever on a dead id.
    if response.status_code == 404:
        raise VapiResourceMissing(path)

    raise UpstreamError("VAPI", log_context={"status": response.status_code, "path": path})


async def create_assistant(business: Business, staff_members: list[StaffMember]) -> str:
    """Create the business's assistant. Returns the VAPI assistant id."""
    data = await _request("POST", "/assistant", build_assistant_payload(business, staff_members))
    assistant_id = data.get("id", "")
    logger.info("Created VAPI assistant %s for business %s", assistant_id, business.id)
    return assistant_id


async def update_assistant(business: Business, staff_members: list[StaffMember]) -> str:
    """Re-push the assistant after a settings change.

    Called whenever business hours, greeting, language, or staff_members change: the
    prompt embeds those facts, so a stale assistant would quote last week's
    timings to callers.

    Returns a new assistant id if the stored one had to be recreated, otherwise
    an empty string. Recreation happens when the id is missing from the account
    the API key points at, which is the normal case after moving a deployment to
    a client's own VAPI account, and also after someone deletes the assistant in
    the dashboard. Without it the business would 404 on every settings save and
    could never get a working assistant back.
    """
    if not business.vapi_assistant_id:
        logger.info("Business %s has no VAPI assistant to update.", business.id)
        return ""

    payload = build_assistant_payload(business, staff_members)
    try:
        await _request("PATCH", f"/assistant/{business.vapi_assistant_id}", payload)
    except VapiResourceMissing:
        logger.warning(
            "VAPI assistant %s is missing for business %s; recreating it.",
            business.vapi_assistant_id,
            business.id,
        )
        return await create_assistant(business, staff_members)

    logger.info("Updated VAPI assistant %s for business %s", business.vapi_assistant_id, business.id)
    return ""


async def attach_phone_number(phone_number_id: str, assistant_id: str) -> None:
    """Route an inbound VAPI number to this business's assistant."""
    await _request("PATCH", f"/phone-number/{phone_number_id}", {"assistantId": assistant_id})
    logger.info("Attached VAPI number %s to assistant %s", phone_number_id, assistant_id)


async def delete_assistant(assistant_id: str) -> None:
    if not assistant_id:
        return
    try:
        await _request("DELETE", f"/assistant/{assistant_id}")
    except UpstreamError:
        # Cleanup path: a failure here should not block deactivating a business.
        logger.warning("Could not delete VAPI assistant %s.", assistant_id)


# --------------------------------------------------------------------------- #
# Outbound calls (testing)
# --------------------------------------------------------------------------- #
async def list_phone_numbers() -> list[dict]:
    """Numbers on the VAPI account, so a test call can pick one automatically."""
    data = await _request("GET", "/phone-number")
    return data if isinstance(data, list) else []


async def start_outbound_call(
    *, assistant_id: str, phone_number_id: str, to_number: str
) -> dict:
    """Ring `to_number` with this business's agent.

    Used to test on a real phone line without owning an inbound Indian number.
    Worth being clear about what this does and does not prove: the audio path,
    narrowband quality and latency are real, but the call originates from a US
    number, so it validates the agent rather than the product a customer would
    actually dial.
    """
    payload = {
        "assistantId": assistant_id,
        "phoneNumberId": phone_number_id,
        "customer": {"number": to_number},
    }
    data = await _request("POST", "/call", payload)
    logger.info("Started outbound test call to %s (id=%s)", to_number, data.get("id"))
    return data
