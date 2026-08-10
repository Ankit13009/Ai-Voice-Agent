"""Tenant isolation.

These are the most important tests in the codebase. Everything else failing is
a bug; these failing is a data breach across two paying clients.

They exist because isolation was verified by hand during development, and a
manual check protects nothing against the next refactor. The wide rename from
clinic/patient/doctor to business/customer/staff touched forty files and could
easily have broken one filter.
"""

import pytest

from tests.conftest import auth, login

pytestmark = pytest.mark.asyncio


async def test_cannot_read_another_tenants_appointment(client, tenants, session_factory):
    from datetime import datetime, timedelta, timezone

    from app.db.models import Appointment, AppointmentStatus, Customer

    async with session_factory() as db:
        customer = Customer(business_id=tenants["beta_id"], name="Beta Patient", phone="+919000000001")
        db.add(customer)
        await db.flush()
        appointment = Appointment(
            business_id=tenants["beta_id"],
            customer_id=customer.id,
            starts_at=datetime.now(timezone.utc) + timedelta(days=1),
            ends_at=datetime.now(timezone.utc) + timedelta(days=1, minutes=15),
            status=AppointmentStatus.SCHEDULED,
            reason="CONFIDENTIAL",
        )
        db.add(appointment)
        await db.commit()
        beta_appointment_id = appointment.id
        beta_customer_id = customer.id

    token = await login(client, "owner@alpha.in", "alpha-password-1")

    # 404 rather than 403: confirming the id exists would itself leak that beta
    # has an appointment with that identifier.
    r = await client.get(f"/api/v1/appointments/{beta_appointment_id}", headers=auth(token))
    assert r.status_code == 404
    assert "CONFIDENTIAL" not in r.text

    r = await client.get(f"/api/v1/customers/{beta_customer_id}", headers=auth(token))
    assert r.status_code == 404


async def test_business_id_query_param_cannot_override_the_token(client, tenants):
    """The classic multi-tenant hole: trusting a client-supplied tenant id."""
    token = await login(client, "owner@alpha.in", "alpha-password-1")

    r = await client.get(
        "/api/v1/calls", params={"business_id": tenants["beta_id"]}, headers=auth(token)
    )
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "BUSINESS_ACCESS_DENIED"


async def test_cannot_write_to_another_tenants_settings(client, tenants):
    token = await login(client, "owner@alpha.in", "alpha-password-1")

    r = await client.patch(
        "/api/v1/businesses/me",
        params={"business_id": tenants["beta_id"]},
        json={"name": "pwned"},
        headers=auth(token),
    )
    assert r.status_code == 403

    # And beta is genuinely untouched, not merely reported as refused.
    beta_token = await login(client, "owner@beta.in", "beta-password-1")
    r = await client.get("/api/v1/businesses/me", headers=auth(beta_token))
    assert r.json()["data"]["name"] == "Beta Salon"


async def test_listing_endpoints_never_include_other_tenants(client, tenants, session_factory):
    from app.db.models import Customer

    async with session_factory() as db:
        db.add(Customer(business_id=tenants["beta_id"], name="Beta Only", phone="+919000000002"))
        db.add(Customer(business_id=tenants["alpha_id"], name="Alpha Only", phone="+919000000003"))
        await db.commit()

    token = await login(client, "owner@alpha.in", "alpha-password-1")
    r = await client.get("/api/v1/customers", headers=auth(token))
    names = [c["name"] for c in r.json()["data"]]

    assert "Alpha Only" in names
    assert "Beta Only" not in names


async def test_business_owner_cannot_reach_admin_endpoints(client, tenants):
    token = await login(client, "owner@alpha.in", "alpha-password-1")

    for path in ("/api/v1/admin/businesses", "/api/v1/admin/stats",
                 "/api/v1/onboarding/business-types"):
        r = await client.get(path, headers=auth(token))
        assert r.status_code == 403, path
        assert r.json()["error"]["code"] == "INSUFFICIENT_ROLE"


async def test_owner_cannot_reset_another_tenants_user_password(client, tenants):
    """Password reset is scoped, or it becomes account takeover across tenants."""
    token = await login(client, "owner@alpha.in", "alpha-password-1")

    r = await client.post(
        f"/api/v1/businesses/me/users/{tenants['beta_owner_id']}/reset-password",
        headers=auth(token),
    )
    assert r.status_code == 404

    # Beta's password still works.
    await login(client, "owner@beta.in", "beta-password-1")


async def test_superadmin_must_name_a_tenant(client, tenants):
    """A superadmin has no implicit tenant, so acting without naming one is refused
    rather than silently defaulting to the first business in the table."""
    token = await login(client, "admin@platform.in", "admin-password-1")

    r = await client.get("/api/v1/businesses/me", headers=auth(token))
    assert r.status_code == 403

    r = await client.get(
        "/api/v1/businesses/me", params={"business_id": tenants["alpha_id"]}, headers=auth(token)
    )
    assert r.status_code == 200
    assert r.json()["data"]["name"] == "Alpha Clinic"
