"""Data retention.

Verifies the policy actually deletes, deletes only what it should, and stays
scoped to one tenant. A retention policy that silently does nothing is worse
than none, because it is claimed in a contract.
"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.db.models import Business, Call, CallOutcome
from app.services.retention import run_retention_sweep

pytestmark = pytest.mark.asyncio


async def _make_call(db, business_id: str, age_days: int, marker: str) -> str:
    call = Call(
        business_id=business_id,
        vapi_call_id=f"call-{marker}",
        caller_number="+919000000000",
        transcript=f"transcript {marker}",
        summary=f"summary {marker}",
        recording_url=f"https://recordings.example/{marker}.wav",
        outcome=CallOutcome.BOOKED,
        duration_seconds=120,
        cost_paise=2500,
    )
    db.add(call)
    await db.flush()
    # created_at has a default, so age it explicitly after insert.
    call.created_at = datetime.now(timezone.utc) - timedelta(days=age_days)
    await db.flush()
    return call.id


async def test_old_transcripts_and_recordings_are_cleared(session_factory, tenants, monkeypatch):
    monkeypatch.setattr("app.services.retention.SessionLocal", session_factory)

    async with session_factory() as db:
        business = (
            await db.execute(select(Business).where(Business.id == tenants["alpha_id"]))
        ).scalar_one()
        business.transcript_retention_days = 30
        business.recording_retention_days = 7

        recent = await _make_call(db, business.id, age_days=1, marker="recent")
        mid = await _make_call(db, business.id, age_days=14, marker="mid")
        old = await _make_call(db, business.id, age_days=60, marker="old")
        await db.commit()

    await run_retention_sweep()

    async with session_factory() as db:
        rows = {c.id: c for c in (await db.execute(select(Call))).scalars()}

        # Inside both windows: untouched.
        assert rows[recent].transcript == "transcript recent"
        assert rows[recent].recording_url != ""

        # Past the recording window only: audio gone, transcript kept.
        assert rows[mid].recording_url == ""
        assert rows[mid].transcript == "transcript mid"

        # Past both: content gone.
        assert rows[old].transcript == ""
        assert rows[old].summary == ""
        assert rows[old].recording_url == ""

        # The row itself survives, so analytics and billing history are intact.
        assert rows[old].duration_seconds == 120
        assert rows[old].cost_paise == 2500
        assert rows[old].outcome == CallOutcome.BOOKED


async def test_zero_means_keep_indefinitely(session_factory, tenants, monkeypatch):
    monkeypatch.setattr("app.services.retention.SessionLocal", session_factory)

    async with session_factory() as db:
        business = (
            await db.execute(select(Business).where(Business.id == tenants["alpha_id"]))
        ).scalar_one()
        business.transcript_retention_days = 0
        business.recording_retention_days = 0
        ancient = await _make_call(db, business.id, age_days=3650, marker="ancient")
        await db.commit()

    await run_retention_sweep()

    async with session_factory() as db:
        call = (await db.execute(select(Call).where(Call.id == ancient))).scalar_one()
        assert call.transcript == "transcript ancient"
        assert call.recording_url != ""


async def test_retention_is_scoped_to_each_business(session_factory, tenants, monkeypatch):
    """One tenant's aggressive policy must not purge another tenant's data."""
    monkeypatch.setattr("app.services.retention.SessionLocal", session_factory)

    async with session_factory() as db:
        alpha = (
            await db.execute(select(Business).where(Business.id == tenants["alpha_id"]))
        ).scalar_one()
        beta = (
            await db.execute(select(Business).where(Business.id == tenants["beta_id"]))
        ).scalar_one()
        alpha.transcript_retention_days = 1
        beta.transcript_retention_days = 3650

        alpha_call = await _make_call(db, alpha.id, age_days=30, marker="alpha")
        beta_call = await _make_call(db, beta.id, age_days=30, marker="beta")
        await db.commit()

    await run_retention_sweep()

    async with session_factory() as db:
        rows = {c.id: c for c in (await db.execute(select(Call))).scalars()}
        assert rows[alpha_call].transcript == ""
        assert rows[beta_call].transcript == "transcript beta"


async def test_sweep_is_idempotent(session_factory, tenants, monkeypatch):
    """Running twice must not error, and must report nothing left to do."""
    monkeypatch.setattr("app.services.retention.SessionLocal", session_factory)

    async with session_factory() as db:
        business = (
            await db.execute(select(Business).where(Business.id == tenants["alpha_id"]))
        ).scalar_one()
        business.transcript_retention_days = 1
        await _make_call(db, business.id, age_days=30, marker="dup")
        await db.commit()

    first = await run_retention_sweep()
    second = await run_retention_sweep()

    assert first["transcripts"] == 1
    assert second["transcripts"] == 0
