"""The prompt must actually reflect the business's configuration.

The selling point of this product is that a clinic, a salon and a plumber run
the same code and differ only in their rows. That claim is only true if those
rows reach the prompt, and for intake fields they did not: the helper that
rendered them was never called, so a home-services business requiring a service
address never asked for one. Nothing failed; the agent simply booked jobs with
nowhere to send anyone.

These tests exist because that class of bug is invisible at runtime.
"""

import pytest

from app.agent.prompts import build_system_prompt
from app.db.models import Business, Language, StaffMember


def _business(**overrides) -> Business:
    from datetime import time

    defaults = dict(
        id="b1",
        name="Test Business",
        slug="test-business",
        business_type="clinic",
        business_descriptor="a medical clinic",
        agent_name="Asha",
        phone_number="+911111111111",
        timezone="Asia/Kolkata",
        opens_at=time(9, 0),
        closes_at=time(18, 0),
        working_days=[1, 2, 3, 4, 5],
        primary_language=Language.MIXED,
        labels={
            "customer_singular": "Patient",
            "customer_plural": "Patients",
            "staff_singular": "Doctor",
            "staff_plural": "Doctors",
            "booking_singular": "appointment",
            "booking_plural": "appointments",
        },
        intake_fields=[],
        agent_rules=[],
    )
    defaults.update(overrides)
    return Business(**defaults)


def test_configured_intake_fields_appear_in_the_prompt():
    """The bug this file exists for: configuration that never reached the agent."""
    business = _business(
        intake_fields=[
            {"key": "customer_name", "label": "Full name", "required": True, "guidance": ""},
            {
                "key": "service_address",
                "label": "Service address",
                "required": True,
                "guidance": "Get the full address.",
            },
        ]
    )
    prompt = build_system_prompt(business, [])

    assert "Service address" in prompt, "a required field the agent never asked for"
    assert "Get the full address." in prompt


def test_a_field_added_by_an_owner_reaches_the_agent():
    """Settings must be able to change behaviour without a deploy."""
    business = _business(
        intake_fields=[
            {"key": "insurance", "label": "Insurance provider", "required": True, "guidance": ""}
        ]
    )
    assert "Insurance provider" in build_system_prompt(business, [])


def test_staff_preference_is_dropped_when_there_are_no_staff():
    """Asking which doctor at a clinic with none is asking about nothing."""
    business = _business(
        intake_fields=[
            {"key": "staff_preference", "label": "Preferred {staff_singular}", "required": False}
        ]
    )
    prompt = build_system_prompt(business, [])

    assert "Preferred doctor" not in prompt
    assert "never ask which doctor" in prompt.lower()


def test_staff_preference_returns_once_staff_exist():
    """And it must come back on its own, without anyone remembering to re-enable it."""
    business = _business(
        intake_fields=[
            {"key": "staff_preference", "label": "Preferred {staff_singular}", "required": False}
        ]
    )
    doctor = StaffMember(
        id="s1", business_id="b1", name="Dr. Mehta", is_active=True,
        consultation_duration_minutes=15,
    )
    prompt = build_system_prompt(business, [doctor])

    assert "Preferred doctor" in prompt
    assert "Dr. Mehta" in prompt


def test_the_phone_rule_is_stated_exactly_once():
    """Two statements of one rule are two things that can disagree."""
    business = _business(
        intake_fields=[
            {"key": "customer_phone", "label": "Phone number", "required": False,
             "guidance": "Do not ask for this."}
        ]
    )
    prompt = build_system_prompt(business, [])
    # The dedicated section owns this rule; the intake list must not repeat it.
    assert prompt.count("Phone number") <= 1


def test_the_date_is_a_template_not_a_frozen_timestamp():
    """A literal time here is baked in at deploy and wrong by the next day."""
    prompt = build_system_prompt(_business(), [])
    assert '{{"now" | date:' in prompt, "the clock must be rendered per call"
    assert "Asia/Kolkata" in prompt


def test_business_vocabulary_replaces_clinic_wording():
    """A salon's agent must not talk about patients and doctors."""
    salon = _business(
        business_type="salon",
        business_descriptor="a hair salon",
        labels={
            "customer_singular": "Client",
            "customer_plural": "Clients",
            "staff_singular": "Stylist",
            "staff_plural": "Stylists",
            "booking_singular": "booking",
            "booking_plural": "bookings",
        },
    )
    prompt = build_system_prompt(salon, [])
    assert "stylist" in prompt.lower()
    assert "patient" not in prompt.lower()


def test_the_prompt_does_not_contradict_itself_about_combining_questions():
    """The rewrite's reason for existing.

    "Ask ONE thing at a time" sat in the speaking rules while the booking steps
    said to combine questions. Given both, the agent asked name and reason
    separately, which cost about ten seconds of every call and was billed on
    every turn.
    """
    prompt = build_system_prompt(_business(), [])
    assert "COMBINED INTO ONE" in prompt
    assert "ONE thing at a time" not in prompt


def test_the_prompt_stays_small_enough_to_be_cheap():
    """Every token here is re-sent on every turn of every call.

    A prompt that quietly grows is a bill that quietly grows, and slower first
    words, since nothing can be said until all of it has been read.
    """
    prompt = build_system_prompt(_business(), [])
    approx_tokens = len(prompt) // 4
    assert approx_tokens < 1800, (
        f"prompt is ~{approx_tokens} tokens. It is re-read on every turn, so "
        "growth here costs money and latency on every call."
    )


@pytest.mark.parametrize(
    "behaviour",
    [
        "lookup_caller",       # recognise a returning caller
        "THREE times",         # offer three slots, not one
        "join_waitlist",       # never end empty-handed
        "returned success",    # never claim a booking before the tool confirms
        "inventing information",  # never offer an unchecked time
        "Never read its JSON",    # never speak raw tool output
    ],
)
def test_rewriting_the_prompt_kept_each_behaviour(behaviour: str):
    """Shortening must not quietly drop a rule that took a real call to learn."""
    assert behaviour in build_system_prompt(_business(), [])
