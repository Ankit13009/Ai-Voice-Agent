"""Demo data must not be able to reach a real database.

seed.py --demo creates invented clinics, invented patients and invented call
transcripts. It had no guard at all, so one command run against the wrong
DATABASE_URL would have put fictional patients into the database a paying client
reads, next to their real ones.
"""

import pytest


def test_the_seed_file_has_no_plausible_phone_numbers():
    """This repository is public, and a realistic number belongs to somebody."""
    import pathlib
    import re

    source = (pathlib.Path(__file__).resolve().parents[1] / "seed.py").read_text()
    numbers = set(re.findall(r"\+91\d{10}", source))

    plausible = [n for n in numbers if not n.startswith("+9199999")]
    assert not plausible, (
        f"seed.py contains numbers a real person could own: {plausible}. "
        "Use an obviously-invented prefix."
    )


@pytest.mark.asyncio
async def test_demo_refuses_when_the_database_already_has_a_business(
    session_factory, tenants
):
    """Invented clinics alongside real ones is worse than either alone."""
    import seed

    async with session_factory() as db:
        with pytest.raises(SystemExit) as raised:
            await seed._refuse_demo_if_unsafe(db)

    assert "already has businesses" in str(raised.value)


@pytest.mark.asyncio
async def test_demo_refuses_in_production(session_factory, monkeypatch):
    """The guard that matters: APP_ENV catches the deployed environment."""
    import seed
    from app.config import get_settings

    monkeypatch.setenv("APP_ENV", "production")
    get_settings.cache_clear()

    async with session_factory() as db:
        with pytest.raises(SystemExit) as raised:
            await seed._refuse_demo_if_unsafe(db)

    assert "production" in str(raised.value)
    get_settings.cache_clear()
