"""Owner notifications, waitlist recovery, and caller recognition."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.db.models import (
    Appointment,
    AppointmentStatus,
    Business,
    Customer,
    MessageKind,
    WaitlistEntry,
    WhatsAppMessage,
)

pytestmark = pytest.mark.asyncio


async def test_owner_is_notified_when_a_booking_is_made(session_factory, tenants):
    from app.services.notifications import notify_owner_of_booking

    async with session_factory() as db:
        business = (
            await db.execute(select(Business).where(Business.id == tenants["alpha_id"]))
        ).scalar_one()
        business.owner_notify_phone = "+919000000099"

        customer = Customer(business_id=business.id, name="Anjali", phone="+919876543210")
        db.add(customer)
        await db.flush()
        appointment = Appointment(
            business_id=business.id,
            customer_id=customer.id,
            starts_at=datetime.now(timezone.utc) + timedelta(days=1),
            ends_at=datetime.now(timezone.utc) + timedelta(days=1, minutes=15),
            status=AppointmentStatus.SCHEDULED,
            reason="cough",
        )
        db.add(appointment)
        await db.flush()

        await notify_owner_of_booking(db, business, appointment, customer)
        await db.commit()

    async with session_factory() as db:
        msg = (
            await db.execute(
                select(WhatsAppMessage).where(
                    WhatsAppMessage.kind == MessageKind.OWNER_BOOKING_ALERT
                )
            )
        ).scalar_one()
        # Goes to the owner, not the customer.
        assert msg.to_phone == "+919000000099"
        assert "Anjali" in msg.rendered_preview
        assert "cough" in msg.rendered_preview


async def test_owner_alerts_are_english_regardless_of_the_customer(session_factory, tenants):
    """The owner's language has nothing to do with who happened to call."""
    from app.db.models import Language
    from app.services import whatsapp

    spec = whatsapp.resolve_template(MessageKind.OWNER_BOOKING_ALERT, Language.HINDI)
    assert spec.language_code == "en"

    # A customer-facing message still follows the customer.
    spec = whatsapp.resolve_template(MessageKind.CONFIRMATION, Language.HINDI)
    assert spec.language_code == "hi"


async def test_cancelling_notifies_the_waitlist_for_that_window(session_factory, tenants):
    from app.services.notifications import notify_waitlist_for_freed_slot

    start = datetime.now(timezone.utc) + timedelta(days=2)
    end = start + timedelta(minutes=15)

    async with session_factory() as db:
        business = (
            await db.execute(select(Business).where(Business.id == tenants["alpha_id"]))
        ).scalar_one()

        waiting = Customer(business_id=business.id, name="Ravi", phone="+919812345678")
        unrelated = Customer(business_id=business.id, name="Other", phone="+919812345679")
        db.add_all([waiting, unrelated])
        await db.flush()

        db.add(
            WaitlistEntry(
                business_id=business.id,
                customer_id=waiting.id,
                preferred_from=start - timedelta(hours=2),
                preferred_to=end + timedelta(hours=2),
                reason="check-up",
            )
        )
        # Wants a completely different week, so must not be notified.
        db.add(
            WaitlistEntry(
                business_id=business.id,
                customer_id=unrelated.id,
                preferred_from=start + timedelta(days=20),
                preferred_to=start + timedelta(days=21),
            )
        )
        await db.flush()

        notified = await notify_waitlist_for_freed_slot(db, business, start, end)
        await db.commit()

    assert notified == 1

    async with session_factory() as db:
        msgs = (
            await db.execute(
                select(WhatsAppMessage).where(
                    WhatsAppMessage.kind == MessageKind.WAITLIST_SLOT_OPEN
                )
            )
        ).scalars().all()
        assert len(msgs) == 1
        assert msgs[0].to_phone == "+919812345678"

        entries = (await db.execute(select(WaitlistEntry))).scalars().all()
        statuses = sorted(e.status for e in entries)
        assert statuses == ["notified", "waiting"]


