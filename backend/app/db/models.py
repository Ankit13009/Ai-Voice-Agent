"""ORM models. Types chosen to behave identically on SQLite (dev) and Postgres.

Tenancy: every table that holds business data carries a non-null `business_id`, and
the API layer derives that value from the caller's JWT, never from a request
parameter. `TenantMixin` exists to make an un-scoped table obvious in review.

Naming: the caller is a "customer", the appointment is an "appointment". The
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
from sqlalchemy.types import TypeDecorator


class UTCDateTime(TypeDecorator):
    """A DateTime that is always timezone-aware in Python.

    SQLite has no native timezone support: a value written as aware comes back
    naive, and every comparison against `datetime.now(timezone.utc)` then raises
    "can't compare offset-naive and offset-aware datetimes". Postgres does not
    have this problem, so the bug is invisible in production and fatal in local
    development, which is the worst possible split.

    Normalising in the type means no call site has to remember. Everything
    stored is converted to UTC on the way in and returned aware on the way out.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect):  # noqa: ANN001
        if value is None:
            return None
        # A naive value reaching the database is assumed UTC: every writer in
        # this codebase produces UTC, and guessing local time would silently
        # shift appointments.
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def process_result_value(self, value: datetime | None, dialect):  # noqa: ANN001
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


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
    SUPERADMIN = "superadmin"  # our staff: can create businesses, sees all tenants
    OWNER = "owner"  # business owner: full access to their own business
    STAFF = "staff"  # business receptionist: read + manage appointments


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
        UTCDateTime(), default=_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), default=_now, onupdate=_now, nullable=False
    )


class TenantMixin:
    """Marks a table as business-scoped. Presence of this mixin is the review
    signal that every query against the table must filter on `business_id`."""

    @property
    def tenant_id(self) -> str:  # pragma: no cover - documentation helper
        return self.business_id  # type: ignore[attr-defined]


# --------------------------------------------------------------------------- #
# Tenant root
# --------------------------------------------------------------------------- #
class Business(Base, TimestampMixin):
    """One row per business. This is the tenant boundary and the agent's config."""

    __tablename__ = "businesses"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(120), unique=True, index=True, nullable=False)

    # --- Contact / location ---
    address: Mapped[str] = mapped_column(Text, default="")
    city: Mapped[str] = mapped_column(String(120), default="")
    contact_phone: Mapped[str] = mapped_column(String(32), default="")
    contact_email: Mapped[str] = mapped_column(String(255), default="")
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Kolkata")

    # --- Business type configuration ---
    # These are seeded from a preset in `app/agent/presets.py` at onboarding and
    # are then fully editable per tenant. Everything downstream (the agent's
    # prompt, the tool descriptions, the dashboard's wording) reads these
    # columns, never the preset, so a business type nobody wrote a preset for is
    # still reachable by editing the form.
    business_type: Mapped[str] = mapped_column(String(64), default="general", index=True)
    # Completes "…for {name}, {business_descriptor}."
    business_descriptor: Mapped[str] = mapped_column(String(255), default="a local business")
    # What this trade calls its customers, staff, and bookings. Drives both the
    # agent's spoken vocabulary and the dashboard's labels.
    labels: Mapped[dict] = mapped_column(JSON, default=dict)
    # What the agent must collect before booking: [{key, label, required, guidance}].
    intake_fields: Mapped[list] = mapped_column(JSON, default=list)
    # Constraints the agent must not break. This is where the trade's liability
    # lives: a clinic must not diagnose, a law firm must not advise, a salon must
    # not invent prices.
    agent_rules: Mapped[list] = mapped_column(JSON, default=list)
    # What to do when the caller describes something urgent. Empty is valid:
    # plenty of trades have no meaningful emergency path.
    escalation_instructions: Mapped[str] = mapped_column(Text, default="")

    # --- Voice agent (VAPI) ---
    agent_name: Mapped[str] = mapped_column(String(120), default="Asha")
    # The number customers dial. Unique because inbound calls are routed by it.
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
    # ISO weekday numbers the business is open: 1=Mon ... 7=Sun.
    working_days: Mapped[list] = mapped_column(JSON, default=lambda: [1, 2, 3, 4, 5, 6])
    slot_duration_minutes: Mapped[int] = mapped_column(Integer, default=15)

    # --- WhatsApp ---
    whatsapp_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # Per-business override; falls back to the platform-wide number when empty.
    whatsapp_phone_number_id: Mapped[str] = mapped_column(String(64), default="")
    reminder_24h_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    reminder_2h_enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)

    # --- Data retention ---
    # Call transcripts and recordings of a clinic are health data. Keeping them
    # forever because nobody chose a number is the default that causes trouble,
    # so a default is chosen here and is editable per business. 0 means keep
    # indefinitely, which a business must opt into rather than fall into.
    transcript_retention_days: Mapped[int] = mapped_column(Integer, default=365)
    recording_retention_days: Mapped[int] = mapped_column(Integer, default=90)

    users: Mapped[list["User"]] = relationship(back_populates="business")
    staff_members: Mapped[list["StaffMember"]] = relationship(back_populates="business")

    # Neutral fallbacks, used when a tenant row predates a label or has it blank.
    # Never raises: a missing label must degrade to generic wording, not break a
    # live call or a dashboard render.
    _DEFAULT_LABELS = {
        "customer_singular": "Customer",
        "customer_plural": "Customers",
        "staff_singular": "Team member",
        "staff_plural": "Team members",
        "booking_singular": "appointment",
        "booking_plural": "appointments",
    }

    def label(self, key: str) -> str:
        return (self.labels or {}).get(key) or self._DEFAULT_LABELS.get(key, key)


