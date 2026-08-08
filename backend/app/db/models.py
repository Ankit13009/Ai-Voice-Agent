"""ORM models. Types chosen to behave identically on SQLite (dev) and Postgres.

Tenancy: every table that holds clinic data carries a non-null `clinic_id`, and
the API layer derives that value from the caller's JWT, never from a request
parameter. `TenantMixin` exists to make an un-scoped table obvious in review.

Naming: the caller is a "patient", the appointment is an "appointment". The
older home-services vocabulary (lead / booking / service area) is gone
deliberately, so nothing downstream half-speaks the wrong domain.
"""

import uuid
from datetime import datetime, time, timezone
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    Time,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _uuid() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


# --------------------------------------------------------------------------- #
# Enums. Stored as their string values so a DB dump stays readable and the API
# can pass them straight through to the frontend union types.
# --------------------------------------------------------------------------- #
class UserRole(StrEnum):
    SUPERADMIN = "superadmin"  # our staff: can create clinics, sees all tenants
    OWNER = "owner"  # clinic owner: full access to their own clinic
    STAFF = "staff"  # clinic receptionist: read + manage appointments


class CallOutcome(StrEnum):
    BOOKED = "booked"
    RESCHEDULED = "rescheduled"
    CANCELLED = "cancelled"
    ENQUIRY = "enquiry"  # answered, no appointment action
    NO_DETAILS = "no_details"  # caller hung up before qualifying
    FAILED = "failed"  # pipeline/telephony failure


class Language(StrEnum):
    HINDI = "hi"
    ENGLISH = "en"
    MIXED = "hi-en"


class AppointmentStatus(StrEnum):
    SCHEDULED = "scheduled"
    RESCHEDULED = "rescheduled"
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    NO_SHOW = "no_show"


class MessageKind(StrEnum):
    CONFIRMATION = "confirmation"
    REMINDER_24H = "reminder_24h"
    REMINDER_2H = "reminder_2h"
    CANCELLATION = "cancellation"
    RESCHEDULE = "reschedule"


class MessageStatus(StrEnum):
    PENDING = "pending"  # queued, not yet due
    SENT = "sent"  # accepted by Meta
    DELIVERED = "delivered"  # delivery receipt received
    READ = "read"
    FAILED = "failed"
    CANCELLED = "cancelled"  # appointment changed before the reminder fired


def _enum(enum_cls, **kw):
    """Store enums by value, not by Python member name."""
    return SAEnum(
        enum_cls,
        values_callable=lambda e: [m.value for m in e],
        native_enum=False,
        length=32,
        **kw,
    )


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now, nullable=False
    )


class TenantMixin:
    """Marks a table as clinic-scoped. Presence of this mixin is the review
    signal that every query against the table must filter on `clinic_id`."""

    @property
    def tenant_id(self) -> str:  # pragma: no cover - documentation helper
        return self.clinic_id  # type: ignore[attr-defined]