async def test_daily_summary_is_sent_once_per_day(session_factory, tenants, monkeypatch):
    from app.services.notifications import send_due_daily_summaries

    monkeypatch.setattr("app.services.notifications.SessionLocal", session_factory)

    async with session_factory() as db:
        business = (
            await db.execute(select(Business).where(Business.id == tenants["alpha_id"]))
        ).scalar_one()
        business.owner_notify_phone = "+919000000099"
        business.daily_summary_hour = 0  # always due, so the test is not clock-dependent

        customer = Customer(business_id=business.id, name="Someone", phone="+919000000010")
        db.add(customer)
        await db.flush()
        db.add(
            Appointment(
                business_id=business.id,
                customer_id=customer.id,
                starts_at=datetime.now(timezone.utc) + timedelta(hours=2),
                ends_at=datetime.now(timezone.utc) + timedelta(hours=2, minutes=15),
                status=AppointmentStatus.SCHEDULED,
            )
        )
        await db.commit()

    first = await send_due_daily_summaries()
    second = await send_due_daily_summaries()

    assert first == 1
    # The guard is a stored date, so a second run the same day is a no-op even
    # though nothing else changed.
    assert second == 0


async def test_silent_day_produces_no_summary(session_factory, tenants, monkeypatch):
    """A daily '0 calls, 0 bookings' trains the owner to ignore the channel."""
    from app.services.notifications import send_due_daily_summaries

    monkeypatch.setattr("app.services.notifications.SessionLocal", session_factory)

    async with session_factory() as db:
        business = (
            await db.execute(select(Business).where(Business.id == tenants["beta_id"]))
        ).scalar_one()
        business.owner_notify_phone = "+919000000098"
        business.daily_summary_hour = 0
        await db.commit()

    assert await send_due_daily_summaries() == 0


async def test_owner_is_alerted_when_the_calendar_disconnects(session_factory, tenants):
    """A revoked calendar must reach the owner, not just the logs.

    With no calendar the agent refuses every booking, so the outage is silent
    from the owner's side until a caller complains.
    """
    from app.services.notifications import notify_owner_calendar_disconnected

    async with session_factory() as db:
        business = (
            await db.execute(select(Business).where(Business.id == tenants["alpha_id"]))
        ).scalar_one()
        business.owner_notify_phone = "+919000000099"
        await db.flush()

        assert await notify_owner_calendar_disconnected(db, business) is True
        await db.commit()

    async with session_factory() as db:
        message = (
            await db.execute(
                select(WhatsAppMessage).where(
                    WhatsAppMessage.kind == MessageKind.OWNER_CALENDAR_DISCONNECTED
                )
            )
        ).scalar_one()
        assert message.to_phone == "+919000000099"
        assert message.business_id == tenants["alpha_id"]
        # The owner must be told which business, since one owner may run several.
        assert "disconnected" in message.rendered_preview.lower()


async def test_calendar_alert_is_not_silenced_by_muting_booking_alerts(
    session_factory, tenants
):
    """Muting routine per-booking pings must not also mute an outage."""
    from app.services.notifications import notify_owner_calendar_disconnected

    async with session_factory() as db:
        business = (
            await db.execute(select(Business).where(Business.id == tenants["alpha_id"]))
        ).scalar_one()
        business.owner_notify_phone = "+919000000099"
        business.notify_on_booking = False
        await db.flush()

        assert await notify_owner_calendar_disconnected(db, business) is True


async def test_calendar_alert_is_skipped_when_no_owner_number_exists(
    session_factory, tenants
):
    """No number is a logged warning, not a crash inside the failure path."""
    from app.services.notifications import notify_owner_calendar_disconnected

    async with session_factory() as db:
        business = (
            await db.execute(select(Business).where(Business.id == tenants["alpha_id"]))
        ).scalar_one()
        business.owner_notify_phone = ""
        business.contact_phone = ""
        await db.flush()

        assert await notify_owner_calendar_disconnected(db, business) is False
