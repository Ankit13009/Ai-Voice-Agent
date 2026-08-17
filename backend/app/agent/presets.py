"""Business type presets.

This is the file that turns a business-specific product into a platform. A preset
is a *starting point*, not a constraint: onboarding copies its values onto the
tenant's own row, and everything downstream reads the tenant row. So a preset
can be tweaked per client, and a business type nobody wrote a preset for is
still just a form away.

What a preset carries:

* **Labels** — what this trade calls its customers, its staff, and a booking.
  These drive the dashboard's wording and the agent's own vocabulary.
* **Persona** — the one-line description of what the agent is.
* **Intake fields** — what the agent must collect before it may book.
* **Rules** — the constraints the agent must not break. This is where the
  domain's liability lives (a business must not give medical advice; a law firm
  must not give legal advice; a salon must not quote prices it cannot know).
* **Escalation** — what to do when the caller describes something urgent.

Adding a new trade means adding one entry here. It requires no other code change.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class IntakeField:
    """One thing the agent must collect on the call.

    `key` matches the argument name on the `book_appointment` tool, so adding a
    field here makes it collectable without touching the tool schema.
    """

    key: str
    label: str
    required: bool = True
    # Told to the model verbatim, so it phrases the question naturally.
    guidance: str = ""


@dataclass(frozen=True)
class BusinessTypePreset:
    slug: str
    display_name: str

    # --- Vocabulary ---
    customer_singular: str
    customer_plural: str
    staff_singular: str
    staff_plural: str
    booking_singular: str = "appointment"
    booking_plural: str = "appointments"

    # --- Agent identity ---
    default_agent_name: str = "Asha"
    # Completes "You are {agent}, the {persona_role} for {business}."
    persona_role: str = "phone receptionist"
    # Completes "…for {business}, {business_descriptor}."
    business_descriptor: str = "a local business"

    # --- Behaviour ---
    intake_fields: tuple[IntakeField, ...] = ()
    # Constraints. Rendered as a bulleted block in the system prompt.
    rules: tuple[str, ...] = ()
    # What to do when the caller describes something urgent. Empty means the
    # trade has no meaningful emergency path.
    escalation: str = ""

    # --- Defaults for the onboarding form ---
    example_services: tuple[str, ...] = ()
    default_slot_minutes: int = 30

    def label_map(self) -> dict[str, str]:
        return {
            "customer_singular": self.customer_singular,
            "customer_plural": self.customer_plural,
            "staff_singular": self.staff_singular,
            "staff_plural": self.staff_plural,
            "booking_singular": self.booking_singular,
            "booking_plural": self.booking_plural,
        }

    def intake_payload(self) -> list[dict]:
        return [
            {
                "key": item.key,
                "label": item.label,
                "required": item.required,
                "guidance": item.guidance,
            }
            for item in self.intake_fields
        ]


# --------------------------------------------------------------------------- #
# Shared intake fields. Most trades want the same core three.
# --------------------------------------------------------------------------- #
NAME = IntakeField("customer_name", "Full name", True, "Ask for their name early and use it.")
PHONE = IntakeField(
    "customer_phone",
    "Phone number",
    # Not required of the agent: the phone system supplies the caller's number
    # and the booking tool falls back to it. Asking anyway is the single most
    # error-prone step in the call, so the agent only asks when the tool says
    # the number is genuinely missing.
    False,
    "Do not ask for this. The phone system already provides it. Only ask if a "
    "tool tells you the number is missing, or if they want to be called back on "
    "a different number.",
)
# Optional on purpose. The business likes knowing why someone is coming, but a
# caller who will not say should still get their appointment: refusing to book
# over a missing field loses a customer to keep a record tidy.
REASON = IntakeField(
    "service_reason",
    "Reason for the visit",
    False,
    "Ask once, briefly. Anything they say is enough, including nothing.",
)
ADDRESS = IntakeField(
    "address", "Service address", True, "Get the full address, or at least the area."
)
STAFF_PREFERENCE = IntakeField(
    "staff_preference",
    "Preferred {staff_singular}",
    False,
    "Only ask if they have not already named someone.",
)


PRESETS: dict[str, BusinessTypePreset] = {
    # ----------------------------------------------------------------------- #
    "clinic": BusinessTypePreset(
        slug="clinic",
        display_name="Medical clinic",
        customer_singular="Patient",
        customer_plural="Patients",
        staff_singular="Doctor",
        staff_plural="Doctors",
        default_agent_name="Asha",
        business_descriptor="a medical clinic",
        intake_fields=(NAME, PHONE, REASON, STAFF_PREFERENCE),
        rules=(
            "You are a receptionist, not a clinician. Never diagnose, never suggest "
            "treatment, never comment on medicines or test results, and never say how "
            "serious a symptom sounds.",
            "Do not ask for detailed symptoms. A short reason for the visit is enough.",
            "If asked anything clinical, say the doctor will discuss it during the "
            "visit, and return to booking.",
            "Never invent prices, test results, or a doctor's opinion.",
        ),
        escalation=(
            "If the caller describes a medical emergency (chest pain, difficulty "
            "breathing, heavy bleeding, unconsciousness, a serious accident), stop "
            "trying to book. Tell them clearly to hang up and go to the nearest "
            "emergency room or call emergency services immediately, then end the call."
        ),
        example_services=("General consultation", "Follow-up", "Health check-up"),
        default_slot_minutes=15,
    ),
    # ----------------------------------------------------------------------- #
    "dental": BusinessTypePreset(
        slug="dental",
        display_name="Dental practice",
        customer_singular="Patient",
        customer_plural="Patients",
        staff_singular="Dentist",
        staff_plural="Dentists",
        default_agent_name="Asha",
        business_descriptor="a dental practice",
        intake_fields=(NAME, PHONE, REASON, STAFF_PREFERENCE),
        rules=(
            "You are a receptionist, not a dentist. Never diagnose and never advise on "
            "treatment or pain medication.",
            "Never quote a price for a procedure. Say the dentist will confirm the cost "
            "at the visit.",
            "If they ask whether something is covered by insurance, say the front desk "
            "will confirm.",
        ),
        escalation=(
            "If the caller describes severe facial swelling, uncontrolled bleeding, or a "
            "knocked-out tooth, treat it as urgent: offer the earliest possible slot and "
            "tell them to come in straight away."
        ),
        example_services=("Check-up and cleaning", "Filling", "Root canal consultation"),
        default_slot_minutes=30,
    ),
    # ----------------------------------------------------------------------- #
    "salon": BusinessTypePreset(
        slug="salon",
        display_name="Salon or spa",
        customer_singular="Client",
        customer_plural="Clients",
        staff_singular="Stylist",
        staff_plural="Stylists",
        default_agent_name="Riya",
        business_descriptor="a salon and spa",
        intake_fields=(NAME, PHONE, REASON, STAFF_PREFERENCE),
        rules=(
            "Ask which service they want, since the length of the booking depends on it.",
            "Never quote a price unless it is listed in the business facts above.",
            "If they ask for a stylist who is not free, offer the next opening with that "
            "stylist as well as an earlier slot with someone else, and let them choose.",
        ),
        example_services=("Haircut", "Hair colour", "Facial", "Manicure"),
        default_slot_minutes=45,
    ),
    # ----------------------------------------------------------------------- #
    "gym": BusinessTypePreset(
        slug="gym",
        display_name="Gym or fitness studio",
        customer_singular="Member",
        customer_plural="Members",
        staff_singular="Trainer",
        staff_plural="Trainers",
        default_agent_name="Aditi",
        business_descriptor="a gym and fitness studio",
        intake_fields=(NAME, PHONE, REASON, STAFF_PREFERENCE),
        rules=(
            "You are a front desk assistant, not a trainer. Never give training or "
            "nutrition advice.",
            "Never quote membership prices or promise a discount unless it is listed in "
            "the business facts above.",
            "If they ask about an injury, say the trainer will assess it in the session.",
        ),
        example_services=("Trial session", "Personal training", "Group class"),
        default_slot_minutes=60,
    ),
    # ----------------------------------------------------------------------- #
    "law": BusinessTypePreset(
        slug="law",
        display_name="Law firm",
        customer_singular="Client",
        customer_plural="Clients",
        staff_singular="Solicitor",
        staff_plural="Solicitors",
        default_agent_name="Meera",
        business_descriptor="a law firm",
        intake_fields=(NAME, PHONE, REASON),
        rules=(
            "You are a receptionist, not a lawyer. Never give legal advice, never comment "
            "on the strength of a case, and never estimate an outcome.",
            "Take only a short description of the matter. Do not ask for documents, "
            "evidence, or sensitive detail over the phone.",
            "Never quote fees. Say the solicitor will discuss fees at the consultation.",
            "If they mention a court date or a deadline, note it and flag it as urgent.",
        ),
        escalation=(
            "If the caller says they have a hearing, filing deadline, or police matter "
            "within the next two days, offer the earliest available consultation and say "
            "the office will call back today."
        ),
        example_services=("Initial consultation", "Case review"),
        default_slot_minutes=45,
    ),
    # ----------------------------------------------------------------------- #
    "home_services": BusinessTypePreset(
        slug="home_services",
        display_name="Home services (plumbing, electrical, HVAC)",
        customer_singular="Customer",
        customer_plural="Customers",
        staff_singular="Technician",
        staff_plural="Technicians",
        default_agent_name="Ava",
        business_descriptor="a home services business",
        # The address is what makes this trade different: without it nobody can
        # be dispatched, so it is required rather than optional.
        intake_fields=(NAME, PHONE, REASON, ADDRESS),
        rules=(
            "Always get the service address, and check it is inside the service area "
            "listed above before promising a visit.",
            "Never quote a repair price. Say the technician will confirm the cost on site.",
            "Judge urgency: an active leak, flooding, no heat, no water, a burst pipe, or "
            "anything involving a burning smell is an emergency.",
        ),
        escalation=(
            "If the caller reports a gas smell, an electrical fire, or serious flooding, "
            "tell them to shut off the supply if it is safe and to call emergency "
            "services, then take their address for an urgent visit."
        ),
        example_services=("Leak repair", "Blocked drain", "No hot water", "Electrical fault"),
        default_slot_minutes=60,
    ),
    # ----------------------------------------------------------------------- #
    "veterinary": BusinessTypePreset(
        slug="veterinary",
        display_name="Veterinary clinic",
        customer_singular="Pet owner",
        customer_plural="Pet owners",
        staff_singular="Vet",
        staff_plural="Vets",
        default_agent_name="Asha",
        business_descriptor="a veterinary clinic",
        intake_fields=(
            NAME,
            PHONE,
            IntakeField(
                "service_reason",
                "Pet and reason",
                True,
                "Get the animal's name and type along with a short reason.",
            ),
            STAFF_PREFERENCE,
        ),
        rules=(
            "You are a receptionist, not a vet. Never diagnose an animal and never advise "
            "on medication or dosage.",
            "Never quote treatment costs.",
        ),
        escalation=(
            "If the animal is struggling to breathe, bleeding heavily, collapsed, or has "
            "eaten something toxic, tell the owner to bring it in immediately and say the "
            "business will be expecting them."
        ),
        example_services=("Consultation", "Vaccination", "Grooming"),
        default_slot_minutes=20,
    ),
    # ----------------------------------------------------------------------- #
    # The escape hatch: neutral wording, minimal rules, everything editable.
    "general": BusinessTypePreset(
        slug="general",
        display_name="General appointment business",
        customer_singular="Customer",
        customer_plural="Customers",
        staff_singular="Team member",
        staff_plural="Team members",
        default_agent_name="Asha",
        business_descriptor="a local business",
        intake_fields=(NAME, PHONE, REASON),
        rules=(
            "Never quote a price unless it is listed in the business facts above.",
            "Never promise anything the business has not told you it offers.",
        ),
        example_services=("Consultation",),
        default_slot_minutes=30,
    ),
}

DEFAULT_PRESET = "general"


def get_preset(slug: str) -> BusinessTypePreset:
    """Look up a preset, falling back to the neutral one.

    Falls back rather than raising: an unknown slug on an existing tenant row
    (say, after a preset is renamed) should degrade to generic wording, not
    break every call that business receives.
    """
    return PRESETS.get(slug, PRESETS[DEFAULT_PRESET])


def list_presets() -> list[dict]:
    """Preset summaries for the onboarding form's dropdown."""
    return [
        {
            "slug": preset.slug,
            "display_name": preset.display_name,
            "default_agent_name": preset.default_agent_name,
            "business_descriptor": preset.business_descriptor,
            "labels": preset.label_map(),
            "intake_fields": preset.intake_payload(),
            "rules": list(preset.rules),
            "escalation": preset.escalation,
            "example_services": list(preset.example_services),
            "default_slot_minutes": preset.default_slot_minutes,
        }
        for preset in PRESETS.values()
    ]
