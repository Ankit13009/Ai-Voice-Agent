"""Assistant sync against VAPI.

The case worth pinning is a stored assistant id that is not in the account the
current API key points at. That happens whenever a deployment moves to a
client's own VAPI account, and whenever someone deletes an assistant in the
dashboard. Before this was handled, every settings save 404'd forever and the
business could not get a working assistant back without editing the database.
"""

import httpx
import pytest

from app.services import vapi


@pytest.fixture(autouse=True)
def _vapi_key(monkeypatch):
    """Supply a fake API key so these tests never depend on a real .env.

    Without this they pass when run from the backend directory, where a
    developer's .env happens to be loaded, and fail anywhere else including CI.
    """
    from app.config import get_settings

    monkeypatch.setenv("VAPI_API_KEY", "test-key")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


class _Response:
    def __init__(self, status_code: int, payload: dict | None = None):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = str(self._payload)
        self.content = b"x" if self._payload else b""

    def json(self) -> dict:
        return self._payload


@pytest.mark.asyncio
async def test_update_recreates_an_assistant_missing_from_the_account(
    monkeypatch, session_factory, tenants
):
    from sqlalchemy import select
    from app.db.models import Business

    calls: list[tuple[str, str]] = []

    async def fake_request(self, method, url, **kwargs):
        calls.append((method, url))
        if method == "PATCH":
            return _Response(404, {"message": "Assistant not found"})
        return _Response(201, {"id": "new-assistant-id"})

    monkeypatch.setattr(httpx.AsyncClient, "request", fake_request)

    async with session_factory() as db:
        business = (
            await db.execute(select(Business).where(Business.id == tenants["alpha_id"]))
        ).scalar_one()
        business.vapi_assistant_id = "id-from-the-old-account"

        new_id = await vapi.update_assistant(business, [])

    assert new_id == "new-assistant-id"
    assert [m for m, _ in calls] == ["PATCH", "POST"], "should PATCH, then create on 404"


@pytest.mark.asyncio
async def test_update_in_place_returns_no_new_id(monkeypatch, session_factory, tenants):
    """The normal path must not create a second assistant."""
    from sqlalchemy import select
    from app.db.models import Business

    calls: list[str] = []

    async def fake_request(self, method, url, **kwargs):
        calls.append(method)
        return _Response(200, {"id": "existing"})

    monkeypatch.setattr(httpx.AsyncClient, "request", fake_request)

    async with session_factory() as db:
        business = (
            await db.execute(select(Business).where(Business.id == tenants["alpha_id"]))
        ).scalar_one()
        business.vapi_assistant_id = "existing"

        assert await vapi.update_assistant(business, []) == ""

    assert calls == ["PATCH"], "must not POST when the assistant exists"


@pytest.mark.asyncio
async def test_a_real_outage_is_not_mistaken_for_a_missing_assistant(
    monkeypatch, session_factory, tenants
):
    """A 500 must propagate, not silently spawn a duplicate assistant."""
    from sqlalchemy import select
    from app.core.errors import UpstreamError
    from app.db.models import Business

    async def fake_request(self, method, url, **kwargs):
        return _Response(500, {"message": "internal"})

    monkeypatch.setattr(httpx.AsyncClient, "request", fake_request)

    async with session_factory() as db:
        business = (
            await db.execute(select(Business).where(Business.id == tenants["alpha_id"]))
        ).scalar_one()
        business.vapi_assistant_id = "existing"

        with pytest.raises(UpstreamError):
            await vapi.update_assistant(business, [])
