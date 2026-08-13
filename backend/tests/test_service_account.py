"""Service-account calendar mode.

This exists to remove an entire class of failure: Google expires refresh tokens
after 7 days while an app is unverified, revokes them on password changes, and
gates OAuth behind a test-user list. A shared calendar has none of those, so a
client can be connected once and left alone.

The behaviour worth pinning is that connecting is *verified* rather than
trusted. A typo in the calendar address, or a calendar shared read-only, must
fail at connect time and not on the first real call.
"""

import pytest
from sqlalchemy import select

from app.db.models import CalendarCredential
from tests.conftest import auth, login

pytestmark = pytest.mark.asyncio

ALPHA_OWNER = ("owner@alpha.in", "alpha-password-1")


async def test_reports_unavailable_without_a_key(client):
    token = await login(client, *ALPHA_OWNER)
    r = await client.get("/api/v1/integrations/google/service-account", headers=auth(token))
    assert r.status_code == 200
    assert r.json()["data"]["available"] is False


async def test_connect_is_refused_when_the_calendar_cannot_be_read(
    client, tenants, monkeypatch
):
    """The failure this endpoint exists to catch, at connect time."""
    from app.services import google_calendar as gcal

    monkeypatch.setattr(gcal, "service_account_email", lambda: "bot@project.iam.gserviceaccount.com")

    async def boom(*args, **kwargs):
        raise RuntimeError("404 notFound")

    monkeypatch.setattr(gcal, "get_busy_windows", boom)

    token = await login(client, *ALPHA_OWNER)
    r = await client.post(
        "/api/v1/integrations/google/service-account",
        json={"calendar_id": "typo@gmail.com"},
        headers=auth(token),
    )

    assert r.status_code == 400
    assert "shared" in r.json()["error"]["message"].lower()


async def test_successful_connect_clears_any_oauth_tokens(
    client, tenants, session_factory, monkeypatch
):
    """Leaving a refresh token behind would be a live credential kept for nothing."""
    from app.services import google_calendar as gcal

    monkeypatch.setattr(gcal, "service_account_email", lambda: "bot@project.iam.gserviceaccount.com")

    async def ok_busy(*args, **kwargs):
        return []

    monkeypatch.setattr(gcal, "get_busy_windows", ok_busy)

    async with session_factory() as db:
        db.add(
            CalendarCredential(
                business_id=tenants["alpha_id"],
                encrypted_refresh_token="leftover-oauth-token",
                is_connected=True,
            )
        )
        await db.commit()

    token = await login(client, *ALPHA_OWNER)
    r = await client.post(
        "/api/v1/integrations/google/service-account",
        json={"calendar_id": "clinic@gmail.com"},
        headers=auth(token),
    )
    assert r.status_code == 200, r.text

    async with session_factory() as db:
        cred = (
            await db.execute(
                select(CalendarCredential).where(
                    CalendarCredential.business_id == tenants["alpha_id"]
                )
            )
        ).scalar_one()
        assert cred.auth_mode == "service_account"
        assert cred.calendar_id == "clinic@gmail.com"
        assert cred.is_connected is True
        assert cred.encrypted_refresh_token == "", "the old OAuth token must be dropped"


async def test_service_account_business_never_uses_the_refresh_path(
    tenants, session_factory, monkeypatch
):
    """The point of the mode: no refresh token means nothing to expire."""
    from app.services import google_calendar as gcal

    async def fake_mint():
        return "minted-token"

    monkeypatch.setattr(gcal, "_service_account_token", fake_mint)

    async with session_factory() as db:
        db.add(
            CalendarCredential(
                business_id=tenants["alpha_id"],
                auth_mode="service_account",
                calendar_id="clinic@gmail.com",
                is_connected=True,
                encrypted_refresh_token="",
            )
        )
        await db.commit()

        token, cred = await gcal._get_access_token(db, tenants["alpha_id"])

    assert token == "minted-token"
    assert cred.auth_mode == "service_account"


async def test_a_staff_user_cannot_repoint_the_calendar(client, tenants, monkeypatch):
    """Repointing a calendar redirects every future booking, so it is owner-only."""
    from app.services import google_calendar as gcal

    monkeypatch.setattr(gcal, "service_account_email", lambda: "bot@project.iam.gserviceaccount.com")

    token = await login(client, *ALPHA_OWNER)
    created = await client.post(
        "/api/v1/businesses/me/users",
        json={"email": "desk@alpha.in", "full_name": "Desk", "role": "staff"},
        headers=auth(token),
    )
    temporary = created.json()["data"]["temporary_password"]
    staff_token = await login(client, "desk@alpha.in", temporary)

    r = await client.post(
        "/api/v1/integrations/google/service-account",
        json={"calendar_id": "attacker@gmail.com"},
        headers=auth(staff_token),
    )
    assert r.status_code == 403


