#!/usr/bin/env python
"""Create the first superadmin.

Usage:
    python seed.py --email you@yourdomain.in --password 'a-strong-password'

This exists for one reason: a brand-new database has no accounts, so there is
nothing to sign in with and no way to create the first one through the
dashboard. Every account after this one is created in the dashboard.

No credentials live in this file. Both are required arguments with no defaults,
so running it cannot produce a predictable account.

Businesses are created through the onboarding form, not here. A previous version
seeded an invented clinic and salon, complete with fabricated patients and call
transcripts, to fill a local dashboard. It is gone: this repository is public,
invented patient records read badly whatever the intent, and one run against the
wrong DATABASE_URL would have put them in a database a client reads.
"""

import argparse
import asyncio
import sys
from datetime import datetime, time, timedelta, timezone

from sqlalchemy import select

from app.agent.presets import get_preset
from app.core.security import hash_password
from app.db.models import (
    Appointment,
    AppointmentStatus,
    AppointmentType,
    Business,
    Call,
    CallOutcome,
    Customer,
    Language,
    StaffMember,
    User,
    UserRole,
)
from app.db.session import SessionLocal, init_db


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Seed the first superadmin.")
    parser.add_argument("--email", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--name", default="Platform Admin")
    return parser.parse_args()


async def create_superadmin(db, args) -> User:
    email = args.email.lower()
    existing = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
    if existing:
        print(f"User {email} already exists; leaving it unchanged.")
        return existing

    if len(args.password) < 10:
        sys.exit("Password must be at least 10 characters.")

    user = User(
        email=email,
        password_hash=hash_password(args.password),
        full_name=args.name,
        role=UserRole.SUPERADMIN,
        business_id=None,
    )
    db.add(user)
    await db.flush()
    print(f"Created superadmin {email}")
    return user





async def main() -> None:
    args = parse_args()
    await init_db()
    async with SessionLocal() as db:
        await create_superadmin(db, args)
        await db.commit()
    print("\nSeed complete.")


if __name__ == "__main__":
    asyncio.run(main())
