"""The monthly value report.

The number this exists to produce is out-of-hours calls, because that is the
one an owner cannot see any other way and the one that justifies the cost. It
is computed in the business's own timezone: doing it in UTC would move a 9pm
call in India into the next day, turning the strongest number in the report
into the weakest.
"""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from app.db.models import Business, Call, CallOutcome
from app.services.reports import build_monthly_report, render_text

pytestmark = pytest.mark.asyncio


def _at(local_str: str, tz: str = "Asia/Kolkata") -> datetime:
    """A local wall-clock time in the business's timezone, as UTC."""
    naive = datetime.strptime(local_str, "%Y-%m-%d %H:%M")
    return naive.replace(tzinfo=ZoneInfo(tz)).astimezone(timezone.utc)


async def _call(db, business_id: str, when: datetime, outcome: CallOutcome, caller="+919000000001"):
    call = Call(
        business_id=business_id,
        caller_number=caller,
        started_at=when,
        ended_at=when + timedelta(minutes=2),
        duration_seconds=120,
        outcome=outcome,
    )
    call.created_at = when
    db.add(call)
    return call


async def test_out_of_hours_uses_the_business_timezone(session_factory, tenants):
    """The report's headline number, and the one easiest to get wrong."""
    async with session_factory() as db:
        business = (
            await db.execute(select(Business).where(Business.id == tenants["alpha_id"]))
        ).scalar_one()
        # Fixture businesses open 09:00 to 18:00, Monday to Saturday.
        await _call(db, business.id, _at("2026-03-10 11:00"), CallOutcome.BOOKED)   # in hours
        await _call(db, business.id, _at("2026-03-10 21:30"), CallOutcome.BOOKED)   # after close
        await _call(db, business.id, _at("2026-03-10 07:00"), CallOutcome.ENQUIRY)  # before open
        await _call(db, business.id, _at("2026-03-08 12:00"), CallOutcome.ENQUIRY)  # a Sunday
        await db.commit()

        report = await build_monthly_report(db, business, 2026, 3)

    assert report.calls_total == 4
    assert report.calls_out_of_hours == 3, "evening, early morning and Sunday all count"
    assert report.out_of_hours_share == 75


async def test_counts_outcomes_and_repeat_callers(session_factory, tenants):
    async with session_factory() as db:
        business = (
            await db.execute(select(Business).where(Business.id == tenants["alpha_id"]))
        ).scalar_one()
        await _call(db, business.id, _at("2026-03-03 10:00"), CallOutcome.BOOKED, "+919000000001")
        await _call(db, business.id, _at("2026-03-04 10:00"), CallOutcome.BOOKED, "+919000000001")
        await _call(db, business.id, _at("2026-03-05 10:00"), CallOutcome.CANCELLED, "+919000000002")
        await _call(db, business.id, _at("2026-03-06 10:00"), CallOutcome.NO_DETAILS, "+919000000003")
        await db.commit()

        report = await build_monthly_report(db, business, 2026, 3)

    assert report.booked == 2
    assert report.cancelled == 1
    assert report.unresolved == 1
    assert report.repeat_callers == 1, "the number that called twice"
    assert report.minutes_answered == 8


async def test_a_month_with_no_calls_reads_sensibly(session_factory, tenants):
    """A silent month must not produce a broken sentence or divide by zero."""
    async with session_factory() as db:
        business = (
            await db.execute(select(Business).where(Business.id == tenants["alpha_id"]))
        ).scalar_one()
        report = await build_monthly_report(db, business, 2026, 1)

    assert report.calls_total == 0
    assert report.out_of_hours_share == 0
    assert "No calls" in report.headline()
    assert render_text(report)


async def test_other_tenants_calls_are_never_counted(session_factory, tenants):
    """A report is shown to a client, so a leak here is a leak to an outsider."""
    async with session_factory() as db:
        alpha = (
            await db.execute(select(Business).where(Business.id == tenants["alpha_id"]))
        ).scalar_one()
        await _call(db, alpha.id, _at("2026-03-03 10:00"), CallOutcome.BOOKED)
        await _call(db, tenants["beta_id"], _at("2026-03-03 11:00"), CallOutcome.BOOKED)
        await _call(db, tenants["beta_id"], _at("2026-03-03 12:00"), CallOutcome.BOOKED)
        await db.commit()

        report = await build_monthly_report(db, alpha, 2026, 3)

    assert report.calls_total == 1, "beta's two calls must not appear"


async def test_month_boundaries_do_not_leak_between_reports(session_factory, tenants):
    """A late call on the last night belongs to that month, in local time."""
    async with session_factory() as db:
        business = (
            await db.execute(select(Business).where(Business.id == tenants["alpha_id"]))
        ).scalar_one()
        await _call(db, business.id, _at("2026-03-31 23:30"), CallOutcome.BOOKED)
        await _call(db, business.id, _at("2026-04-01 00:30"), CallOutcome.BOOKED)
        await db.commit()

        march = await build_monthly_report(db, business, 2026, 3)
        april = await build_monthly_report(db, business, 2026, 4)

    assert march.calls_total == 1
    assert april.calls_total == 1
