"""Business, staff, and onboarding payloads."""

from datetime import time

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.db.models import Language

PHONE_PATTERN = r"^\+[1-9]\d{7,14}$"


def _validate_working_days(v: list[int]) -> list[int]:
    if not v:
        raise ValueError("At least one working day is required.")
    if any(d < 1 or d > 7 for d in v):
        raise ValueError("Working days must be ISO weekday numbers (1=Monday .. 7=Sunday).")
    return sorted(set(v))


# --------------------------------------------------------------------------- #
# Business type configuration
# --------------------------------------------------------------------------- #
class BusinessLabels(BaseModel):
    """What this trade calls its customers, staff, and bookings.

    Drives both the dashboard's wording and the agent's spoken vocabulary, so a
    salon's dashboard says "Clients" and its agent says "client" from one field.
    """

    customer_singular: str = Field(default="Customer", max_length=60)
    customer_plural: str = Field(default="Customers", max_length=60)
    staff_singular: str = Field(default="Team member", max_length=60)
    staff_plural: str = Field(default="Team members", max_length=60)
    booking_singular: str = Field(default="appointment", max_length=60)
    booking_plural: str = Field(default="appointments", max_length=60)


class IntakeFieldSchema(BaseModel):
    """One thing the agent must collect before it may book."""

    key: str = Field(..., min_length=1, max_length=60)
    label: str = Field(..., min_length=1, max_length=120)
    required: bool = True
    guidance: str = Field(default="", max_length=500)


class BusinessTypePresetOut(BaseModel):
    """A starting point offered in the onboarding form. Every value is editable."""

    slug: str
    display_name: str
    default_agent_name: str
    business_descriptor: str
    labels: dict[str, str]
    intake_fields: list[IntakeFieldSchema]
    rules: list[str]
    escalation: str
    example_services: list[str]
    default_slot_minutes: int


