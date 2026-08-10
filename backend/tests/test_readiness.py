"""The readiness probe.

/health answers "is the process alive", which Render uses to judge a deploy.
/health/ready answers "would a real caller be served right now", which is what
an uptime monitor needs. The distinction matters: without it, a monitor watching
/health reports everything healthy while the database is unreachable and every
call is failing.
"""

import pytest

pytestmark = pytest.mark.asyncio


async def test_ready_when_the_database_answers(client):
    r = await client.get("/health/ready")
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["status"] == "ready"
    assert data["checks"]["database"]["ok"] is True


async def test_liveness_stays_simple(client):
    """/health must not depend on the database, or a blip fails the deploy."""
    r = await client.get("/health")
    assert r.status_code == 200
    assert r.json()["data"]["status"] == "ok"


async def test_returns_503_when_the_database_is_unreachable(client, monkeypatch):
    """The failure that matters: a monitor must see a non-200, not a cheerful body."""
    import app.main as main_module

    class _Boom:
        def __call__(self):
            return self

        async def __aenter__(self):
            raise ConnectionRefusedError("database is down")

        async def __aexit__(self, *args):
            return False

    monkeypatch.setattr("app.db.session.SessionLocal", _Boom())

    r = await client.get("/health/ready")
    assert r.status_code == 503, "an uptime monitor keys on the status code"
