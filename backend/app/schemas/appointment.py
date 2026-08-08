"""Appointment, patient, call, and WhatsApp payloads."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.db.models import (
    AppointmentStatus,
    CallOutcome,
    Language,
    MessageKind,
    MessageStatus,
)

PHONE_PATTERN = r"^\+[1-9]\d{7,14}$"


# --------------------------------------------------------------------------- #
# Patients
# --------------------------------------------------------------------------- #
class PatientOut(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    id: str
    name: str
    phone: str
    email: str
    preferred_language: Language
    notes: str
    created_at: datetime


class PatientSummary(BaseModel):
    """Trimmed patient block embedded in appointment/call responses, so the
    frontend can render a row without a second request."""

    id: str
    name: str
    phone: str


class PatientUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=255)
    email: str | None = Field(default=None, max_length=255)
    preferred_language: Language | None = None
    notes: str | None = Field(default=None, max_length=4000)


# --------------------------------------------------------------------------- #
# Availability
# --------------------------------------------------------------------------- #
class SlotOut(BaseModel):
    """One bookable opening. `starts_at` is UTC; `label` is pre-formatted in the
    clinic's timezone so the agent can speak it and the UI can show it without
    either re-deriving the conversion."""

    starts_at: datetime
    ends_at: datetime
    label: str
    doctor_id: str | None = None
    doctor_name: str = ""


class AvailabilityQuery(BaseModel):
    date_from: datetime
    date_to: datetime
    doctor_id: str | None = None
    duration_minutes: int | None = Field(default=None, ge=5, le=240)

    @model_validator(mode="after")
    def _range(self) -> "AvailabilityQuery":
        if self.date_to <= self.date_from:
            raise ValueError("date_to must be after date_from.")
        if (self.date_to - self.date_from).days > 60:
            raise ValueError("Availability can be queried for at most 60 days at a time.")
        return self


# --------------------------------------------------------------------------- #
# Appointments
# --------------------------------------------------------------------------- #
class AppointmentOut(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    id: str
    status: AppointmentStatus
    starts_at: datetime
    ends_at: datetime
    # Same instant as `starts_at`, rendered in the clinic's timezone for display.
    starts_at_local: str
    reason: str
    notes: str
    cancellation_reason: str

    patient: PatientSummary
    doctor_id: str | None
    doctor_name: str = ""
    call_id: str | None = None

    google_event_id: str
    synced_to_calendar: bool
    rescheduled_from_id: str | None = None
    created_at: datetime


class AppointmentCreate(BaseModel):
    patient_name: str = Field(..., min_length=1, max_length=255)
    patient_phone: str = Field(..., pattern=PHONE_PATTERN)
    starts_at: datetime = Field(..., description="Appointment start, ISO-8601 with offset.")
    doctor_id: str | None = None
    appointment_type_id: str | None = None
    duration_minutes: int | None = Field(default=None, ge=5, le=240)
    reason: str = Field(default="", max_length=2000)
    notes: str = Field(default="", max_length=4000)
    preferred_language: Language = Language.MIXED

    @field_validator("starts_at")
    @classmethod
    def _tz_aware(cls, v: datetime) -> datetime:
        """Reject naive datetimes.

        A naive value is ambiguous, and guessing the offset is how an
        appointment silently lands 5.5 hours from where the caller meant.
        """
        if v.tzinfo is None:
            raise ValueError("starts_at must include a timezone offset (e.g. 2026-08-12T15:00:00+05:30).")
        return v


class AppointmentReschedule(BaseModel):
    starts_at: datetime
    doctor_id: str | None = None
    reason: str = Field(default="", max_length=2000)

    @field_validator("starts_at")
    @classmethod
    def _tz_aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("starts_at must include a timezone offset.")
        return v


class AppointmentCancel(BaseModel):
    reason: str = Field(default="", max_length=2000)
    notify_patient: bool = True


# --------------------------------------------------------------------------- #
# Calls
# --------------------------------------------------------------------------- #
class CallOut(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    id: str
    vapi_call_id: str
    caller_number: str
    started_at: datetime | None
    ended_at: datetime | None
    duration_seconds: int
    language: Language
    outcome: CallOutcome
    summary: str
    recording_url: str
    ended_reason: str
    patient: PatientSummary | None = None
    appointment_id: str | None = None
    created_at: datetime


class CallDetailOut(CallOut):
    """Adds the full transcript. Split from the list response so a 200-row call
    log doesn't ship several megabytes of transcript text."""

    transcript: str
    cost_paise: int


# --------------------------------------------------------------------------- #
# WhatsApp
# --------------------------------------------------------------------------- #
class WhatsAppMessageOut(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    id: str
    appointment_id: str | None
    to_phone: str
    kind: MessageKind
    status: MessageStatus
    template_name: str
    language_code: str
    rendered_preview: str
    scheduled_for: datetime | None
    sent_at: datetime | None
    delivered_at: datetime | None
    attempt_count: int
    last_error: str
    created_at: datetime


# --------------------------------------------------------------------------- #
# Dashboard stats
# --------------------------------------------------------------------------- #
class DashboardStats(BaseModel):
    calls_total: int
    calls_today: int
    appointments_upcoming: int
    appointments_today: int
    booked_by_agent: int
    cancelled: int
    no_details: int
    # Share of calls that ended in a booking, 0-100, rounded to one decimal.
    conversion_rate: float
    whatsapp_sent: int
    whatsapp_failed: int
    avg_call_duration_seconds: int
