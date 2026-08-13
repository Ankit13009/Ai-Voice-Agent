"""Booking, rescheduling, and the WhatsApp rows each one leaves behind.

This file exists because rescheduling was broken in every deployment and no test
noticed. The reason it went unnoticed is worth stating: the suite covered auth,
tenancy and config thoroughly, and the write paths that touch three services at
once not at all. So the assertions here are deliberately about the *side effects* —
what is queued, what is cancelled, and what got written to the calendar — rather
than only the return value.

Google is patched throughout. `is_slot_free` is the only call that must say yes for
a booking to proceed, and `create_event` / `update_event_time` are recorded so a
test can assert the calendar was (or was not) touched.
"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.db.models import (
    Appointment,
    AppointmentStatus,
    Business,
    MessageKind,
    MessageStatus,
    WhatsAppMessage,
)

pytestmark = pytest.mark.asyncio


@pytest.fixture
def calendar(monkeypatch):
    """A stand-in Google Calendar that records what was asked of it."""
    from app.services import google_calendar as gcal

    log: dict = {"created": [], "moved": [], "deleted": [], "fail_move": False}

    async def is_slot_free(*_args, **_kwargs):
        return True

    async def create_event(_db, _business, **kwargs):
        log["created"].append(kwargs)
        return f"evt-{len(log['created'])}", "primary"

    async def update_event_time(_db, _business, appointment, *, start, end):
        if log["fail_move"]:
            raise RuntimeError("Google rejected the move")
        log["moved"].append((appointment.id, start))

    async def delete_event(_db, _business, appointment):
        log["deleted"].append(appointment.id)

    monkeypatch.setattr(gcal, "is_slot_free", is_slot_free)
    monkeypatch.setattr(gcal, "create_event", create_event)
    monkeypatch.setattr(gcal, "update_event_time", update_event_time)
    monkeypatch.setattr(gcal, "delete_event", delete_event)
    return log


async def _alpha(db) -> Business:
    return (
        await db.execute(select(Business).where(Business.slug == "alpha-clinic"))
    ).scalar_one()


async def _book(db, business, *, days_ahead: int = 5):
    from app.services import appointments as svc

    return await svc.book_appointment(
        db,
        business,
        customer_name="Meera Iyer",
        customer_phone="+919876500001",
        starts_at=datetime.now(timezone.utc) + timedelta(days=days_ahead),
    )


async def _messages(db, appointment_id: str) -> dict[MessageKind, WhatsAppMessage]:
    rows = (
        await db.execute(
            select(WhatsAppMessage).where(WhatsAppMessage.appointment_id == appointment_id)
        )
    ).scalars().all()
    return {row.kind: row for row in rows}


async def test_booking_queues_a_confirmation_and_both_reminders(
    session_factory, tenants, calendar
):
    async with session_factory() as db:
        business = await _alpha(db)
        # The owner alert is skipped without a number to send it to, and it shares
        # the appointment id with the customer's messages, so setting one here is
        # what proves the two coexist rather than competing for the same row.
        business.owner_notify_phone = "+919812300000"
        appointment = await _book(db, business)
        await db.commit()

        queued = await _messages(db, appointment.id)

    assert MessageKind.CONFIRMATION in queued
    assert MessageKind.OWNER_BOOKING_ALERT in queued
    for kind, lead in ((MessageKind.REMINDER_24H, 24), (MessageKind.REMINDER_2H, 2)):
        assert queued[kind].status == MessageStatus.PENDING
        assert queued[kind].scheduled_for == appointment.starts_at - timedelta(hours=lead)


async def test_rescheduling_moves_the_appointment_and_requeues_the_reminders(
    session_factory, tenants, calendar
):
    """The regression: re-queueing a reminder used to collide with the row the
    cancellation had just left behind, and every reschedule raised IntegrityError."""
    from app.services import appointments as svc

    async with session_factory() as db:
        business = await _alpha(db)
        appointment = await _book(db, business)
        await db.commit()

        new_time = appointment.starts_at + timedelta(days=2)
        await svc.reschedule_appointment(db, business, appointment, starts_at=new_time)
        await db.commit()

        assert appointment.starts_at == new_time
        assert appointment.status == AppointmentStatus.RESCHEDULED

        queued = await _messages(db, appointment.id)

    # Reminders now point at the new time, and are live rather than cancelled.
    for kind, lead in ((MessageKind.REMINDER_24H, 24), (MessageKind.REMINDER_2H, 2)):
        assert queued[kind].status == MessageStatus.PENDING
        assert queued[kind].scheduled_for == new_time - timedelta(hours=lead)

    # And the customer is told about the move.
    assert queued[MessageKind.RESCHEDULE].status == MessageStatus.PENDING

    assert calendar["moved"] == [(appointment.id, new_time)]


async def test_an_appointment_can_be_rescheduled_more_than_once(
    session_factory, tenants, calendar
):
    """The second move collided on the `reschedule` row the first one created."""
    from app.services import appointments as svc

    async with session_factory() as db:
        business = await _alpha(db)
        appointment = await _book(db, business)
        await db.commit()

        first = appointment.starts_at + timedelta(days=1)
        await svc.reschedule_appointment(db, business, appointment, starts_at=first)
        await db.commit()

        second = first + timedelta(days=1)
        await svc.reschedule_appointment(db, business, appointment, starts_at=second)
        await db.commit()

        assert appointment.starts_at == second
        queued = await _messages(db, appointment.id)

    assert queued[MessageKind.RESCHEDULE].scheduled_for is not None
    assert queued[MessageKind.REMINDER_24H].scheduled_for == second - timedelta(hours=24)
    assert len(calendar["moved"]) == 2


async def test_a_failed_calendar_move_leaves_the_stored_time_alone(
    session_factory, tenants, calendar
):
    """Ordering: the calendar is written last, so a Google failure cannot leave the
    event at one time and the database at another.

    This was the damaging half of the reschedule bug. The move went through, the
    database rolled back, and the dashboard then disagreed with the calendar the
    business actually reads.
    """
    from app.services import appointments as svc

    async with session_factory() as db:
        business = await _alpha(db)
        appointment = await _book(db, business)
        await db.commit()

        # Read both before the failure: the rollback expires every object in the
        # session, so touching one afterwards would go back to the database.
        appointment_id = appointment.id
        original = appointment.starts_at
        calendar["fail_move"] = True

        with pytest.raises(RuntimeError):
            await svc.reschedule_appointment(
                db, business, appointment, starts_at=original + timedelta(days=3)
            )
        await db.rollback()

        stored = (
            await db.execute(select(Appointment).where(Appointment.id == appointment_id))
        ).scalar_one()
        assert stored.starts_at == original
        assert stored.status == AppointmentStatus.SCHEDULED


async def test_cancelling_kills_the_pending_reminders(session_factory, tenants, calendar):
    """A cancelled customer receiving "your appointment is in 2 hours" is the worst
    failure an automated reminder system has."""
    from app.services import appointments as svc

    async with session_factory() as db:
        business = await _alpha(db)
        appointment = await _book(db, business)
        await db.commit()

        await svc.cancel_appointment(db, business, appointment, reason="changed plans")
        await db.commit()

        queued = await _messages(db, appointment.id)

    assert queued[MessageKind.REMINDER_24H].status == MessageStatus.CANCELLED
    assert queued[MessageKind.REMINDER_2H].status == MessageStatus.CANCELLED
    assert queued[MessageKind.CANCELLATION].status == MessageStatus.PENDING
    assert calendar["deleted"] == [appointment.id]


async def test_the_dashboard_can_reschedule_over_http(client, tenants, calendar):
    """The same path a receptionist actually takes.

    Worth having alongside the service-level tests: the endpoint is a thin wrapper,
    but "thin wrapper over a broken function" is precisely what shipped, and only a
    request that goes through the router, the envelope and the commit proves the
    whole thing.
    """
    from tests.conftest import auth, login

    token = await login(client, "owner@alpha.in", "alpha-password-1")
    starts_at = datetime.now(timezone.utc) + timedelta(days=6)

    created = await client.post(
        "/api/v1/appointments",
        json={
            "customer_name": "Sunil Verma",
            "customer_phone": "+919876500003",
            "starts_at": starts_at.isoformat(),
            "reason": "follow-up",
        },
        headers=auth(token),
    )
    assert created.status_code == 201, created.text
    appointment_id = created.json()["data"]["id"]

    moved_to = starts_at + timedelta(days=1)
    moved = await client.patch(
        f"/api/v1/appointments/{appointment_id}/reschedule",
        json={"starts_at": moved_to.isoformat(), "reason": "clashed with work"},
        headers=auth(token),
    )
    assert moved.status_code == 200, moved.text
    body = moved.json()
    assert body["success"] is True
    assert body["data"]["status"] == "rescheduled"
    assert datetime.fromisoformat(body["data"]["starts_at"]) == moved_to

    # The message log the owner sees must show a live reschedule notice.
    log = await client.get(
        "/api/v1/messages", params={"appointment_id": appointment_id}, headers=auth(token)
    )
    kinds = {row["kind"]: row["status"] for row in log.json()["data"]}
    assert kinds["reschedule"] == "pending"
    assert kinds["reminder_24h"] == "pending"
