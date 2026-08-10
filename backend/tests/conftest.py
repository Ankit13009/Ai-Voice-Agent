"""Test fixtures.

Each test gets a fresh in-memory database and two tenants, because the property
most worth protecting is that one tenant cannot see the other. A shared database
would let a leak in one test hide behind data another test created.

External services are never called: VAPI, Google and Meta are all patched. A
test that depends on a third party is a test that fails for reasons unrelated
to the code.
"""

import asyncio
from datetime import time
from typing import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.security import hash_password
from app.db.models import Base, Business, Language, User, UserRole


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture
async def db_engine():
    # A file-backed temp database rather than :memory:, because the app opens
    # more than one connection and each would otherwise get its own empty DB.
    import tempfile, pathlib

    tmp = pathlib.Path(tempfile.mkdtemp()) / "test.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp}", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def session_factory(db_engine):
    return async_sessionmaker(bind=db_engine, class_=AsyncSession, expire_on_commit=False)


def _make_business(slug: str, name: str, phone: str) -> Business:
    return Business(
        name=name,
        slug=slug,
        phone_number=phone,
        timezone="Asia/Kolkata",
        opens_at=time(9, 0),
        closes_at=time(18, 0),
        working_days=[1, 2, 3, 4, 5, 6],
        primary_language=Language.MIXED,
        labels={
            "customer_singular": "Patient",
            "customer_plural": "Patients",
            "staff_singular": "Doctor",
            "staff_plural": "Doctors",
            "booking_singular": "appointment",
            "booking_plural": "appointments",
        },
    )


@pytest_asyncio.fixture
async def tenants(session_factory) -> dict:
    """Two unrelated businesses, each with an owner. The heart of every isolation test."""
    async with session_factory() as db:
        alpha = _make_business("alpha-clinic", "Alpha Clinic", "+911100000001")
        beta = _make_business("beta-salon", "Beta Salon", "+911100000002")
        db.add_all([alpha, beta])
        await db.flush()

        users = {
            "alpha_owner": User(
                email="owner@alpha.in", password_hash=hash_password("alpha-password-1"),
                role=UserRole.OWNER, business_id=alpha.id, full_name="Alpha Owner",
            ),
            "beta_owner": User(
                email="owner@beta.in", password_hash=hash_password("beta-password-1"),
                role=UserRole.OWNER, business_id=beta.id, full_name="Beta Owner",
            ),
            "superadmin": User(
                email="admin@platform.in", password_hash=hash_password("admin-password-1"),
                role=UserRole.SUPERADMIN, business_id=None, full_name="Admin",
            ),
        }
        db.add_all(list(users.values()))
        await db.commit()

        return {
            "alpha_id": alpha.id,
            "beta_id": beta.id,
            "alpha_owner_id": users["alpha_owner"].id,
            "beta_owner_id": users["beta_owner"].id,
        }


@pytest_asyncio.fixture
async def client(session_factory, tenants, monkeypatch) -> AsyncIterator[AsyncClient]:
    """An HTTP client bound to the app, with the test database injected."""
    from app.core.middleware import reset_rate_limits
    from app.db.session import get_db
    from app.main import app

    # Counters are process-wide and would otherwise carry over between tests,
    # so a later test fails on a limit an earlier one consumed.
    reset_rate_limits()

    async def override_get_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db

    # No test should ever reach a third party.
    async def _no_vapi(*args, **kwargs):
        raise AssertionError("a test tried to call VAPI")

    monkeypatch.setattr("app.services.vapi._request", _no_vapi, raising=False)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()


async def login(client: AsyncClient, email: str, password: str) -> str:
    """Return an access token, failing loudly if the login itself broke."""
    response = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return response.json()["data"]["tokens"]["access_token"]


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}
