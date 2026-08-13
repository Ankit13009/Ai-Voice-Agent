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


# --------------------------------------------------------------------------- #
# Tool calls and inbound replies, past the signature check
# --------------------------------------------------------------------------- #
def _vapi_tool_call(name: str, arguments: dict, *, dialed: str, caller: str) -> dict:
    """A tool-call payload in VAPI's shape.

    The business is deliberately identified by the dialed number rather than an
    argument: the model relays whatever the caller says, so a business id in the
    arguments would be a prompt-injection path straight through tenant isolation.
    """
    return {
        "message": {
            "type": "tool-calls",
            "call": {
                "id": "call-1",
                "phoneNumber": {"number": dialed},
                "customer": {"number": caller},
            },
            "toolCallList": [
                {"id": "tc-1", "function": {"name": name, "arguments": arguments}}
            ],
        }
    }


async def test_join_waitlist_records_the_caller(client, tenants, session_factory, monkeypatch):
    """Nothing was free, so the caller goes on the list instead of being lost.

    This went through the signature check and then failed on a function name that
    did not exist, so the caller heard "something went wrong" and the whole
    never-end-empty-handed path was dead. Driven through the real endpoint,
    because that is what a name error survives.
    """
    from datetime import date, timedelta

    from sqlalchemy import select

    from app.config import get_settings
    from app.db.models import Customer, WaitlistEntry

    monkeypatch.setenv("VAPI_WEBHOOK_SECRET", "tool-secret")
    get_settings.cache_clear()

    wanted = date.today() + timedelta(days=3)
    r = await client.post(
        "/webhooks/vapi/tool",
        json=_vapi_tool_call(
            "join_waitlist",
            {
                "customer_name": "Anita Rao",
                "date_from": wanted.isoformat(),
                "date_to": (wanted + timedelta(days=2)).isoformat(),
                "reason": "follow-up",
            },
            dialed="+911100000001",
            caller="+919876511111",
        ),
        headers={"X-Vapi-Secret": "tool-secret"},
    )

    assert r.status_code == 200, r.text
    result = r.json()["results"][0]["result"]
    assert result["status"] == "success", result

    async with session_factory() as db:
        entries = (await db.execute(select(WaitlistEntry))).scalars().all()
        customers = (await db.execute(select(Customer))).scalars().all()

    assert len(entries) == 1
    assert entries[0].business_id == tenants["alpha_id"]
    assert [c.phone for c in customers] == ["+919876511111"]


async def test_whatsapp_cancel_works_for_a_customer_with_two_appointments(
    client, tenants, session_factory, monkeypatch
):
    """A returning customer could not cancel by replying CANCEL.

    Their business was looked up by joining through their appointments, and two
    appointments returned the same business twice, which the ambiguity guard read
    as two different businesses and refused to act on. The guard itself is right —
    cancelling the wrong business's appointment is worse than doing nothing — it
    was only counting rows instead of businesses.
    """
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import select

    from app.config import get_settings
    from app.db.models import Appointment, AppointmentStatus, Customer

    monkeypatch.setenv("WHATSAPP_APP_SECRET", "cancel-secret")
    get_settings.cache_clear()

    now = datetime.now(timezone.utc)
    async with session_factory() as db:
        customer = Customer(
            business_id=tenants["alpha_id"], phone="+919876522222", name="Repeat Caller"
        )
        db.add(customer)
        await db.flush()
        for days in (2, 9):
            db.add(
                Appointment(
                    business_id=tenants["alpha_id"],
                    customer_id=customer.id,
                    starts_at=now + timedelta(days=days),
                    ends_at=now + timedelta(days=days, minutes=15),
                    status=AppointmentStatus.SCHEDULED,
                )
            )
        await db.commit()

    body = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "metadata": {},
                            "messages": [
                                {
                                    "type": "text",
                                    "from": "919876522222",
                                    "text": {"body": "CANCEL"},
                                }
                            ],
                        }
                    }
                ]
            }
        ]
    }
    raw = json.dumps(body).encode()
    signature = hmac.new(b"cancel-secret", raw, hashlib.sha256).hexdigest()

    r = await client.post(
        "/webhooks/whatsapp",
        content=raw,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": f"sha256={signature}",
        },
    )
    assert r.status_code == 200, r.text

    async with session_factory() as db:
        statuses = (
            await db.execute(
                select(Appointment.status).order_by(Appointment.starts_at.asc())
            )
        ).scalars().all()

    # The next one is cancelled; the later one is left alone.
    assert statuses == [AppointmentStatus.CANCELLED, AppointmentStatus.SCHEDULED]
