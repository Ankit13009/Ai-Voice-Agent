#!/usr/bin/env python
"""Take a full backup of the database to a local file.

Why this exists: every client's appointments, customers, call history and
calendar tokens live in one database, and the free hosting tier gives no backup
that we control. This data has already been lost once during development. If it
goes now it is a phone call to a clinic explaining their patient records are
gone, so a copy that lives somewhere else is not optional.

Deliberately not a GitHub Action: the repository is public, and Actions
artifacts on a public repository can be downloaded by anyone. A dump contains
patient names, phone numbers and call transcripts, so it must never go near it.

Output is one gzipped JSON file per run, holding every row of every table.
JSON rather than pg_dump so that restoring does not depend on a matching
Postgres version, and so the file can be read and checked by eye.

Usage:
    python backup.py                       # writes ./backups/backup-<utc>.json.gz
    python backup.py --out ~/Drive/backups # somewhere that syncs off this machine
    python backup.py --keep 30             # delete local backups older than 30
    python backup.py --verify <file>       # check a backup is readable and count rows

Restore is intentionally a separate, manual step: see restore_from_backup() at
the bottom. Nothing here writes to the database.
"""

import argparse
import asyncio
import gzip
import json
import pathlib
import sys
from datetime import date, datetime, time, timezone
from decimal import Decimal

from sqlalchemy import select

from app.db.models import Base
from app.db.session import SessionLocal

# Ordered so a restore can insert parents before children. Derived from the
# metadata rather than hand-listed, so a new table cannot be silently missed:
# that is exactly how waitlist_entries went unnoticed for weeks.
TABLES = list(Base.metadata.sorted_tables)


def _encode(value):
    """JSON has no date, decimal or bytes. Keep them round-trippable."""
    if isinstance(value, (datetime, date, time)):
        return {"__type__": "datetime", "value": value.isoformat()}
    if isinstance(value, Decimal):
        return {"__type__": "decimal", "value": str(value)}
    if isinstance(value, bytes):
        return {"__type__": "bytes", "value": value.hex()}
    raise TypeError(f"Cannot serialize {type(value).__name__}")


async def create_backup(out_dir: pathlib.Path) -> pathlib.Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = out_dir / f"backup-{stamp}.json.gz"

    payload: dict = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "format": 1,
        "tables": {},
    }

    async with SessionLocal() as db:
        for table in TABLES:
            rows = (await db.execute(select(table))).mappings().all()
            payload["tables"][table.name] = [dict(row) for row in rows]
            print(f"  {table.name:24} {len(rows):>6} rows")

    # Written compressed: transcripts dominate the size and compress heavily.
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        json.dump(payload, handle, default=_encode)

    total = sum(len(rows) for rows in payload["tables"].values())
    size_kb = path.stat().st_size / 1024
    print(f"\n  {total} rows across {len(TABLES)} tables -> {path} ({size_kb:.0f} KB)")
    return path


def verify_backup(path: pathlib.Path) -> bool:
    """Read a backup back.

    A backup nobody has ever read is a guess. This is cheap enough to run on
    every backup, and catches truncation or a half-written file before it is
    the only copy left.
    """
    try:
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            payload = json.load(handle)
    except Exception as exc:  # noqa: BLE001
        print(f"  UNREADABLE: {exc}")
        return False

    tables = payload.get("tables", {})
    missing = [t.name for t in TABLES if t.name not in tables]
    total = sum(len(rows) for rows in tables.values())

    print(f"  created_at : {payload.get('created_at')}")
    print(f"  tables     : {len(tables)}")
    print(f"  rows       : {total}")
    if missing:
        print(f"  MISSING TABLES: {', '.join(missing)}")
        return False

    # An empty businesses table means the backup captured nothing that matters,
    # which is worth failing on rather than reporting a healthy-looking file.
    if not tables.get("businesses"):
        print("  WARNING: no businesses in this backup.")
        return False

    print("  readable, and every table is present.")
    return True


def prune(out_dir: pathlib.Path, keep: int) -> None:
    backups = sorted(out_dir.glob("backup-*.json.gz"))
    for old in backups[:-keep] if keep > 0 else []:
        old.unlink()
        print(f"  removed old backup {old.name}")


async def restore_from_backup(path: pathlib.Path) -> None:
    """Not wired to the CLI on purpose.

    Restoring overwrites live data, and a flag that does that is a flag someone
    eventually runs against the wrong database. When a restore is genuinely
    needed, import this function deliberately.
    """
    raise NotImplementedError(
        "Restore is manual by design. Read the backup with json.load, then "
        "insert per table in Base.metadata.sorted_tables order."
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Back up the database to a file.")
    parser.add_argument("--out", default="backups", help="Directory to write to.")
    parser.add_argument("--keep", type=int, default=14, help="How many to retain locally.")
    parser.add_argument("--verify", metavar="FILE", help="Verify an existing backup and exit.")
    args = parser.parse_args()

    if args.verify:
        return 0 if verify_backup(pathlib.Path(args.verify)) else 1

    out_dir = pathlib.Path(args.out).expanduser()
    path = asyncio.run(create_backup(out_dir))

    print("\nVerifying:")
    if not verify_backup(path):
        print("\nBackup FAILED verification. Keeping the file for inspection.")
        return 1

    prune(out_dir, args.keep)
    return 0


if __name__ == "__main__":
    sys.exit(main())
