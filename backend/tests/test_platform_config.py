"""Operator-managed credentials.

These moved out of environment variables so onboarding a client does not
require the hosting login. That makes two things worth pinning: only a
superadmin can touch them, and the API never hands a token back out. A settings
screen that echoes a secret turns every screenshot and screen-share into a leak.
"""

import pytest

from tests.conftest import auth, login

pytestmark = pytest.mark.asyncio

ADMIN = ("admin@platform.in", "admin-password-1")
ALPHA_OWNER = ("owner@alpha.in", "alpha-password-1")


async def test_an_owner_cannot_read_or_write_platform_credentials(client):
    """These are platform-wide: one tenant must not see or change them."""
    token = await login(client, *ALPHA_OWNER)

    assert (await client.get("/api/v1/admin/whatsapp", headers=auth(token))).status_code == 403
    r = await client.put(
        "/api/v1/admin/whatsapp",
        json={"whatsapp_access_token": "stolen"},
        headers=auth(token),
    )
    assert r.status_code == 403


async def test_saving_then_reading_never_returns_the_secret(client):
    token = await login(client, *ADMIN)

    saved = await client.put(
        "/api/v1/admin/whatsapp",
        json={
            "whatsapp_access_token": "EAAG-super-secret-token-1234",
            "whatsapp_phone_number_id": "123456789",
        },
        headers=auth(token),
    )
    assert saved.status_code == 200, saved.text

    body = (await client.get("/api/v1/admin/whatsapp", headers=auth(token))).json()
    settings = body["data"]["settings"]

    assert settings["whatsapp_access_token"]["set"] is True
    assert settings["whatsapp_access_token"]["source"] == "dashboard"
    # Enough to recognise which token is in use, not enough to use it.
    assert settings["whatsapp_access_token"]["preview"] == "...1234"
    assert "super-secret" not in str(body), "the raw token must never be returned"

    # Ids are not secret: an operator needs to see them to spot a typo.
    assert settings["whatsapp_phone_number_id"]["preview"] == "123456789"


async def test_clearing_a_value_falls_back_to_the_environment(client, monkeypatch):
    """The only way out of a bad paste without a deploy."""
    from app.config import get_settings
    from app.services import platform_config

    token = await login(client, *ADMIN)

    await client.put(
        "/api/v1/admin/whatsapp",
        json={"whatsapp_app_secret": "typed-wrong"},
        headers=auth(token),
    )
    assert await platform_config.get_value("whatsapp_app_secret") == "typed-wrong"

    monkeypatch.setenv("WHATSAPP_APP_SECRET", "from-environment")
    get_settings.cache_clear()

    await client.put(
        "/api/v1/admin/whatsapp", json={"whatsapp_app_secret": ""}, headers=auth(token)
    )
    assert await platform_config.get_value("whatsapp_app_secret") == "from-environment"

    get_settings.cache_clear()
    platform_config.clear_cache()


async def test_an_unknown_key_cannot_be_written(client):
    """The write path is an allowlist, so a crafted key cannot set anything else."""
    from app.services import platform_config

    token = await login(client, *ADMIN)
    r = await client.put(
        "/api/v1/admin/whatsapp",
        json={"jwt_secret": "attacker-controlled"},
        headers=auth(token),
    )
    # Unknown fields are dropped by the schema, so there is nothing to save.
    assert r.status_code == 400
    assert "jwt_secret" not in platform_config.MANAGED_KEYS


async def test_test_endpoint_reports_missing_credentials_rather_than_failing(client):
    """Setup is incremental; a half-filled form should say what is missing."""
    token = await login(client, *ADMIN)
    r = await client.post("/api/v1/admin/whatsapp/test", headers=auth(token))
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["ok"] is False
    assert "missing" in data["detail"].lower()
