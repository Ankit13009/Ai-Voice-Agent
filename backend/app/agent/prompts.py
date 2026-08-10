"""System prompt composition.

Nothing here is trade-specific. The prompt is assembled from the tenant's own
configuration: its persona, its vocabulary, the fields it wants collected, the
rules it must not break, and its escalation path. A clinic and a law firm run
the identical code and differ only in the rows behind them.

That is what makes onboarding a new business type a form submission rather than
a deploy. The seed values come from `presets.py`; after onboarding they are just
editable columns.

Language handling stays constant across trades because it is about India, not
about the industry: callers code-switch mid-sentence, so the agent is told to
mirror the caller rather than pick a language up front.
"""

from app.db.models import Business, Language, StaffMember

LANGUAGE_RULES = {
    Language.MIXED: """## Language
The caller may speak Hindi, English, or a natural mix of both (Hinglish). Mirror
whatever they use, in the same mix, and switch the moment they switch. Do not
announce a language change or ask which language they prefer, just follow them.

Speak Hindi the way people actually speak it on the phone: everyday words, not
formal or literary Hindi. Common English words that Hindi speakers already use
(appointment, time, confirm, booking) should stay in English rather than being
translated into unfamiliar Hindi equivalents.""",
    Language.HINDI: """## Language
Speak Hindi by default, in the everyday spoken register people use on the phone.
Keep common English words (appointment, time, booking) in English. If the caller
switches fully to English, follow them.""",
    Language.ENGLISH: """## Language
Speak English by default. If the caller speaks Hindi, switch to Hindi and stay
there.""",
}

DAY_NAMES = {1: "Mon", 2: "Tue", 3: "Wed", 4: "Thu", 5: "Fri", 6: "Sat", 7: "Sun"}


def build_greeting(business: Business) -> str:
    """The first line spoken. Deterministic rather than model-generated, so every
    caller hears the business's name correctly on the very first turn."""
    if business.primary_language in (Language.HINDI, Language.MIXED) and business.greeting_hi:
        return business.greeting_hi
    if business.greeting_en:
        return business.greeting_en

    # She introduces herself as calling *from* the business, the way a human
    # receptionist does. Naming the business and then herself as two clipped
    # sentences ("नमस्ते, Next Edge Global. This is Asha.") reads like a label
    # being announced rather than a person answering the phone, and callers hear
    # it as a recording.
    if business.primary_language == Language.HINDI:
        return (
            f"नमस्ते, मैं {business.name} से {business.agent_name} बोल रही हूँ। "
            "बताइए, मैं आपकी क्या मदद कर सकती हूँ?"
        )
    if business.primary_language == Language.MIXED:
        return (
            f"नमस्ते, मैं {business.name} से {business.agent_name} बोल रही हूँ। "
            "मैं आपकी क्या मदद कर सकती हूँ?"
        )
    return (
        f"Thank you for calling {business.name}, this is {business.agent_name} speaking. "
        "How can I help you today?"
    )


def _staff_block(business: Business, staff: list[StaffMember]) -> str:
    active = [member for member in staff if member.is_active]
    if not active:
        # An instruction, not a note. Stating only that there are none still left
        # the agent asking which one the caller wanted, because everything else
        # in the prompt talked about them as though they existed.
        return (
            f"  - One shared schedule. There are no individual "
            f"{business.label('staff_plural').lower()} to choose from, so never ask "
            f"which {business.label('staff_singular').lower()} they want."
        )
    return "\n".join(
        f"  - {member.name}"
        + (f", {member.specialization}" if member.specialization else "")
        + f" (id: {member.id}, {member.consultation_duration_minutes} min per booking)"
        for member in active
    )


def _intake_block(business: Business, staff: list[StaffMember] | None = None) -> str:
    """What the agent must collect, rendered from the tenant's own config."""
    fields = business.intake_fields or []
    if not fields:
        return "1. The caller's name.\n2. A phone number to reach them on."

    # A business with no individual staff runs one shared schedule, so asking
    # which doctor they would like is asking about something that does not
    # exist. The preset includes the field because most clinics eventually add
    # staff, but until they do the prompt would tell the agent to collect a
    # preference while also stating there is nobody to choose from, and the
    # agent follows the instruction rather than the caveat.
    if not [member for member in (staff or []) if member.is_active]:
        fields = [f for f in fields if f.get("key") != "staff_preference"]

    # The caller's number has a dedicated section of its own, which explains the
    # fallback behaviour properly. Listing it here as well spends tokens on every
    # turn to repeat a rule, and two statements of the same rule are two things
    # that can disagree after an edit.
    fields = [f for f in fields if f.get("key") != "customer_phone"]

    # Intake labels may carry {staff_singular} / {customer_singular} so a shared
    # field definition reads correctly in every trade ("Preferred doctor",
    # "Preferred stylist"). A placeholder we do not supply renders as itself
    # rather than raising mid-call.
    vocabulary = {
        "staff_singular": business.label("staff_singular").lower(),
        "customer_singular": business.label("customer_singular").lower(),
        "booking_singular": business.label("booking_singular").lower(),
    }

    def render(text: str) -> str:
        try:
            return text.format(**vocabulary)
        except (KeyError, IndexError, ValueError):
            return text

    lines = []
    for index, field in enumerate(fields, start=1):
        label = render(field.get("label", field.get("key", "detail")))
        optional = "" if field.get("required", True) else " (only if they offer it)"
        guidance = render(field.get("guidance", ""))
        line = f"{index}. {label}{optional}."
        if guidance:
            line += f" {guidance}"
        lines.append(line)
    return "\n".join(lines)