# --------------------------------------------------------------------------- #
# Tenant root
# --------------------------------------------------------------------------- #
class Clinic(Base, TimestampMixin):
    """One row per clinic. This is the tenant boundary and the agent's config."""

    __tablename__ = "clinics"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(120), unique=True, index=True, nullable=False)

    # --- Contact / location ---
    address: Mapped[str] = mapped_column(Text, default="")
    city: Mapped[str] = mapped_column(String(120), default="")
    contact_phone: Mapped[str] = mapped_column(String(32), default="")
    contact_email: Mapped[str] = mapped_column(String(255), default="")
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Kolkata")

    # --- Voice agent (VAPI) ---
    agent_name: Mapped[str] = mapped_column(String(120), default="Asha")
    # The number patients dial. Unique because inbound calls are routed by it.
    phone_number: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)
    vapi_assistant_id: Mapped[str] = mapped_column(String(120), default="", index=True)
    vapi_phone_number_id: Mapped[str] = mapped_column(String(120), default="")
    primary_language: Mapped[Language] = mapped_column(_enum(Language), default=Language.MIXED)
    greeting_en: Mapped[str] = mapped_column(Text, default="")
    greeting_hi: Mapped[str] = mapped_column(Text, default="")
    # Free-text facts the agent may state: parking, insurance, walk-in policy.
    agent_notes: Mapped[str] = mapped_column(Text, default="")

    # --- Scheduling defaults ---
    opens_at: Mapped[time] = mapped_column(Time, default=time(9, 0))
    closes_at: Mapped[time] = mapped_column(Time, default=time(18, 0))
    # ISO weekday numbers the clinic is open: 1=Mon ... 7=Sun.
    working_days: Mapped[list] = mapped_column(JSON, default=lambda: [1, 2, 3, 4, 5, 6])
    slot_duration_minutes: Mapped[int] = mapped_column(Integer, default=15)

    # --- WhatsApp ---
    whatsapp_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # Per-clinic override; falls back to the platform-wide number when empty.
    whatsapp_phone_number_id: Mapped[str] = mapped_column(String(64), default="")
    reminder_24h_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    reminder_2h_enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)

    users: Mapped[list["User"]] = relationship(back_populates="clinic")
    doctors: Mapped[list["Doctor"]] = relationship(back_populates="clinic")


