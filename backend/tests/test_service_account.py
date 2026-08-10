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
