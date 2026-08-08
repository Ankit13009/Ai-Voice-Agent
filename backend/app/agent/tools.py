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
            "Find real open appointment slots. Always call this before offering "
            "any time to the caller. Returns only slots that are genuinely free "
            "on the business's calendar."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "date": {
                    "type": "string",
                    "description": (
                        "The day the caller asked about, as YYYY-MM-DD in the business's "
                        "local date. Work it out from the current date given in your "
                        "instructions (for example 'kal' or 'tomorrow'). Leave empty to "
                        "search from now onwards."
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
                        "The id of the {staff_singular} the caller asked for, taken from the "
                        "list in your instructions. Leave empty if they did not name one."
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
            "Book an appointment at a slot that check_availability already "
            "returned. Call this only once the caller has chosen a specific time "
            "and you have their name and phone number. Do not tell the caller it "
            "is booked until this returns success."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "customer_name": {"type": "string", "description": "The {customer_singular}'s full name."},
                "customer_phone": {
                    "type": "string",
                    "description": (
                        "Callback number in international format, e.g. +919876543210. "
                        "If the caller says to use the number they are calling from, "
                        "pass an empty string and the business's system will use it."
                    ),
                },
                "starts_at": {
                    "type": "string",
                    "description": (
                        "The chosen slot's exact start time, copied verbatim from the "
                        "'starts_at' value that check_availability returned. Do not "
                        "reformat it or compute it yourself."
                    ),
                },
                "reason": {
                    "type": "string",
                    "description": "A short reason for the visit, e.g. 'fever and cough', 'follow-up'.",
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
            "Look up the caller's existing upcoming appointment, matched on the "
            "number they are calling from. Call this first for any reschedule or "
            "cancellation request."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "customer_phone": {
                    "type": "string",
                    "description": (
                        "Only if the caller says the booking is under a different "
                        "number. Otherwise leave empty to use the calling number."
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
            "Move the caller's existing appointment to a new slot that "
            "check_availability returned. Call find_appointment first."
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
                        "The new slot's start time, copied verbatim from "
                        "check_availability's 'starts_at'."
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
            "Cancel the caller's existing appointment. Call find_appointment "
            "first and confirm with the caller before calling this."
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

ALL_TOOLS = [
    CHECK_AVAILABILITY,
    BOOK_APPOINTMENT,
    FIND_APPOINTMENT,
    RESCHEDULE_APPOINTMENT,
    CANCEL_APPOINTMENT,
]


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


def build_vapi_tools(business: "Business | None" = None) -> list[dict[str, Any]]:
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
    return [{**_fill_labels(tool, labels), "server": {"url": url}} for tool in ALL_TOOLS]