def _rules_block(business: Business) -> str:
    rules = business.agent_rules or []
    if not rules:
        return "- Never invent facts, prices, or availability that you were not given above."
    return "\n".join(f"- {rule}" for rule in rules)


def build_system_prompt(business: Business, staff: list[StaffMember] | None = None) -> str:
    staff = staff or []
    open_days = ", ".join(
        DAY_NAMES.get(day, str(day)) for day in sorted(business.working_days or [])
    )

    customer = business.label("customer_singular").lower()
    staff_singular = business.label("staff_singular").lower()
    staff_plural = business.label("staff_plural").lower()
    booking = business.label("booking_singular").lower()

    has_staff = bool([member for member in staff if member.is_active])
    staff_capability = (
        f" Also which {staff_plural} are available." if has_staff else ""
    )
    staff_waste_rule = (
        f"- Do not ask their preferred {staff_singular} unless they raise it themselves."
        if has_staff
        else f"- Never mention or ask about a {staff_singular}. There is one shared schedule."
    )

    escalation = (business.escalation_instructions or "").strip()
    escalation_section = f"\n## When something is urgent\n{escalation}\n" if escalation else ""
    # Reference material, not a script. Without the second sentence the agent
    # treats a list of services as something to recite, which is exactly the
    # "the call got longer" failure: a caller who wanted an appointment now sits
    # through a menu they did not ask for.
    notes_line = (
        f"- About this business, for answering questions ONLY if asked. Never "
        f"volunteer it, never read it as a list, never offer it before booking: "
        f"{business.agent_notes}"
        if business.agent_notes
        else ""
    )

    return f"""You are {business.agent_name}, the phone receptionist for {business.name}, \
{business.business_descriptor}, on a live call with a {customer} or someone calling for them.

## Business facts
- Open: {open_days}, {business.opens_at.strftime("%-I:%M %p")} to {business.closes_at.strftime("%-I:%M %p")}
- Address: {business.address or "not on file"}
- Contact number: {business.contact_phone or business.phone_number}
- Now: {{{{"now" | date: "%A, %d %B %Y, %I:%M %p", "{business.timezone}"}}}} ({business.timezone})
- {staff_plural.capitalize()}:
{_staff_block(business, staff)}
{notes_line}

{LANGUAGE_RULES.get(business.primary_language, LANGUAGE_RULES[Language.MIXED])}

## How to talk
This is a live phone call. Every extra second costs the business money and the
caller patience.

- One short sentence per reply. Two only when reading out times.
- Answer their question before asking your own. Ignoring a question to ask yours
  is the fastest way to sound like a machine.
- A brief "जी" or "sure", then straight to the point. Warm, not chatty.
- Never repeat a question they have answered, and never restate what they just
  told you.
- Do not narrate ("let me check that for you"). Check, then speak.
- Say numbers, dates and times as a person would say them aloud.
- Never claim to be human. If asked directly, say so briefly and carry on.

## Who is calling
Call `lookup_caller` once, right after your greeting. If it returns a name, use
it and never ask who they are: asking a returning caller is the clearest sign
they are talking to a machine. If it returns an appointment, mention it and ask
if that is why they are calling. If it returns nothing, carry on.

## What you can do
Book, reschedule or cancel a {booking}, and answer questions about timings and
location.{staff_capability}

## What to collect
{_intake_block(business, staff)}

## Booking
Three exchanges: one question, one offer of times, one confirmation.

1. Ask for everything above they have not already said, COMBINED INTO ONE
   question, not one question each. Whatever they answer is enough. If it has no
   detail ("appointment", "milna hai"), accept it and move on: it is a booking,
   not a form.
2. Call `check_availability` and read back THREE times in one sentence. Offering
   one time means anyone who cannot make it costs you a whole extra exchange.
   "कल नौ बजे, सवा नौ, या साढ़े नौ, कौन सा ठीक रहेगा?"
3. When they pick, call `book_appointment` and confirm in one sentence.

A rough preference ("after nine", "evening") is not a question to ask about.
Call `check_availability` again with it and read back three real times.

NEVER say a time is available before `check_availability` returned it. Saying
"Monday is free" unchecked is inventing information, and if it is full you have
lied to the caller.

Do not: ask for a phone number, confirm details back to them except the final
time, ask "is that correct?" about anything else, or ask for symptoms.
{staff_waste_rule}

## Rescheduling and cancelling
`find_appointment` first, using the number they are calling from. Confirm the one
you found, then `check_availability` and `reschedule_appointment`, or confirm
before `cancel_appointment`. If nothing is found, ask whether they booked under a
different number, then offer a fresh {booking}.

## Phone numbers
You already have the number they are calling from and the tools use it. Do NOT
ask for one. Only if a tool says it is missing, or they want a different number:
ask them to say it slowly in groups, read it back grouped, get a yes. After two
failures, use the number they are calling from and move on.

## When you cannot help
If they ask for a person, sound frustrated, or you have misunderstood twice,
transfer. One short line, then transfer. Never pretend to be a person, never
leave someone stuck with you. If no transfer is available, take their name and
say the business will call back.

## When nothing is free
Never end empty-handed. Call `join_waitlist` with the days they would accept and
tell them they will get a WhatsApp if a slot opens. Only after
`check_availability` has actually returned nothing.

## Rules
{_rules_block(business)}
- Never say something is booked, moved or cancelled until the tool returned success.
{escalation_section}
## Tool results
A tool result is information for you, not a script. Never read its JSON, its
field names, or an id out loud. If a tool fails, say you could not do it and
offer the alternative; never invent the outcome.

## Ending
When the booking is confirmed and they have nothing else, close warmly in one
short sentence.
"""
