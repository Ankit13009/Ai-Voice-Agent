"""Clinic, doctor, and onboarding payloads."""

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


class DoctorOut(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    id: str
    name: str
    specialization: str
    google_calendar_id: str
    consultation_duration_minutes: int
    is_active: bool


class DoctorCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    specialization: str = Field(default="", max_length=255)
    google_calendar_id: str = Field(default="", max_length=255)
    consultation_duration_minutes: int = Field(default=15, ge=5, le=240)
    opens_at: time | None = None
    closes_at: time | None = None
    working_days: list[int] | None = None

    @field_validator("working_days")
    @classmethod
    def _days(cls, v: list[int] | None) -> list[int] | None:
        return _validate_working_days(v) if v is not None else None


class DoctorUpdate(BaseModel):
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

    Credentials themselves are never included: only whether the connection
    works and which account it points at.
    """

    google_calendar_connected: bool
    google_calendar_email: str = ""
    google_calendar_error: str = ""
    vapi_assistant_configured: bool
    whatsapp_configured: bool


class ClinicOut(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    id: str
    name: str
    slug: str
    address: str
    city: str
    contact_phone: str
    contact_email: str
    timezone: str

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
    doctors: list[DoctorOut] = Field(default_factory=list)


class ClinicUpdate(BaseModel):
    """Every field optional: this is a PATCH.

    `phone_number`, `slug`, and `vapi_assistant_id` are absent on purpose.
    Changing the dialed number or the tenant slug re-points inbound call routing
    and has to go through the onboarding flow, not a settings form.
    """

    name: str | None = Field(default=None, min_length=1, max_length=255)
    address: str | None = Field(default=None, max_length=2000)
    city: str | None = Field(default=None, max_length=120)
    contact_phone: str | None = Field(default=None, pattern=PHONE_PATTERN)
    contact_email: EmailStr | None = None
    timezone: str | None = Field(default=None, max_length=64)

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


class OnboardClinicRequest(BaseModel):
    """One request creates a fully working clinic: the "1-click new clinic" flow.

    Everything not supplied falls back to a sensible clinic default, so the
    minimum viable onboarding is name + phone number + owner email.
    """

    name: str = Field(..., min_length=1, max_length=255)
    slug: str = Field(..., min_length=2, max_length=120, pattern=r"^[a-z0-9][a-z0-9-]*$")
    phone_number: str = Field(..., pattern=PHONE_PATTERN, description="The number patients dial.")

    owner_email: EmailStr
    owner_password: str = Field(..., min_length=10, max_length=72)
    owner_name: str = Field(default="", max_length=255)

    address: str = Field(default="", max_length=2000)
    city: str = Field(default="", max_length=120)
    contact_phone: str = Field(default="", max_length=32)
    timezone: str = Field(default="Asia/Kolkata", max_length=64)

    agent_name: str = Field(default="Asha", max_length=120)
    primary_language: Language = Language.MIXED
    greeting_en: str = Field(default="", max_length=1000)
    greeting_hi: str = Field(default="", max_length=1000)

    opens_at: time = time(9, 0)
    closes_at: time = time(18, 0)
    working_days: list[int] = Field(default_factory=lambda: [1, 2, 3, 4, 5, 6])
    slot_duration_minutes: int = Field(default=15, ge=5, le=240)

    doctors: list[DoctorCreate] = Field(default_factory=list)
    # Created verbatim; if empty, a standard consultation/follow-up pair is added.
    appointment_types: list[str] = Field(default_factory=list)

    # Provision a VAPI assistant from the master template as part of onboarding.
    create_vapi_assistant: bool = True

    @field_validator("working_days")
    @classmethod
    def _days(cls, v: list[int]) -> list[int]:
        return _validate_working_days(v)


class OnboardClinicResponse(BaseModel):
    clinic: ClinicOut
    owner_user_id: str
    vapi_assistant_id: str = ""
    # Steps onboarding could not finish automatically, shown as a checklist.
    next_steps: list[str] = Field(default_factory=list)
