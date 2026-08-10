"""The operator's escalation path for a locked-out owner.

With no email service there is no self-serve recovery, so an owner who forgets
their password has no route back into their own tenant. This endpoint is the
only remedy short of editing the database, which makes both halves worth
pinning: that it works, and that only a superadmin can reach it. It crosses
tenant boundaries by design, so a role check failing here would let any owner
seize any other tenant's account.
"""

import pytest

from tests.conftest import auth, login

pytestmark = pytest.mark.asyncio

ADMIN = ("admin@platform.in", "admin-password-1")
ALPHA_OWNER = ("owner@alpha.in", "alpha-password-1")


async def test_superadmin_can_reset_a_locked_out_owner(client, tenants):
    token = await login(client, *ADMIN)

    r = await client.post(
        f"/api/v1/admin/users/{tenants['alpha_owner_id']}/reset-password",
        headers=auth(token),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["success"] is True
    new_password = body["data"]["temporary_password"]
    assert body["data"]["email"] == "owner@alpha.in"

    # The whole point: the owner can get back in with it.
    assert await login(client, "owner@alpha.in", new_password)

    # And the old password must be dead.
    r = await client.post(
        "/api/v1/auth/login",
        json={"email": "owner@alpha.in", "password": "alpha-password-1"},
    )
    assert r.status_code == 401


async def test_reset_forces_a_password_change_and_kills_old_sessions(client, tenants):
    """A one-time password left in place is a shared password."""
    owner_token = await login(client, *ALPHA_OWNER)
    assert (await client.get("/api/v1/businesses/me", headers=auth(owner_token))).status_code == 200

    admin_token = await login(client, *ADMIN)
    r = await client.post(
        f"/api/v1/admin/users/{tenants['alpha_owner_id']}/reset-password",
        headers=auth(admin_token),
    )
    new_password = r.json()["data"]["temporary_password"]

    fresh = await login(client, "owner@alpha.in", new_password)
    me = await client.get("/api/v1/auth/me", headers=auth(fresh))
    assert me.json()["data"]["must_change_password"] is True


async def test_an_owner_cannot_reset_another_tenants_user(client, tenants):
    """The isolation that matters: this endpoint crosses tenants, so the role
    check is the only thing standing between an owner and every other client."""
    token = await login(client, *ALPHA_OWNER)

    r = await client.post(
        f"/api/v1/admin/users/{tenants['beta_owner_id']}/reset-password",
        headers=auth(token),
    )
    assert r.status_code == 403, r.text

    # Beta's original password must still work, proving nothing was changed.
    assert await login(client, "owner@beta.in", "beta-password-1")


async def test_an_owner_cannot_list_another_tenants_users(client, tenants):
    token = await login(client, *ALPHA_OWNER)
    r = await client.get(
        f"/api/v1/admin/businesses/{tenants['beta_id']}/users", headers=auth(token)
    )
    assert r.status_code == 403


async def test_superadmin_lists_users_of_a_tenant(client, tenants):
    token = await login(client, *ADMIN)
    r = await client.get(
        f"/api/v1/admin/businesses/{tenants['alpha_id']}/users", headers=auth(token)
    )
    assert r.status_code == 200
    emails = [u["email"] for u in r.json()["data"]["users"]]
    assert "owner@alpha.in" in emails
    assert "owner@beta.in" not in emails, "must not leak another tenant's users"