# --------------------------------------------------------------------------- #
# Auth
# --------------------------------------------------------------------------- #
class User(Base, TimestampMixin):
    __tablename__ = "users"
    __table_args__ = (Index("ix_users_business_role", "business_id", "role"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(255), default="")
    role: Mapped[UserRole] = mapped_column(_enum(UserRole), default=UserRole.STAFF, nullable=False)

    # Null only for superadmins, who are not bound to a single tenant.
    business_id: Mapped[str | None] = mapped_column(
        ForeignKey("businesses.id", ondelete="CASCADE"), nullable=True, index=True
    )

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_login_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)

    # --- Brute-force protection ---
    # Rate limiting alone is per-process and keyed by IP, so it neither survives
    # a restart nor stops an attacker rotating addresses. Locking the account
    # itself is the control that actually binds to the thing being attacked.
    failed_login_attempts: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    # Set when an owner resets this user's password; forces a change at next login.
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False)

    business: Mapped["Business | None"] = relationship(back_populates="users")


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
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now)


# --------------------------------------------------------------------------- #
# Business resources
# --------------------------------------------------------------------------- #
class StaffMember(Base, TenantMixin, TimestampMixin):
    __tablename__ = "staff_members"
    __table_args__ = (Index("ix_staff_business_active", "business_id", "is_active"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    business_id: Mapped[str] = mapped_column(
        ForeignKey("businesses.id", ondelete="CASCADE"), index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    specialization: Mapped[str] = mapped_column(String(255), default="")
    # The Google Calendar this staff_member's appointments read from and write to.
    # Empty means the business-level calendar is used.
    google_calendar_id: Mapped[str] = mapped_column(String(255), default="")
    consultation_duration_minutes: Mapped[int] = mapped_column(Integer, default=15)
    opens_at: Mapped[time | None] = mapped_column(Time, nullable=True)
    closes_at: Mapped[time | None] = mapped_column(Time, nullable=True)
    working_days: Mapped[list | None] = mapped_column(JSON, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    business: Mapped["Business"] = relationship(back_populates="staff_members")


class AppointmentType(Base, TenantMixin, TimestampMixin):
    """e.g. "First consultation" (30 min), "Follow-up" (15 min)."""

    __tablename__ = "appointment_types"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    business_id: Mapped[str] = mapped_column(
        ForeignKey("businesses.id", ondelete="CASCADE"), index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    duration_minutes: Mapped[int] = mapped_column(Integer, default=15)
    description: Mapped[str] = mapped_column(Text, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Customer(Base, TenantMixin, TimestampMixin):
    __tablename__ = "customers"
    __table_args__ = (
        # A phone number identifies a returning customer within one business only.
        UniqueConstraint("business_id", "phone", name="uq_customer_business_phone"),
        Index("ix_customers_business_created", "business_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    business_id: Mapped[str] = mapped_column(
        ForeignKey("businesses.id", ondelete="CASCADE"), index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), default="")
    phone: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    email: Mapped[str] = mapped_column(String(255), default="")
    preferred_language: Mapped[Language] = mapped_column(_enum(Language), default=Language.MIXED)
    notes: Mapped[str] = mapped_column(Text, default="")

    appointments: Mapped[list["Appointment"]] = relationship(back_populates="customer")


# --------------------------------------------------------------------------- #
# Calls and appointments
# --------------------------------------------------------------------------- #
class Call(Base, TenantMixin, TimestampMixin):
    __tablename__ = "calls"
    __table_args__ = (
        Index("ix_calls_business_started", "business_id", "started_at"),
        Index("ix_calls_business_outcome", "business_id", "outcome"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    business_id: Mapped[str] = mapped_column(
        ForeignKey("businesses.id", ondelete="CASCADE"), index=True, nullable=False
    )
    # VAPI's call id, used to reconcile the end-of-call webhook with the row the
    # tool-call webhook already created.
    vapi_call_id: Mapped[str] = mapped_column(String(120), default="", index=True)
    customer_id: Mapped[str | None] = mapped_column(
        ForeignKey("customers.id", ondelete="SET NULL"), nullable=True, index=True
    )

    caller_number: Mapped[str] = mapped_column(String(32), default="")
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
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

    customer: Mapped["Customer | None"] = relationship()


class Appointment(Base, TenantMixin, TimestampMixin):
    __tablename__ = "appointments"
    __table_args__ = (
        Index("ix_appointments_business_start", "business_id", "starts_at"),
        Index("ix_appointments_business_status", "business_id", "status"),
        Index("ix_appointments_reminder_scan", "status", "starts_at"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    business_id: Mapped[str] = mapped_column(
        ForeignKey("businesses.id", ondelete="CASCADE"), index=True, nullable=False
    )
    customer_id: Mapped[str] = mapped_column(
        ForeignKey("customers.id", ondelete="CASCADE"), index=True, nullable=False
    )
    staff_member_id: Mapped[str | None] = mapped_column(
        ForeignKey("staff_members.id", ondelete="SET NULL"), nullable=True, index=True
    )
    appointment_type_id: Mapped[str | None] = mapped_column(
        ForeignKey("appointment_types.id", ondelete="SET NULL"), nullable=True
    )
    # The call that created it. Null for appointments made from the dashboard.
    call_id: Mapped[str | None] = mapped_column(
        ForeignKey("calls.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # Always stored in UTC. The business's timezone is applied at the edges only.
    starts_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
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

    customer: Mapped["Customer"] = relationship(back_populates="appointments")
    staff_member: Mapped["StaffMember | None"] = relationship()


# --------------------------------------------------------------------------- #
# Integrations
# --------------------------------------------------------------------------- #
class CalendarCredential(Base, TenantMixin, TimestampMixin):
    """Google OAuth credentials for one business.

    The refresh token is Fernet-encrypted at rest (`core.security`). It is never
    returned by any API endpoint; the business settings response exposes only
    `connected_email` and `is_connected`.
    """

    __tablename__ = "calendar_credentials"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    business_id: Mapped[str] = mapped_column(
        ForeignKey("businesses.id", ondelete="CASCADE"), unique=True, index=True, nullable=False
    )
    provider: Mapped[str] = mapped_column(String(32), default="google")
    connected_email: Mapped[str] = mapped_column(String(255), default="")
    calendar_id: Mapped[str] = mapped_column(String(255), default="primary")

    encrypted_refresh_token: Mapped[str] = mapped_column(Text, default="")
    encrypted_access_token: Mapped[str] = mapped_column(Text, default="")
    access_token_expires_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(), nullable=True
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
        Index("ix_wa_business_created", "business_id", "created_at"),
        # One message of each kind per appointment. This is what makes the
        # scheduler idempotent: a double-run hits the constraint instead of
        # double-texting the customer.
        UniqueConstraint("appointment_id", "kind", name="uq_wa_appointment_kind"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    business_id: Mapped[str] = mapped_column(
        ForeignKey("businesses.id", ondelete="CASCADE"), index=True, nullable=False
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

    scheduled_for: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)

    wa_message_id: Mapped[str] = mapped_column(String(128), default="", index=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str] = mapped_column(Text, default="")


class AuditLog(Base):
    """Append-only record of privileged actions.

    Business data is health data. Knowing who cancelled an appointment or exported
    a customer list is a baseline requirement, and it is far cheaper to write from
    day one than to retrofit.
    """

    __tablename__ = "audit_logs"
    __table_args__ = (Index("ix_audit_business_created", "business_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    business_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    actor_user_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    actor_label: Mapped[str] = mapped_column(String(255), default="")
    action: Mapped[str] = mapped_column(String(120), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(64), default="")
    resource_id: Mapped[str] = mapped_column(String(64), default="")
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    ip_address: Mapped[str] = mapped_column(String(64), default="")
    request_id: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now, index=True)
