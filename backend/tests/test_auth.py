"""Authentication: lockout, token handling, password reset, and the response envelope."""

import pytest

from tests.conftest import auth, login

pytestmark = pytest.mark.asyncio


async def test_every_response_uses_the_standard_envelope(client):
    """The contract the frontend's discriminated union depends on."""
    r = await client.get("/health")
    body = r.json()
    assert body["success"] is True
    assert set(body) == {"success", "data", "meta", "message", "request_id", "timestamp"}
    assert body["request_id"], "request_id must be populated for support traceability"

    r = await client.post("/api/v1/auth/login", json={"email": "no", "password": ""})
    body = r.json()
    assert body["success"] is False
    assert set(body) == {"success", "error", "request_id", "timestamp"}
    assert set(body["error"]) == {"code", "message", "details"}
    # data and error must never both appear, or the union is not a union.
    assert "data" not in body


async def test_login_does_not_reveal_whether_an_account_exists(client):
    unknown = await client.post(
        "/api/v1/auth/login", json={"email": "nobody@nowhere.in", "password": "whatever123"}
    )
    wrong = await client.post(
        "/api/v1/auth/login", json={"email": "owner@alpha.in", "password": "wrong-password-1"}
    )
    assert unknown.status_code == wrong.status_code == 401
    assert unknown.json()["error"]["message"] == wrong.json()["error"]["message"]


async def test_account_locks_after_repeated_failures(client):
    from app.api.v1.auth import MAX_FAILED_ATTEMPTS

    for _ in range(MAX_FAILED_ATTEMPTS):
        r = await client.post(
            "/api/v1/auth/login", json={"email": "owner@alpha.in", "password": "wrong-password-1"}
        )
        assert r.status_code == 401

    # Even the CORRECT password is now refused. A lockout that the real password
    # can bypass protects nothing.
    r = await client.post(
        "/api/v1/auth/login", json={"email": "owner@alpha.in", "password": "alpha-password-1"}
    )
    assert r.status_code == 401
    assert "Too many failed attempts" in r.json()["error"]["message"]


async def test_successful_login_clears_the_failure_counter(client, session_factory):
    from sqlalchemy import select

    from app.db.models import User

    for _ in range(2):
        await client.post(
            "/api/v1/auth/login", json={"email": "owner@alpha.in", "password": "wrong-password-1"}
        )
    await login(client, "owner@alpha.in", "alpha-password-1")

    async with session_factory() as db:
        user = (
            await db.execute(select(User).where(User.email == "owner@alpha.in"))
        ).scalar_one()
        assert user.failed_login_attempts == 0
        assert user.locked_until is None


async def test_protected_routes_reject_missing_and_malformed_tokens(client):
    r = await client.get("/api/v1/businesses/me")
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "UNAUTHENTICATED"

    r = await client.get("/api/v1/businesses/me", headers=auth("not-a-real-token"))
    assert r.status_code == 401


async def test_refresh_token_is_rejected_where_an_access_token_is_required(client):
    """Otherwise a stolen long-lived refresh token becomes a permanent API key."""
    r = await client.post(
        "/api/v1/auth/login", json={"email": "owner@alpha.in", "password": "alpha-password-1"}
    )
    refresh = r.json()["data"]["tokens"]["refresh_token"]

    r = await client.get("/api/v1/businesses/me", headers=auth(refresh))
    assert r.status_code == 401


async def test_refresh_token_is_single_use_and_reuse_revokes_the_family(client):
    r = await client.post(
        "/api/v1/auth/login", json={"email": "owner@alpha.in", "password": "alpha-password-1"}
    )
    original = r.json()["data"]["tokens"]["refresh_token"]

    first = await client.post("/api/v1/auth/refresh", json={"refresh_token": original})
    assert first.status_code == 200

    # Replaying the original is treated as theft.
    replay = await client.post("/api/v1/auth/refresh", json={"refresh_token": original})
    assert replay.status_code == 401

    # ...and the token issued from it is revoked too, so an attacker who raced
    # ahead does not keep a working session.
    rotated = first.json()["data"]["tokens"]["refresh_token"]
    after = await client.post("/api/v1/auth/refresh", json={"refresh_token": rotated})
    assert after.status_code == 401


async def test_password_reset_issues_a_one_time_password_and_kills_sessions(client, tenants):
    owner_token = await login(client, "owner@alpha.in", "alpha-password-1")

    created = await client.post(
        "/api/v1/businesses/me/users",
        json={"email": "staff@alpha.in", "full_name": "Front Desk", "role": "staff"},
        headers=auth(owner_token),
    )
    assert created.status_code == 201
    staff_id = created.json()["data"]["id"]
    first_password = created.json()["data"]["temporary_password"]

    staff_login = await client.post(
        "/api/v1/auth/login", json={"email": "staff@alpha.in", "password": first_password}
    )
    assert staff_login.status_code == 200
    assert staff_login.json()["data"]["user"]["must_change_password"] is True
    old_refresh = staff_login.json()["data"]["tokens"]["refresh_token"]

    reset = await client.post(
        f"/api/v1/businesses/me/users/{staff_id}/reset-password", headers=auth(owner_token)
    )
    assert reset.status_code == 200
    new_password = reset.json()["data"]["temporary_password"]
    assert new_password != first_password

    # The old password is dead, the new one works, and the old session is gone.
    assert (
        await client.post(
            "/api/v1/auth/login", json={"email": "staff@alpha.in", "password": first_password}
        )
    ).status_code == 401
    assert (
        await client.post(
            "/api/v1/auth/login", json={"email": "staff@alpha.in", "password": new_password}
        )
    ).status_code == 200
    assert (
        await client.post("/api/v1/auth/refresh", json={"refresh_token": old_refresh})
    ).status_code == 401


async def test_password_response_never_contains_the_hash(client):
    r = await client.post(
        "/api/v1/auth/login", json={"email": "owner@alpha.in", "password": "alpha-password-1"}
    )
    assert "password_hash" not in r.text
    assert "$2b$" not in r.text
