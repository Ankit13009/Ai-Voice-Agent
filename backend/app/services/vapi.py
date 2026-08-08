"""VAPI client: create and update the per-clinic assistant from one master template.

This is the "master template, one click to add a clinic" mechanism. There is a
single assistant *shape* defined in `build_assistant_payload`; onboarding a
clinic renders that shape with the clinic's own prompt, greeting, and voice, and
creates a VAPI assistant from it. Nothing about a new clinic requires touching
code or the VAPI dashboard.

Provider choices, and why:

* Transcriber: Deepgram nova-2 with `language: multi`, which is Deepgram's
  code-switching model. Hindi-English switching mid-sentence is the normal case
  for Indian clinic callers, and a single-language model transcribes the other
  half as noise.
* Voice: Azure `hi-IN-SwaraNeural` speaks both Hindi and English acceptably in
  one voice, so the agent does not change voice when the caller switches.
* Model: Claude, for instruction-following on the medical-boundary rules. Haiku
  keeps phone latency low; the model id is overridable per clinic.
"""

import logging
from typing import Any

import httpx

from app.config import get_settings
from app.core.errors import IntegrationNotConfiguredError, UpstreamError
from app.agent.prompts import build_greeting, build_system_prompt
from app.agent.tools import build_vapi_tools
from app.db.models import Clinic, Doctor, Language

logger = logging.getLogger(__name__)

VAPI_BASE = "https://api.vapi.ai"
HTTP_TIMEOUT = httpx.Timeout(20.0, connect=5.0)

# Low-latency default. Phone calls punish slow first tokens far more than they
# reward marginally better phrasing.
DEFAULT_MODEL = "claude-haiku-4-5"

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


def build_assistant_payload(clinic: Clinic, doctors: list[Doctor]) -> dict[str, Any]:
    """The master template, rendered for one clinic."""
    settings = get_settings()
    voice = VOICE_BY_LANGUAGE.get(clinic.primary_language, VOICE_BY_LANGUAGE[Language.MIXED])

    return {
        "name": f"{clinic.name} receptionist ({clinic.slug})",
        "firstMessage": build_greeting(clinic),
        # Speak first: a silent pickup makes callers think the line is dead.
        "firstMessageMode": "assistant-speaks-first",
        "model": {
            "provider": "anthropic",
            "model": DEFAULT_MODEL,
            "temperature": 0.4,  # low: this agent follows rules, it does not riff
            "maxTokens": 300,  # phone replies are one or two sentences
            "messages": [
                {"role": "system", "content": build_system_prompt(clinic, doctors)}
            ],
            "tools": build_vapi_tools(),
        },
        "transcriber": {
            "provider": "deepgram",
            "model": "nova-2",
            "language": "multi",  # Hindi/English code-switching
        },
        "voice": {**voice, "speed": 1.0},
        "server": {
            "url": f"{settings.public_base_url.rstrip('/')}/webhooks/vapi/events",
            "secret": settings.vapi_webhook_secret,
        },
        "serverMessages": ["end-of-call-report", "status-update", "tool-calls"],
        # Callers pause mid-sentence when reading out a phone number or checking
        # a calendar. A short endpointing window cuts them off.
        "startSpeakingPlan": {"waitSeconds": 0.6},
        "silenceTimeoutSeconds": 30,
        "maxDurationSeconds": 900,
        "endCallMessage": "Thank you for calling. Take care.",
        "backgroundDenoisingEnabled": True,
        "recordingEnabled": True,
        "metadata": {"clinic_id": clinic.id, "clinic_slug": clinic.slug},
    }


async def _request(method: str, path: str, json_body: dict | None = None) -> dict:
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        response = await client.request(
            method, f"{VAPI_BASE}{path}", headers=_headers(), json=json_body
        )
    if response.status_code in (200, 201, 204):
        return response.json() if response.content else {}
    logger.error("VAPI %s %s failed %s: %s", method, path, response.status_code, response.text[:400])
    raise UpstreamError("VAPI", log_context={"status": response.status_code, "path": path})


async def create_assistant(clinic: Clinic, doctors: list[Doctor]) -> str:
    """Create the clinic's assistant. Returns the VAPI assistant id."""
    data = await _request("POST", "/assistant", build_assistant_payload(clinic, doctors))
    assistant_id = data.get("id", "")
    logger.info("Created VAPI assistant %s for clinic %s", assistant_id, clinic.id)
    return assistant_id


async def update_assistant(clinic: Clinic, doctors: list[Doctor]) -> None:
    """Re-push the assistant after a settings change.

    Called whenever clinic hours, greeting, language, or doctors change: the
    prompt embeds those facts, so a stale assistant would quote last week's
    timings to callers.
    """
    if not clinic.vapi_assistant_id:
        logger.info("Clinic %s has no VAPI assistant to update.", clinic.id)
        return
    await _request(
        "PATCH",
        f"/assistant/{clinic.vapi_assistant_id}",
        build_assistant_payload(clinic, doctors),
    )
    logger.info("Updated VAPI assistant %s for clinic %s", clinic.vapi_assistant_id, clinic.id)


async def attach_phone_number(phone_number_id: str, assistant_id: str) -> None:
    """Route an inbound VAPI number to this clinic's assistant."""
    await _request("PATCH", f"/phone-number/{phone_number_id}", {"assistantId": assistant_id})
    logger.info("Attached VAPI number %s to assistant %s", phone_number_id, assistant_id)


async def delete_assistant(assistant_id: str) -> None:
    if not assistant_id:
        return
    try:
        await _request("DELETE", f"/assistant/{assistant_id}")
    except UpstreamError:
        # Cleanup path: a failure here should not block deactivating a clinic.
        logger.warning("Could not delete VAPI assistant %s.", assistant_id)
