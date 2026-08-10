"""Backups.

A backup nobody has read is a guess, so these check the two things that make
one worth having: that every table is captured, and that the file can be read
back. The table list is derived from the metadata rather than hand-written
precisely so a newly added table cannot be silently left out of backups, which
is the same failure that let waitlist_entries ship with no migration.
"""

import gzip
import json
import pathlib

import pytest

import backup as backup_module
from app.db.models import Base

pytestmark = pytest.mark.asyncio


async def test_backup_captures_every_table(tmp_path, session_factory, tenants, monkeypatch):
    monkeypatch.setattr(backup_module, "SessionLocal", session_factory)

    path = await backup_module.create_backup(tmp_path)

    with gzip.open(path, "rt", encoding="utf-8") as handle:
        payload = json.load(handle)

    captured = set(payload["tables"])
    expected = {t.name for t in Base.metadata.sorted_tables}
    assert captured == expected, f"tables missing from backup: {expected - captured}"

    # The seeded tenants must actually be in there, not just an empty shell.
    names = [b["name"] for b in payload["tables"]["businesses"]]
    assert len(names) >= 2


async def test_backup_verifies_and_round_trips_dates(
    tmp_path, session_factory, tenants, monkeypatch
):
    """Datetimes must survive: they are the whole value of an appointments table."""
    monkeypatch.setattr(backup_module, "SessionLocal", session_factory)

    path = await backup_module.create_backup(tmp_path)
    assert backup_module.verify_backup(path) is True

    with gzip.open(path, "rt", encoding="utf-8") as handle:
        payload = json.load(handle)

    created = payload["tables"]["businesses"][0]["created_at"]
    assert created["__type__"] == "datetime"
    assert created["value"].startswith("20")


async def test_verify_rejects_a_truncated_file(tmp_path, session_factory, monkeypatch):
    """The failure mode that matters: a half-written file that looks fine."""
    monkeypatch.setattr(backup_module, "SessionLocal", session_factory)
    path = await backup_module.create_backup(tmp_path)

    raw = path.read_bytes()
    path.write_bytes(raw[: len(raw) // 2])

    assert backup_module.verify_backup(path) is False


async def test_verify_rejects_a_backup_with_no_businesses(tmp_path):
    """An empty backup is worse than none: it reports success and holds nothing."""
    empty = tmp_path / "backup-empty.json.gz"
    payload = {
        "created_at": "2026-01-01T00:00:00Z",
        "format": 1,
        "tables": {t.name: [] for t in Base.metadata.sorted_tables},
    }
    with gzip.open(empty, "wt", encoding="utf-8") as handle:
        json.dump(payload, handle)

    assert backup_module.verify_backup(empty) is False


async def test_prune_keeps_only_the_newest(tmp_path):
    for name in ("backup-20260101T000000Z", "backup-20260102T000000Z", "backup-20260103T000000Z"):
        (tmp_path / f"{name}.json.gz").write_bytes(b"x")

    backup_module.prune(tmp_path, keep=2)

    remaining = sorted(p.name for p in tmp_path.glob("backup-*.json.gz"))
    assert remaining == ["backup-20260102T000000Z.json.gz", "backup-20260103T000000Z.json.gz"]
