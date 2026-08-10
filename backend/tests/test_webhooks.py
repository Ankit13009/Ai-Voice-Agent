"""Webhook authentication.

These endpoints take no JWT, so the signature check is the only thing between an
attacker who learns the URL and the ability to book, move and cancel
appointments for any business.
"""

import hashlib
import hmac
import json

import pytest

pytestmark = pytest.mark.asyncio


async def test_vapi_webhook_rejects_missing_and_wrong_secret(client):
    payload = {"message": {"type": "tool-calls", "call": {"id": "x"}, "toolCallList": []}}

    r = await client.post("/webhooks/vapi/tool", json=payload)
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "WEBHOOK_SIGNATURE_INVALID"

    r = await client.post("/webhooks/vapi/tool", json=payload, headers={"X-Vapi-Secret": "guessed"})
    assert r.status_code == 403


async def test_whatsapp_webhook_requires_a_valid_signature(client, monkeypatch):
    from app.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("WHATSAPP_APP_SECRET", "test-app-secret")
    get_settings.cache_clear()

    body = {"entry": []}
    raw = json.dumps(body).encode()

    r = await client.post("/webhooks/whatsapp", json=body)
    assert r.status_code == 403

    r = await client.post(
        "/webhooks/whatsapp", content=raw,
        headers={"Content-Type": "application/json", "X-Hub-Signature-256": "sha256=deadbeef"},
    )
    assert r.status_code == 403

    good = hmac.new(b"test-app-secret", raw, hashlib.sha256).hexdigest()
    r = await client.post(
        "/webhooks/whatsapp", content=raw,
        headers={"Content-Type": "application/json", "X-Hub-Signature-256": f"sha256={good}"},
    )
    assert r.status_code == 200
    get_settings.cache_clear()


async def test_whatsapp_verification_handshake_requires_the_token(client, monkeypatch):
    from app.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("WHATSAPP_VERIFY_TOKEN", "the-right-token")
    get_settings.cache_clear()

    r = await client.get("/webhooks/whatsapp", params={
        "hub.mode": "subscribe", "hub.challenge": "12345", "hub.verify_token": "wrong"})
    assert r.status_code == 403

    r = await client.get("/webhooks/whatsapp", params={
        "hub.mode": "subscribe", "hub.challenge": "12345", "hub.verify_token": "the-right-token"})
    assert r.status_code == 200
    assert r.text == "12345"
    get_settings.cache_clear()


async def test_rate_limit_blocks_repeated_login_attempts(client):
    """The limiter is a real control, so it gets a real test."""
    seen_429 = False
    for _ in range(15):
        r = await client.post(
            "/api/v1/auth/login", json={"email": "owner@alpha.in", "password": "wrong-password-1"}
        )
        if r.status_code == 429:
            seen_429 = True
            assert "Retry-After" in r.headers
            break
    assert seen_429, "login should be rate limited"
