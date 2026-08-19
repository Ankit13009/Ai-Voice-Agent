"""A one-time password must not work forever.

Every created user and every reset sets must_change_password, the dashboard
shows it as a badge, and login returns it. Nothing checked it, so a password
read aloud over the phone kept working indefinitely, which is the one thing a
one-time password exists to prevent. The settings screen told operators the
user "will be asked to change it when they sign in", which was untrue.

Enforced in the auth dependency rather than per endpoint, so a route added
later cannot forget.
"""

import pytest

from tests.conftest import auth, login

pytestmark = pytest.mark.asyncio

OWNER = ("owner@alpha.in", "alpha-password-1")


async def _staff_with_temporary_password(client) -> tuple[str, str]:
    owner_token = await login(client, *OWNER)
    created = await client.post(
        "/api/v1/businesses/me/users",
        json={"email": "new-desk@alpha.in", "full_name": "Desk", "role": "staff"},
        headers=auth(owner_token),
    )
    temporary = created.json()["data"]["temporary_password"]
    return temporary, await login(client, "new-desk@alpha.in", temporary)


async def test_a_one_time_password_cannot_be_used_to_work(client, tenants):
    """The whole point: it signs you in, and then stops."""
    _temporary, token = await _staff_with_temporary_password(client)

    r = await client.get("/api/v1/appointments", headers=auth(token))
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "PASSWORD_CHANGE_REQUIRED"


async def test_the_user_can_still_see_themselves_and_change_it(client, tenants):
    """Blocking everything would leave them unable to fix it."""
    temporary, token = await _staff_with_temporary_password(client)

    assert (await client.get("/api/v1/auth/me", headers=auth(token))).status_code == 200

    changed = await client.post(
        "/api/v1/auth/change-password",
        json={"current_password": temporary, "new_password": "a-real-password-1"},
        headers=auth(token),
    )
    assert changed.status_code == 200, changed.text


async def test_everything_works_once_the_password_is_replaced(client, tenants):
    temporary, token = await _staff_with_temporary_password(client)
    await client.post(
        "/api/v1/auth/change-password",
        json={"current_password": temporary, "new_password": "a-real-password-1"},
        headers=auth(token),
    )

    fresh = await login(client, "new-desk@alpha.in", "a-real-password-1")
    assert (await client.get("/api/v1/appointments", headers=auth(fresh))).status_code == 200


async def test_an_admin_reset_re_arms_the_requirement(client, tenants):
    """A reset issues a new one-time password, so it must block again."""
    temporary, token = await _staff_with_temporary_password(client)
    await client.post(
        "/api/v1/auth/change-password",
        json={"current_password": temporary, "new_password": "a-real-password-1"},
        headers=auth(token),
    )

    admin_token = await login(client, "admin@platform.in", "admin-password-1")
    users = await client.get(
        f"/api/v1/admin/businesses/{tenants['alpha_id']}/users", headers=auth(admin_token)
    )
    user_id = next(
        u["id"] for u in users.json()["data"]["users"] if u["email"] == "new-desk@alpha.in"
    )
    reset = await client.post(
        f"/api/v1/admin/users/{user_id}/reset-password", headers=auth(admin_token)
    )
    new_temporary = reset.json()["data"]["temporary_password"]

    blocked = await login(client, "new-desk@alpha.in", new_temporary)
    r = await client.get("/api/v1/appointments", headers=auth(blocked))
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "PASSWORD_CHANGE_REQUIRED"
