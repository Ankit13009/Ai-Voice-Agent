"""A business sending WhatsApp from its own number.

A doctor wants patients to see the clinic, not the platform, and the WhatsApp
number is normally a different one from the line people call. Two properties
matter: the token is never readable back, and one client can never redirect
another client's messages through their own number.
"""

import pytest
from sqlalchemy import select

from app.db.models import Business
from tests.conftest import auth, login

pytestmark = pytest.mark.asyncio

ALPHA_OWNER = ("owner@alpha.in", "alpha-password-1")
BETA_OWNER = ("owner@beta.in", "beta-password-1")


async def test_status_never_returns_the_token(client, tenants, session_factory):
    from app.core.security import encrypt_secret

    async with session_factory() as db:
        business = (
            await db.execute(select(Business).where(Business.id == tenants["alpha_id"]))
        ).scalar_one()
        business.whatsapp_encrypted_access_token = encrypt_secret("EAA-secret-token")
        business.whatsapp_phone_number_id = "111222333"
        business.whatsapp_display_number = "+919812345678"
        await db.commit()

    token = await login(client, *ALPHA_OWNER)
    r = await client.get("/api/v1/integrations/whatsapp", headers=auth(token))

    assert r.status_code == 200
    body = r.json()
    assert body["data"]["using_own_number"] is True
    assert body["data"]["display_number"] == "+919812345678"
    assert body["data"]["has_access_token"] is True
    assert "EAA-secret-token" not in str(body), "the token must never be returned"


async def test_an_owner_only_ever_edits_their_own_sender(client, tenants, session_factory):
    """The isolation that matters: redirecting another tenant's messages."""
    token = await login(client, *ALPHA_OWNER)

    # business_id is derived from the JWT, so naming beta cannot retarget it.
    r = await client.put(
        "/api/v1/integrations/whatsapp",
        params={"business_id": tenants["beta_id"]},
        json={"phone_number_id": "attacker-number"},
        headers=auth(token),
    )
    assert r.status_code in (200, 403)

    async with session_factory() as db:
        beta = (
            await db.execute(select(Business).where(Business.id == tenants["beta_id"]))
        ).scalar_one()
        assert beta.whatsapp_phone_number_id != "attacker-number", (
            "one tenant must never be able to change another's WhatsApp sender"
        )


async def test_clearing_the_token_falls_back_to_the_platform_sender(
    client, tenants, session_factory
):
    """An owner must be able to hand control back without support."""
    from app.core.security import encrypt_secret

    async with session_factory() as db:
        business = (
            await db.execute(select(Business).where(Business.id == tenants["alpha_id"]))
        ).scalar_one()
        business.whatsapp_encrypted_access_token = encrypt_secret("EAA-old")
        business.whatsapp_phone_number_id = "111222333"
        await db.commit()

    token = await login(client, *ALPHA_OWNER)
    r = await client.put(
        "/api/v1/integrations/whatsapp",
        json={"access_token": ""},
        headers=auth(token),
    )
    assert r.status_code == 200
    assert r.json()["data"]["using_own_number"] is False

    async with session_factory() as db:
        business = (
            await db.execute(select(Business).where(Business.id == tenants["alpha_id"]))
        ).scalar_one()
        assert business.whatsapp_encrypted_access_token == ""


async def test_sends_use_the_business_own_credentials_when_present(
    tenants, session_factory
):
    """The point of the feature: patients see the clinic's number."""
    from app.core.security import encrypt_secret
    from app.services.whatsapp import _credentials_for

    async with session_factory() as db:
        business = (
            await db.execute(select(Business).where(Business.id == tenants["alpha_id"]))
        ).scalar_one()
        business.whatsapp_encrypted_access_token = encrypt_secret("clinic-token")
        business.whatsapp_phone_number_id = "clinic-number-id"
        await db.flush()

        phone_id, token = await _credentials_for(business)

    assert (phone_id, token) == ("clinic-number-id", "clinic-token")


async def test_a_business_without_its_own_token_uses_the_platform_sender(
    tenants, session_factory, monkeypatch
):
    """A salon should not need Meta business verification to get reminders."""
    from app.services import platform_config
    from app.services.whatsapp import _credentials_for

    async def fake_get(key):
        return {"whatsapp_phone_number_id": "platform-number", "whatsapp_access_token": "platform-token"}[key]

    monkeypatch.setattr(platform_config, "get_value", fake_get)

    async with session_factory() as db:
        business = (
            await db.execute(select(Business).where(Business.id == tenants["alpha_id"]))
        ).scalar_one()
        business.whatsapp_encrypted_access_token = ""
        business.whatsapp_phone_number_id = ""
        await db.flush()

        assert await _credentials_for(business) == ("platform-number", "platform-token")
