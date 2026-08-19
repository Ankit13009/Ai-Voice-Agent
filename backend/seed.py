#!/usr/bin/env python
"""Create the first superadmin, and optionally two demo businesses.

Usage:
    python seed.py --email you@yourdomain.in --password 'a-strong-password'
    python seed.py --email you@yourdomain.in --password '...' --demo

The `--demo` businesses are deliberately two *different* trades (a clinic and a
salon) running on the same code, seeded from their presets. That is the whole
point of the platform, and it is worth being able to see it side by side.

Everything in the demo data is invented. The businesses, people, numbers and
call transcripts are fictional and exist to fill a local dashboard so it can be
looked at. None of it came from a real caller, and this repository is public, so
nothing real should ever be added to it.

`--demo` refuses to run against a production database or one that already has
businesses in it. Without that, one command run against the wrong DATABASE_URL
puts invented clinics and invented patients into a database a paying client
reads.
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
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Also create a demo clinic and a demo salon, to show one codebase serving two trades.",
    )
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


async def create_business(
    db,
    *,
    business_type: str,
    name: str,
    slug: str,
    phone_number: str,
    owner_email: str,
    owner_name: str,
    address: str,
    city: str,
    contact_phone: str,
    staff: list[tuple[str, str, int]],
    opens_at: time,
    closes_at: time,
) -> Business | None:
    """Create one tenant, seeded from its business-type preset.

    This mirrors exactly what `POST /api/v1/onboarding/businesses` does, so the
    demo data cannot drift away from the real onboarding path.
    """
    existing = (await db.execute(select(Business).where(Business.slug == slug))).scalar_one_or_none()
    if existing:
        print(f"Business '{slug}' already exists; leaving it unchanged.")
        return None

    preset = get_preset(business_type)

    business = Business(
        name=name,
        slug=slug,
        phone_number=phone_number,
        address=address,
        city=city,
        contact_phone=contact_phone,
        contact_email=owner_email,
        timezone="Asia/Kolkata",
        business_type=preset.slug,
        business_descriptor=preset.business_descriptor,
        labels=preset.label_map(),
        intake_fields=preset.intake_payload(),
        agent_rules=list(preset.rules),
        escalation_instructions=preset.escalation,
        agent_name=preset.default_agent_name,
        primary_language=Language.MIXED,
        opens_at=opens_at,
        closes_at=closes_at,
        working_days=[1, 2, 3, 4, 5, 6],
        slot_duration_minutes=preset.default_slot_minutes,
    )
    db.add(business)
    await db.flush()

    db.add(
        User(
            email=owner_email,
            password_hash=hash_password("demo-password-123"),
            full_name=owner_name,
            role=UserRole.OWNER,
            business_id=business.id,
        )
    )

    for staff_name, specialization, duration in staff:
        db.add(
            StaffMember(
                business_id=business.id,
                name=staff_name,
                specialization=specialization,
                consultation_duration_minutes=duration,
            )
        )

    for service in preset.example_services:
        db.add(
            AppointmentType(
                business_id=business.id,
                name=service,
                duration_minutes=preset.default_slot_minutes,
            )
        )

    await db.flush()
    print(f"Created {preset.display_name.lower()} '{name}'  ({owner_email} / demo-password-123)")
    return business


async def add_activity(db, business: Business, samples: list[dict]) -> None:
    """Give a business some calls and appointments so its dashboard is not empty."""
    now = datetime.now(timezone.utc)
    staff = (
        await db.execute(select(StaffMember).where(StaffMember.business_id == business.id))
    ).scalars().all()

    for index, sample in enumerate(samples):
        customer = Customer(
            business_id=business.id,
            name=sample["customer_name"],
            phone=sample["phone"],
            preferred_language=sample.get("language", Language.MIXED),
        )
        db.add(customer)
        await db.flush()

        call = Call(
            business_id=business.id,
            vapi_call_id=f"demo-{business.slug}-{index}",
            customer_id=customer.id,
            caller_number=customer.phone,
            started_at=now - timedelta(hours=index + 1),
            ended_at=now - timedelta(hours=index + 1) + timedelta(seconds=sample["duration"]),
            duration_seconds=sample["duration"],
            language=sample.get("language", Language.MIXED),
            outcome=CallOutcome.BOOKED,
            summary=sample["summary"],
            transcript=sample["transcript"],
        )
        db.add(call)
        await db.flush()

        db.add(
            Appointment(
                business_id=business.id,
                customer_id=customer.id,
                staff_member_id=staff[index % len(staff)].id if staff else None,
                call_id=call.id,
                starts_at=now + timedelta(days=index + 1, hours=2),
                ends_at=now
                + timedelta(days=index + 1, hours=2, minutes=business.slot_duration_minutes),
                status=AppointmentStatus.SCHEDULED,
                reason=sample["reason"],
            )
        )


async def create_demo_data(db) -> None:
    """Fill a local dashboard with something to look at.

    Every name, number, address and transcript below is invented. Numbers use an
    obviously-unreal 99999 prefix rather than a plausible one, because this file
    is in a public repository and a realistic number belongs to somebody.
    """
    clinic = await create_business(
        db,
        business_type="clinic",
        name="Sunrise Multispeciality Clinic",
        slug="sunrise-clinic",
        phone_number="+919999900001",
        owner_email="owner@sunriseclinic.in",
        owner_name="Dr. Meera Sharma",
        address="21 Nehru Place, New Delhi",
        city="New Delhi",
        contact_phone="+919999900002",
        staff=[
            ("Dr. Meera Sharma", "General Physician", 15),
            ("Dr. Rohit Verma", "Dermatologist", 20),
        ],
        opens_at=time(9, 0),
        closes_at=time(19, 0),
    )
    if clinic:
        await add_activity(
            db,
            clinic,
            [
                {
                    "customer_name": "Anjali Gupta",
                    "phone": "+919999900010",
                    "duration": 95,
                    "reason": "Persistent cough",
                    "summary": "Patient booked a follow-up for a persistent cough.",
                    "transcript": (
                        "Asha: नमस्ते, Sunrise Multispeciality Clinic. This is Asha. मैं आपकी क्या मदद कर सकती हूँ?\n"
                        "Caller: Hi, mujhe kal appointment chahiye tha, cough ke liye.\n"
                        "Asha: बिल्कुल. Kal 11:30 या 4:15 खाली है. कौन सा ठीक रहेगा?\n"
                        "Caller: 11:30 theek hai.\n"
                        "Asha: Done. आपका appointment कल 11:30 बजे confirm हो गया है."
                    ),
                }
            ],
        )

    salon = await create_business(
        db,
        business_type="salon",
        name="Glow Studio",
        slug="glow-studio",
        phone_number="+919999900003",
        owner_email="owner@glowstudio.in",
        owner_name="Priya Nair",
        address="14 Linking Road, Bandra West, Mumbai",
        city="Mumbai",
        contact_phone="+919999900004",
        staff=[
            ("Priya Nair", "Senior Stylist", 45),
            ("Kabir Shah", "Colour Specialist", 90),
        ],
        opens_at=time(10, 0),
        closes_at=time(20, 0),
    )
    if salon:
        await add_activity(
            db,
            salon,
            [
                {
                    "customer_name": "Sneha Iyer",
                    "phone": "+919999900011",
                    "duration": 78,
                    "reason": "Hair colour touch-up",
                    "summary": "Client booked a colour touch-up with Kabir.",
                    "transcript": (
                        "Riya: नमस्ते, Glow Studio. This is Riya. मैं आपकी क्या मदद कर सकती हूँ?\n"
                        "Caller: Hi, colour touch-up karwana tha, Kabir ke saath.\n"
                        "Riya: Sure. Kabir के पास Saturday 2:00 या 5:30 खाली है.\n"
                        "Caller: 2 baje perfect hai.\n"
                        "Riya: Booked. Saturday 2 बजे, WhatsApp पर confirmation आ जाएगा."
                    ),
                }
            ],
        )

    print()
    print("Two different trades, one codebase. Compare their Settings pages:")
    print("  Clinic → 'Patients' / 'Doctors', medical rules, emergency escalation")
    print("  Salon  → 'Clients'  / 'Stylists', pricing rules, no escalation")


async def _refuse_demo_if_unsafe(db) -> None:
    """Demo data belongs in a local database and nowhere else.

    Two separate checks, because either alone is easy to slip past: APP_ENV
    catches the deployed environment, and an existing business catches a staging
    or local database that someone has since pointed at real work.
    """
    from app.config import get_settings

    settings = get_settings()
    if settings.is_production:
        raise SystemExit(
            "Refusing to seed demo data: APP_ENV is production.\n"
            "The demo businesses, people and transcripts are invented, and they "
            "must never appear in a database a client reads."
        )

    existing = (await db.execute(select(Business))).scalars().first()
    if existing is not None:
        raise SystemExit(
            f"Refusing to seed demo data: this database already has businesses "
            f"(found {existing.name!r}).\n"
            "Invented clinics alongside real ones is worse than either alone."
        )


async def main() -> None:
    args = parse_args()
    await init_db()
    async with SessionLocal() as db:
        if args.demo:
            await _refuse_demo_if_unsafe(db)
        await create_superadmin(db, args)
        if args.demo:
            await create_demo_data(db)
        await db.commit()
    print("\nSeed complete.")


if __name__ == "__main__":
    asyncio.run(main())
