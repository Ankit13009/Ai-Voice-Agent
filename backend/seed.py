#!/usr/bin/env python
"""Create the first superadmin, and optionally a demo clinic to click around in.

Usage:
    python seed.py --email you@example.com --password 'a-strong-password'
    python seed.py --email you@example.com --password '...' --demo

The superadmin is the only role that can onboard clinics, so this bootstraps the
system. Run once per environment.
"""

import argparse
import asyncio
import sys
from datetime import datetime, time, timedelta, timezone

from sqlalchemy import select

from app.core.security import hash_password
from app.db.models import (
    Appointment,
    AppointmentStatus,
    AppointmentType,
    Call,
    CallOutcome,
    Clinic,
    Doctor,
    Language,
    Patient,
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
        help="Also create a demo clinic with sample calls and appointments.",
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
        clinic_id=None,
    )
    db.add(user)
    await db.flush()
    print(f"Created superadmin {email}")
    return user


async def create_demo_clinic(db) -> None:
    existing = (
        await db.execute(select(Clinic).where(Clinic.slug == "demo-clinic"))
    ).scalar_one_or_none()
    if existing:
        print("Demo clinic already exists; leaving it unchanged.")
        return

    clinic = Clinic(
        name="Sunrise Multispeciality Clinic",
        slug="demo-clinic",
        phone_number="+911140001234",
        address="21 Nehru Place, New Delhi",
        city="New Delhi",
        contact_phone="+919810012345",
        contact_email="frontdesk@sunriseclinic.in",
        timezone="Asia/Kolkata",
        agent_name="Asha",
        primary_language=Language.MIXED,
        opens_at=time(9, 0),
        closes_at=time(19, 0),
        working_days=[1, 2, 3, 4, 5, 6],
        slot_duration_minutes=15,
    )
    db.add(clinic)
    await db.flush()

    owner = User(
        email="owner@sunriseclinic.in",
        password_hash=hash_password("demo-password-123"),
        full_name="Dr. Meera Sharma",
        role=UserRole.OWNER,
        clinic_id=clinic.id,
    )
    db.add(owner)

    doctors = [
        Doctor(
            clinic_id=clinic.id,
            name="Dr. Meera Sharma",
            specialization="General Physician",
            consultation_duration_minutes=15,
        ),
        Doctor(
            clinic_id=clinic.id,
            name="Dr. Rohit Verma",
            specialization="Dermatologist",
            consultation_duration_minutes=20,
        ),
    ]
    for doctor in doctors:
        db.add(doctor)

    for name, duration in (("First consultation", 30), ("Follow-up", 15)):
        db.add(AppointmentType(clinic_id=clinic.id, name=name, duration_minutes=duration))

    await db.flush()

    now = datetime.now(timezone.utc)
    patients = [
        Patient(
            clinic_id=clinic.id,
            name="Anjali Gupta",
            phone="+919876543210",
            preferred_language=Language.MIXED,
        ),
        Patient(
            clinic_id=clinic.id,
            name="Ravi Kumar",
            phone="+919812345678",
            preferred_language=Language.HINDI,
        ),
    ]
    for patient in patients:
        db.add(patient)
    await db.flush()

    # A couple of calls and appointments so every dashboard page has content.
    call = Call(
        clinic_id=clinic.id,
        vapi_call_id="demo-call-1",
        patient_id=patients[0].id,
        caller_number=patients[0].phone,
        started_at=now - timedelta(hours=3),
        ended_at=now - timedelta(hours=3) + timedelta(seconds=95),
        duration_seconds=95,
        language=Language.MIXED,
        outcome=CallOutcome.BOOKED,
        summary="Patient booked a follow-up for a persistent cough.",
        transcript=(
            "Asha: नमस्ते, Sunrise Multispeciality Clinic. This is Asha. मैं आपकी क्या मदद कर सकती हूँ?\n"
            "Caller: Hi, mujhe kal appointment chahiye tha, cough ke liye.\n"
            "Asha: बिल्कुल. Kal 11:30 या 4:15 खाली है. कौन सा ठीक रहेगा?\n"
            "Caller: 11:30 theek hai.\n"
            "Asha: Done. आपका appointment कल 11:30 बजे confirm हो गया है."
        ),
    )
    db.add(call)
    await db.flush()

    db.add(
        Appointment(
            clinic_id=clinic.id,
            patient_id=patients[0].id,
            doctor_id=doctors[0].id,
            call_id=call.id,
            starts_at=now + timedelta(days=1, hours=2),
            ends_at=now + timedelta(days=1, hours=2, minutes=15),
            status=AppointmentStatus.SCHEDULED,
            reason="Persistent cough",
        )
    )
    db.add(
        Appointment(
            clinic_id=clinic.id,
            patient_id=patients[1].id,
            doctor_id=doctors[1].id,
            starts_at=now + timedelta(days=3),
            ends_at=now + timedelta(days=3, minutes=20),
            status=AppointmentStatus.SCHEDULED,
            reason="Skin rash follow-up",
        )
    )
    db.add(
        Call(
            clinic_id=clinic.id,
            vapi_call_id="demo-call-2",
            caller_number="+919900011122",
            started_at=now - timedelta(hours=1),
            ended_at=now - timedelta(hours=1) + timedelta(seconds=22),
            duration_seconds=22,
            language=Language.ENGLISH,
            outcome=CallOutcome.ENQUIRY,
            summary="Caller asked about clinic timings on Sunday.",
            transcript="Asha: Thank you for calling Sunrise. \nCaller: Are you open on Sunday?\nAsha: We are closed on Sundays.",
        )
    )

    print("Created demo clinic 'Sunrise Multispeciality Clinic'")
    print("  Owner login: owner@sunriseclinic.in / demo-password-123")
    print("  Note: Google Calendar is not connected, so availability and booking")
    print("        will report the integration as unconfigured until you connect it.")


async def main() -> None:
    args = parse_args()
    await init_db()
    async with SessionLocal() as db:
        await create_superadmin(db, args)
        if args.demo:
            await create_demo_clinic(db)
        await db.commit()
    print("Seed complete.")


if __name__ == "__main__":
    asyncio.run(main())
