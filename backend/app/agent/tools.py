"""Tool (function-call) definitions exposed to the VAPI assistant.

VAPI uses OpenAI-style function schemas. Each tool points at our
`/webhooks/vapi/tool` endpoint; VAPI calls it mid-conversation, waits for the
JSON result, and the model speaks a reply based on it.

Two design rules:

1. No tool accepts a business id. The business is resolved server-side from the
   assistant/phone number on the call, so a prompt-injected caller ("book me
   into business xyz") cannot reach another tenant.

2. Times are ISO-8601 *with* an offset. Handing the model a naive local time is
   how appointments end up 5.5 hours out, so the description is explicit and the
   server rejects naive values.
"""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.db.models import Business

from app.config import get_settings


def _server_url() -> str:
    settings = get_settings()
    return f"{settings.public_base_url.rstrip('/')}/webhooks/vapi/tool"


CHECK_AVAILABILITY = {
    "type": "function",
    "function": {
        "name": "check_availability",
        "description": (
            "Real open slots. Call before offering any time. Never invent times."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "date": {
                    "type": "string",
                    "description": (
                        "YYYY-MM-DD in the business local date, worked out from the current date in your instructions. Empty = search from now."
                    ),
                },
                "preferred_time_of_day": {
                    "type": "string",
                    "enum": ["morning", "afternoon", "evening", "any"],
                    "description": "Rough preference the caller expressed, if any.",
                },
                "staff_member_id": {
                    "type": "string",
                    "description": (
                        "{staff_singular} id from your instructions. Empty if none named."
                    ),
                },
            },
            "required": [],
        },
    },
}

BOOK_APPOINTMENT = {
    "type": "function",
    "function": {
        "name": "book_appointment",
        "description": (
            "Book a slot check_availability returned. Requires a chosen time and a name. Do not say it is booked until this succeeds."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "customer_name": {"type": "string", "description": "The {customer_singular}'s full name."},
                "customer_phone": {
                    "type": "string",
                    "description": (
                        "International format, e.g. +919876543210. Empty = use the number they are calling from."
                    ),
                },
                "starts_at": {
                    "type": "string",
                    "description": (
                        "Copy verbatim from check_availability starts_at. Never reformat or compute it."
                    ),
                },
                "reason": {
                    "type": "string",
                    "description": "Short reason, e.g. fever and cough, follow-up.",
                },
                "staff_member_id": {"type": "string", "description": "The {staff_singular} id, if one was chosen."},
            },
            "required": ["customer_name", "starts_at"],
        },
    },
}

FIND_APPOINTMENT = {
    "type": "function",
    "function": {
        "name": "find_appointment",
        "description": (
            "Find the caller existing appointment. Call first for any reschedule or cancellation."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "customer_phone": {
                    "type": "string",
                    "description": (
                        "Only if booked under a different number. Empty = calling number."
                    ),
                }
            },
            "required": [],
        },
    },
}

RESCHEDULE_APPOINTMENT = {
    "type": "function",
    "function": {
        "name": "reschedule_appointment",
        "description": (
            "Move an appointment to a slot check_availability returned. Call find_appointment first."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "appointment_id": {
                    "type": "string",
                    "description": "The id returned by find_appointment.",
                },
                "starts_at": {
                    "type": "string",
                    "description": (
                        "Copy verbatim from check_availability starts_at."
                    ),
                },
                "reason": {"type": "string", "description": "Why they are moving it, if given."},
            },
            "required": ["appointment_id", "starts_at"],
        },
    },
}

CANCEL_APPOINTMENT = {
    "type": "function",
    "function": {
        "name": "cancel_appointment",
        "description": (
            "Cancel an appointment. Call find_appointment first and confirm with the caller."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "appointment_id": {
                    "type": "string",
                    "description": "The id returned by find_appointment.",
                },
                "reason": {"type": "string", "description": "Why they are cancelling, if given."},
            },
            "required": ["appointment_id"],
        },
    },
}