# --------------------------------------------------------------------------- #
# Which calendar the agent actually reads and writes
# --------------------------------------------------------------------------- #
# Connecting stored the shared calendar and verified it, and then nothing ever
# read the value back: availability and event writes both went to "primary",
# which for a service account is its own empty calendar. The connection reported
# healthy, the agent saw every slot as free, and bookings landed somewhere the
# business could not see. These tests pin the value down at each of the three
# places it is used.
async def _connect_shared_calendar(db, business_id: str, calendar_id: str) -> None:
    db.add(
        CalendarCredential(
            business_id=business_id,
            auth_mode="service_account",
            calendar_id=calendar_id,
            is_connected=True,
        )
    )
    await db.commit()


@pytest.fixture
def calendar_probe(monkeypatch):
    """Record which calendar id reaches Google, and answer "nothing is busy"."""
    from app.services import google_calendar as gcal

    asked: list[str] = []

    async def get_busy_windows(_db, _business_id, calendar_id, _start, _end):
        asked.append(calendar_id)
        return []

    monkeypatch.setattr(gcal, "get_busy_windows", get_busy_windows)
    return asked


async def test_availability_reads_the_businesss_shared_calendar(
    session_factory, tenants, calendar_probe
):
    from datetime import datetime, timedelta, timezone

    from app.db.models import Business
    from app.services import google_calendar as gcal

    async with session_factory() as db:
        await _connect_shared_calendar(db, tenants["alpha_id"], "alpha-clinic@gmail.com")
        business = (
            await db.execute(select(Business).where(Business.id == tenants["alpha_id"]))
        ).scalar_one()

        now = datetime.now(timezone.utc)
        await gcal.find_available_slots(db, business, start=now, end=now + timedelta(days=2))
        await gcal.is_slot_free(db, business, now + timedelta(days=1), now + timedelta(days=1, minutes=15))

    assert calendar_probe == ["alpha-clinic@gmail.com", "alpha-clinic@gmail.com"]


async def test_bookings_are_written_to_the_shared_calendar(
    session_factory, tenants, calendar_probe, monkeypatch
):
    """The event must land in the calendar the business reads, and the appointment
    must remember which one, so a later move or cancellation finds it."""
    from datetime import datetime, timedelta, timezone

    from app.db.models import Business
    from app.services import appointments as svc
    from app.services import google_calendar as gcal

    written: dict = {}

    async def api_request(_db, _business_id, method, path, **kwargs):
        written["path"] = path
        return {"id": "evt-shared"}

    monkeypatch.setattr(gcal, "_api_request", api_request)

    async with session_factory() as db:
        await _connect_shared_calendar(db, tenants["alpha_id"], "alpha-clinic@gmail.com")
        business = (
            await db.execute(select(Business).where(Business.id == tenants["alpha_id"]))
        ).scalar_one()

        appointment = await svc.book_appointment(
            db,
            business,
            customer_name="Ravi Kumar",
            customer_phone="+919876500002",
            starts_at=datetime.now(timezone.utc) + timedelta(days=4),
        )
        await db.commit()
        stored_calendar = appointment.google_calendar_id

    assert written["path"] == "/calendars/alpha-clinic@gmail.com/events"
    assert stored_calendar == "alpha-clinic@gmail.com"


async def test_an_oauth_business_still_uses_its_primary_calendar(
    session_factory, tenants, calendar_probe
):
    """OAuth connects the owner's own account, where "primary" is the right
    calendar. This mode must be unchanged."""
    from datetime import datetime, timedelta, timezone

    from app.db.models import Business
    from app.services import google_calendar as gcal

    async with session_factory() as db:
        db.add(
            CalendarCredential(
                business_id=tenants["alpha_id"],
                auth_mode="oauth",
                is_connected=True,
                encrypted_refresh_token="whatever",
            )
        )
        await db.commit()
        business = (
            await db.execute(select(Business).where(Business.id == tenants["alpha_id"]))
        ).scalar_one()

        now = datetime.now(timezone.utc)
        await gcal.find_available_slots(db, business, start=now, end=now + timedelta(days=2))

    assert calendar_probe == ["primary"]


async def test_a_staff_members_own_calendar_wins_over_the_business_one(
    session_factory, tenants, calendar_probe
):
    """Per-staff calendars are how a multi-chair business gets real capacity, so a
    staff override must not be swallowed by the business-level lookup."""
    from datetime import datetime, timedelta, timezone

    from app.db.models import Business, StaffMember
    from app.services import google_calendar as gcal

    async with session_factory() as db:
        await _connect_shared_calendar(db, tenants["alpha_id"], "alpha-clinic@gmail.com")
        business = (
            await db.execute(select(Business).where(Business.id == tenants["alpha_id"]))
        ).scalar_one()
        staff = StaffMember(
            business_id=business.id,
            name="Dr Sharma",
            google_calendar_id="dr-sharma@gmail.com",
        )
        db.add(staff)
        await db.commit()

        now = datetime.now(timezone.utc)
        await gcal.find_available_slots(
            db, business, start=now, end=now + timedelta(days=2), staff_member=staff
        )

    assert calendar_probe == ["dr-sharma@gmail.com"]