# --------------------------------------------------------------------------- #
# Auth
# --------------------------------------------------------------------------- #
class User(Base, TimestampMixin):
    __tablename__ = "users"
    __table_args__ = (Index("ix_users_clinic_role", "clinic_id", "role"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(255), default="")
    role: Mapped[UserRole] = mapped_column(_enum(UserRole), default=UserRole.STAFF, nullable=False)

    # Null only for superadmins, who are not bound to a single tenant.
    clinic_id: Mapped[str | None] = mapped_column(
        ForeignKey("clinics.id", ondelete="CASCADE"), nullable=True, index=True
    )

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    clinic: Mapped["Clinic | None"] = relationship(back_populates="users")


class RefreshToken(Base):
    """Server-side refresh token records, so sessions can actually be revoked.

    Only the SHA-256 of the token is stored: a database leak then yields no
    usable sessions.
    """

    __tablename__ = "refresh_tokens"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


# --------------------------------------------------------------------------- #
# Clinic resources
# --------------------------------------------------------------------------- #
class Doctor(Base, TenantMixin, TimestampMixin):
    __tablename__ = "doctors"
    __table_args__ = (Index("ix_doctors_clinic_active", "clinic_id", "is_active"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    clinic_id: Mapped[str] = mapped_column(
        ForeignKey("clinics.id", ondelete="CASCADE"), index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    specialization: Mapped[str] = mapped_column(String(255), default="")
    # The Google Calendar this doctor's appointments read from and write to.
    # Empty means the clinic-level calendar is used.
    google_calendar_id: Mapped[str] = mapped_column(String(255), default="")
    consultation_duration_minutes: Mapped[int] = mapped_column(Integer, default=15)
    opens_at: Mapped[time | None] = mapped_column(Time, nullable=True)
    closes_at: Mapped[time | None] = mapped_column(Time, nullable=True)
    working_days: Mapped[list | None] = mapped_column(JSON, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    clinic: Mapped["Clinic"] = relationship(back_populates="doctors")


class AppointmentType(Base, TenantMixin, TimestampMixin):
    """e.g. "First consultation" (30 min), "Follow-up" (15 min)."""

    __tablename__ = "appointment_types"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    clinic_id: Mapped[str] = mapped_column(
        ForeignKey("clinics.id", ondelete="CASCADE"), index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    duration_minutes: Mapped[int] = mapped_column(Integer, default=15)
    description: Mapped[str] = mapped_column(Text, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Patient(Base, TenantMixin, TimestampMixin):
    __tablename__ = "patients"
    __table_args__ = (
        # A phone number identifies a returning patient within one clinic only.
        UniqueConstraint("clinic_id", "phone", name="uq_patient_clinic_phone"),
        Index("ix_patients_clinic_created", "clinic_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    clinic_id: Mapped[str] = mapped_column(
        ForeignKey("clinics.id", ondelete="CASCADE"), index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), default="")
    phone: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    email: Mapped[str] = mapped_column(String(255), default="")
    preferred_language: Mapped[Language] = mapped_column(_enum(Language), default=Language.MIXED)
    notes: Mapped[str] = mapped_column(Text, default="")

    appointments: Mapped[list["Appointment"]] = relationship(back_populates="patient")


# --------------------------------------------------------------------------- #
# Calls and appointments
# --------------------------------------------------------------------------- #
class Call(Base, TenantMixin, TimestampMixin):
    __tablename__ = "calls"
    __table_args__ = (
        Index("ix_calls_clinic_started", "clinic_id", "started_at"),
        Index("ix_calls_clinic_outcome", "clinic_id", "outcome"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    clinic_id: Mapped[str] = mapped_column(
        ForeignKey("clinics.id", ondelete="CASCADE"), index=True, nullable=False
    )
    # VAPI's call id, used to reconcile the end-of-call webhook with the row the
    # tool-call webhook already created.
    vapi_call_id: Mapped[str] = mapped_column(String(120), default="", index=True)
    patient_id: Mapped[str | None] = mapped_column(
        ForeignKey("patients.id", ondelete="SET NULL"), nullable=True, index=True
    )

    caller_number: Mapped[str] = mapped_column(String(32), default="")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_seconds: Mapped[int] = mapped_column(Integer, default=0)

    language: Mapped[Language] = mapped_column(_enum(Language), default=Language.MIXED)
    transcript: Mapped[str] = mapped_column(Text, default="")
    summary: Mapped[str] = mapped_column(Text, default="")
    recording_url: Mapped[str] = mapped_column(String(1024), default="")
    outcome: Mapped[CallOutcome] = mapped_column(
        _enum(CallOutcome), default=CallOutcome.NO_DETAILS, index=True
    )
    # Cost in the smallest currency unit (paise), to avoid float drift.
    cost_paise: Mapped[int] = mapped_column(Integer, default=0)
    ended_reason: Mapped[str] = mapped_column(String(120), default="")

    patient: Mapped["Patient | None"] = relationship()


class Appointment(Base, TenantMixin, TimestampMixin):
    __tablename__ = "appointments"
    __table_args__ = (
        Index("ix_appointments_clinic_start", "clinic_id", "starts_at"),
        Index("ix_appointments_clinic_status", "clinic_id", "status"),
        Index("ix_appointments_reminder_scan", "status", "starts_at"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    clinic_id: Mapped[str] = mapped_column(
        ForeignKey("clinics.id", ondelete="CASCADE"), index=True, nullable=False
    )
    patient_id: Mapped[str] = mapped_column(
        ForeignKey("patients.id", ondelete="CASCADE"), index=True, nullable=False
    )
    doctor_id: Mapped[str | None] = mapped_column(
        ForeignKey("doctors.id", ondelete="SET NULL"), nullable=True, index=True
    )
    appointment_type_id: Mapped[str | None] = mapped_column(
        ForeignKey("appointment_types.id", ondelete="SET NULL"), nullable=True
    )
    # The call that created it. Null for appointments made from the dashboard.
    call_id: Mapped[str | None] = mapped_column(
        ForeignKey("calls.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # Always stored in UTC. The clinic's timezone is applied at the edges only.
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[AppointmentStatus] = mapped_column(
        _enum(AppointmentStatus), default=AppointmentStatus.SCHEDULED, index=True
    )

    # Google Calendar event id, so we can patch/delete the same event later.
    google_event_id: Mapped[str] = mapped_column(String(255), default="", index=True)
    google_calendar_id: Mapped[str] = mapped_column(String(255), default="")

    reason: Mapped[str] = mapped_column(Text, default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    cancellation_reason: Mapped[str] = mapped_column(Text, default="")
    # Set when this row supersedes an earlier one, so the history stays walkable.
    rescheduled_from_id: Mapped[str | None] = mapped_column(String(32), nullable=True)

    patient: Mapped["Patient"] = relationship(back_populates="appointments")
    doctor: Mapped["Doctor | None"] = relationship()


# --------------------------------------------------------------------------- #
# Integrations
# --------------------------------------------------------------------------- #
class CalendarCredential(Base, TenantMixin, TimestampMixin):
    """Google OAuth credentials for one clinic.

    The refresh token is Fernet-encrypted at rest (`core.security`). It is never
    returned by any API endpoint; the clinic settings response exposes only
    `connected_email` and `is_connected`.
    """

    __tablename__ = "calendar_credentials"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    clinic_id: Mapped[str] = mapped_column(
        ForeignKey("clinics.id", ondelete="CASCADE"), unique=True, index=True, nullable=False
    )
    provider: Mapped[str] = mapped_column(String(32), default="google")
    connected_email: Mapped[str] = mapped_column(String(255), default="")
    calendar_id: Mapped[str] = mapped_column(String(255), default="primary")

    encrypted_refresh_token: Mapped[str] = mapped_column(Text, default="")
    encrypted_access_token: Mapped[str] = mapped_column(Text, default="")
    access_token_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    scopes: Mapped[list] = mapped_column(JSON, default=list)
    is_connected: Mapped[bool] = mapped_column(Boolean, default=False)
    last_error: Mapped[str] = mapped_column(Text, default="")


class WhatsAppMessage(Base, TenantMixin, TimestampMixin):
    """Outbound WhatsApp messages: confirmations and the two reminders.

    Rows are created up front in `pending` state with a `scheduled_for` time, so
    the scheduler is a simple due-query and reminders survive a restart.
    """

    __tablename__ = "whatsapp_messages"
    __table_args__ = (
        # The scheduler's hot path: pending messages that are now due.
        Index("ix_wa_due", "status", "scheduled_for"),
        Index("ix_wa_clinic_created", "clinic_id", "created_at"),
        # One message of each kind per appointment. This is what makes the
        # scheduler idempotent: a double-run hits the constraint instead of
        # double-texting the patient.
        UniqueConstraint("appointment_id", "kind", name="uq_wa_appointment_kind"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    clinic_id: Mapped[str] = mapped_column(
        ForeignKey("clinics.id", ondelete="CASCADE"), index=True, nullable=False
    )
    appointment_id: Mapped[str | None] = mapped_column(
        ForeignKey("appointments.id", ondelete="CASCADE"), nullable=True, index=True
    )
    to_phone: Mapped[str] = mapped_column(String(32), nullable=False)
    kind: Mapped[MessageKind] = mapped_column(_enum(MessageKind), nullable=False)
    status: Mapped[MessageStatus] = mapped_column(
        _enum(MessageStatus), default=MessageStatus.PENDING, index=True
    )

    template_name: Mapped[str] = mapped_column(String(120), default="")
    language_code: Mapped[str] = mapped_column(String(16), default="en")
    # The resolved template variables, kept for the dashboard's message log.
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    rendered_preview: Mapped[str] = mapped_column(Text, default="")

    scheduled_for: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    wa_message_id: Mapped[str] = mapped_column(String(128), default="", index=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str] = mapped_column(Text, default="")


class AuditLog(Base):
    """Append-only record of privileged actions.

    Clinic data is health data. Knowing who cancelled an appointment or exported
    a patient list is a baseline requirement, and it is far cheaper to write from
    day one than to retrofit.
    """

    __tablename__ = "audit_logs"
    __table_args__ = (Index("ix_audit_clinic_created", "clinic_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    clinic_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    actor_user_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    actor_label: Mapped[str] = mapped_column(String(255), default="")
    action: Mapped[str] = mapped_column(String(120), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(64), default="")
    resource_id: Mapped[str] = mapped_column(String(64), default="")
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    ip_address: Mapped[str] = mapped_column(String(64), default="")
    request_id: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