LOOKUP_CALLER = {
    "type": "function",
    "function": {
        "name": "lookup_caller",
        "description": (
            "Who is calling. Call ONCE right after your greeting, before asking anything. If it returns a name, use it and never ask for their name."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}

JOIN_WAITLIST = {
    "type": "function",
    "function": {
        "name": "join_waitlist",
        "description": (
            "Waiting list when nothing suitable is free. Offer instead of ending empty-handed."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "customer_name": {"type": "string", "description": "The {customer_singular}'s name."},
                "date_from": {
                    "type": "string",
                    "description": "Earliest acceptable date, YYYY-MM-DD.",
                },
                "date_to": {
                    "type": "string",
                    "description": "Latest acceptable date, YYYY-MM-DD. Same as date_from for a single day.",
                },
                "reason": {"type": "string", "description": "Short reason for the visit."},
            },
            "required": ["customer_name", "date_from", "date_to"],
        },
    },
}

ALL_TOOLS = [
    LOOKUP_CALLER,
    CHECK_AVAILABILITY,
    BOOK_APPOINTMENT,
    FIND_APPOINTMENT,
    RESCHEDULE_APPOINTMENT,
    CANCEL_APPOINTMENT,
    JOIN_WAITLIST,
]


def _transfer_message(business: "Business") -> str:
    """What the caller hears as the call is handed to a person."""
    from app.db.models import Language

    if business.primary_language == Language.ENGLISH:
        return "Let me put you through to someone now, one moment."
    if business.primary_language == Language.HINDI:
        return "मैं आपको अभी किसी से जोड़ती हूँ, एक मिनट।"
    return "Main aapko abhi connect kar rahi hoon, ek moment."


def _fill_labels(value: Any, labels: dict[str, str]) -> Any:
    """Recursively substitute {customer_singular} / {staff_singular} in descriptions.

    Only `str.format_map` on a defaulting dict is used, so a placeholder we
    forgot to supply renders as itself rather than raising mid-onboarding.
    """
    if isinstance(value, str):
        try:
            return value.format_map(_Defaulting(labels))
        except (ValueError, IndexError):
            # A literal brace in a description (JSON examples) is not a placeholder.
            return value
    if isinstance(value, dict):
        return {k: _fill_labels(v, labels) for k, v in value.items()}
    if isinstance(value, list):
        return [_fill_labels(v, labels) for v in value]
    return value


class _Defaulting(dict):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def build_vapi_tools(
    business: "Business | None" = None,
    staff_members: "list | None" = None,
) -> list[dict[str, Any]]:
    """Attach our webhook URL to every tool, in VAPI's expected shape.

    Descriptions are rendered in the tenant's own vocabulary, so a salon's agent
    is told to collect "the Client's full name" and a clinic's "the Patient's
    full name" from one shared tool definition.
    """
    labels = (
        {
            "customer_singular": business.label("customer_singular").lower(),
            "staff_singular": business.label("staff_singular").lower(),
            "booking_singular": business.label("booking_singular").lower(),
        }
        if business is not None
        else {}
    )

    url = _server_url()
    tools: list[dict[str, Any]] = [
        {**_fill_labels(tool, labels), "server": {"url": url}} for tool in ALL_TOOLS
    ]

    # A business with no individual staff has nothing to choose between, and a
    # parameter is an affordance: leaving staff_member_id on the schema invited
    # the agent to ask which doctor the caller wanted at a clinic that has none.
    # Removing it is stronger than instructing against it, and costs fewer
    # tokens on every turn.
    if business is not None and not [s for s in (staff_members or []) if s.is_active]:
        for tool in tools:
            properties = (
                tool.get("function", {}).get("parameters", {}).get("properties", {})
            )
            properties.pop("staff_member_id", None)

    # Handing off to a human is VAPI's own transferCall tool rather than one of
    # ours: the call has to be physically moved, which a webhook response cannot
    # do. Only added when the business has somewhere to transfer to, so the agent
    # is never told it can do something that would then fail.
    if business is not None and business.handoff_enabled:
        destination = business.handoff_phone or business.contact_phone
        if destination:
            tools.append(
                {
                    "type": "transferCall",
                    "destinations": [
                        {
                            "type": "number",
                            "number": destination,
                            # Spoken to the caller as the transfer begins, so it
                            # has to be in the language the business runs in. It
                            # was hardcoded Hinglish, which an English-only law
                            # firm or a Hindi-only clinic would both have heard
                            # wrongly.
                            "message": _transfer_message(business),
                        }
                    ],
                }
            )

    return tools