# --------------------------------------------------------------------------- #
# Staff
# --------------------------------------------------------------------------- #
class StaffMemberOut(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    id: str
    name: str
    specialization: str
    google_calendar_id: str
    consultation_duration_minutes: int
    is_active: bool


class StaffMemberCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    specialization: str = Field(default="", max_length=255)
    google_calendar_id: str = Field(default="", max_length=255)
    consultation_duration_minutes: int = Field(default=30, ge=5, le=240)
    opens_at: time | None = None
    closes_at: time | None = None
    working_days: list[int] | None = None

    @field_validator("working_days")
    @classmethod
    def _days(cls, v: list[int] | None) -> list[int] | None:
        return _validate_working_days(v) if v is not None else None


class StaffMemberUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    specialization: str | None = Field(default=None, max_length=255)
    google_calendar_id: str | None = Field(default=None, max_length=255)
    consultation_duration_minutes: int | None = Field(default=None, ge=5, le=240)
    is_active: bool | None = None


class AppointmentTypeOut(BaseModel):
    id: str
    name: str
    duration_minutes: int
    description: str
    is_active: bool


class IntegrationStatus(BaseModel):
    """What the settings page shows about a connected third party.

    Credentials themselves are never included: only whether the connection works
    and which account it points at.
    """

    google_calendar_connected: bool
    google_calendar_email: str = ""
    google_calendar_error: str = ""
    vapi_assistant_configured: bool
    whatsapp_configured: bool


# --------------------------------------------------------------------------- #
# Business
# --------------------------------------------------------------------------- #
class BusinessOut(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    id: str
    name: str
    slug: str
    address: str
    city: str
    contact_phone: str
    contact_email: str
    timezone: str

    # --- Business type configuration ---
    business_type: str
    business_descriptor: str
    labels: BusinessLabels
    intake_fields: list[IntakeFieldSchema]
    agent_rules: list[str]
    escalation_instructions: str

    agent_name: str
    phone_number: str
    primary_language: Language
    greeting_en: str
    greeting_hi: str
    agent_notes: str

    opens_at: time
    closes_at: time
    working_days: list[int]
    slot_duration_minutes: int

    whatsapp_enabled: bool
    reminder_24h_enabled: bool
    reminder_2h_enabled: bool
    is_active: bool

    integrations: IntegrationStatus | None = None
    staff_members: list[StaffMemberOut] = Field(default_factory=list)


class BusinessUpdate(BaseModel):
    """Every field optional: this is a PATCH.

    `phone_number` and `slug` are absent on purpose. Changing the dialed number
    or the tenant slug re-points inbound call routing and has to go through
    onboarding, not a settings form.

    The business-type fields ARE editable here. That is the point: a preset is a
    starting point, and an owner who needs a rule the preset did not anticipate
    can add it without a deploy.
    """

    name: str | None = Field(default=None, min_length=1, max_length=255)
    address: str | None = Field(default=None, max_length=2000)
    city: str | None = Field(default=None, max_length=120)
    contact_phone: str | None = Field(default=None, pattern=PHONE_PATTERN)
    contact_email: EmailStr | None = None
    timezone: str | None = Field(default=None, max_length=64)

    business_type: str | None = Field(default=None, max_length=64)
    business_descriptor: str | None = Field(default=None, max_length=255)
    labels: BusinessLabels | None = None
    intake_fields: list[IntakeFieldSchema] | None = None
    agent_rules: list[str] | None = Field(default=None, max_length=30)
    escalation_instructions: str | None = Field(default=None, max_length=4000)

    agent_name: str | None = Field(default=None, min_length=1, max_length=120)
    primary_language: Language | None = None
    greeting_en: str | None = Field(default=None, max_length=1000)
    greeting_hi: str | None = Field(default=None, max_length=1000)
    agent_notes: str | None = Field(default=None, max_length=4000)

    opens_at: time | None = None
    closes_at: time | None = None
    working_days: list[int] | None = None
    slot_duration_minutes: int | None = Field(default=None, ge=5, le=240)

    whatsapp_enabled: bool | None = None
    reminder_24h_enabled: bool | None = None
    reminder_2h_enabled: bool | None = None

    @field_validator("working_days")
    @classmethod
    def _days(cls, v: list[int] | None) -> list[int] | None:
        return _validate_working_days(v) if v is not None else None

    @field_validator("timezone")
    @classmethod
    def _tz(cls, v: str | None) -> str | None:
        if v is None:
            return None
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

        try:
            ZoneInfo(v)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"Unknown timezone: {v}") from exc
        return v


# --------------------------------------------------------------------------- #
# Onboarding
# --------------------------------------------------------------------------- #
class OnboardBusinessRequest(BaseModel):
    """One request creates a fully working tenant of any business type.

    `business_type` picks a preset, which seeds the vocabulary, intake fields,
    rules, and escalation path. Every one of those can be overridden here, and
    all of them stay editable afterwards, so an unusual business never requires
    a code change.

    The minimum viable request is name + slug + phone number + business type +
    owner credentials.
    """

    name: str = Field(..., min_length=1, max_length=255)
    slug: str = Field(..., min_length=2, max_length=120, pattern=r"^[a-z0-9][a-z0-9-]*$")
    phone_number: str = Field(
        ..., pattern=PHONE_PATTERN, description="The number customers dial."
    )

    business_type: str = Field(
        default="general",
        max_length=64,
        description="Preset slug. Call GET /onboarding/business-types for the list.",
    )

    owner_email: EmailStr
    owner_password: str = Field(..., min_length=10, max_length=72)
    owner_name: str = Field(default="", max_length=255)

    address: str = Field(default="", max_length=2000)
    city: str = Field(default="", max_length=120)
    contact_phone: str = Field(default="", max_length=32)
    timezone: str = Field(default="Asia/Kolkata", max_length=64)

    # --- Preset overrides. Omit to accept the preset's value. ---
    agent_name: str | None = Field(default=None, max_length=120)
    business_descriptor: str | None = Field(default=None, max_length=255)
    labels: BusinessLabels | None = None
    intake_fields: list[IntakeFieldSchema] | None = None
    agent_rules: list[str] | None = Field(default=None, max_length=30)
    escalation_instructions: str | None = Field(default=None, max_length=4000)

    primary_language: Language = Language.MIXED
    greeting_en: str = Field(default="", max_length=1000)
    greeting_hi: str = Field(default="", max_length=1000)

    opens_at: time = time(9, 0)
    closes_at: time = time(18, 0)
    working_days: list[int] = Field(default_factory=lambda: [1, 2, 3, 4, 5, 6])
    slot_duration_minutes: int | None = Field(default=None, ge=5, le=240)

    staff_members: list[StaffMemberCreate] = Field(default_factory=list)
    # Created verbatim; if empty, the preset's example services are used.
    appointment_types: list[str] = Field(default_factory=list)

    # Provision a VAPI assistant from the master template as part of onboarding.
    create_vapi_assistant: bool = True

    @field_validator("working_days")
    @classmethod
    def _days(cls, v: list[int]) -> list[int]:
        return _validate_working_days(v)


class OnboardBusinessResponse(BaseModel):
    business: BusinessOut
    owner_user_id: str
    vapi_assistant_id: str = ""
    # Steps onboarding could not finish automatically, shown as a checklist.
    next_steps: list[str] = Field(default_factory=list)
