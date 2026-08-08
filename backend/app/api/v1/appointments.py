"""Appointment endpoints: availability, list, create, reschedule, cancel.

Every query filters on `business` (from `ActiveBusiness`, which is derived from the
JWT). Single-appointment reads go through `scoped_get`, so an id belonging to
another business is a 404 rather than a leak.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Query, Request
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.core.deps import (
    ActiveBusiness,
    CurrentUserDep,
    DbSession,
    Paging,
    scoped_get,
    write_audit_log,
)
from app.core.response import ok, paginated
from app.db.models import (
    Appointment,
    AppointmentStatus,
    Business,
    StaffMember,
    Customer,
)
from app.schemas.appointment import (
    AppointmentCancel,
    AppointmentCreate,
    AppointmentReschedule,
)
from app.services import appointments as appointment_service
from app.services import google_calendar as gcal

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/appointments", tags=["appointments"])


async def _load_business(db, business_id: str) -> Business:
    return (await db.execute(select(Business).where(Business.id == business_id))).scalar_one()


def _serialize(appointment: Appointment, business: Business) -> dict:
    customer = appointment.customer
    staff_member = appointment.staff_member
    return {
        "id": appointment.id,
        "status": appointment.status.value,
        "starts_at": appointment.starts_at.isoformat(),
        "ends_at": appointment.ends_at.isoformat(),
        # Pre-rendered in the business's timezone so the browser never has to guess.
        "starts_at_local": appointment.starts_at.astimezone(
            ZoneInfo(business.timezone)
        ).strftime("%d %b %Y, %-I:%M %p"),
        "reason": appointment.reason,
        "notes": appointment.notes,
        "cancellation_reason": appointment.cancellation_reason,
        "customer": {
            "id": customer.id,
            "name": customer.name,
            "phone": customer.phone,
        }
        if customer
        else {"id": "", "name": "", "phone": ""},
        "staff_member_id": appointment.staff_member_id,
        "staff_member_name": staff_member.name if staff_member else "",
        "call_id": appointment.call_id,
        "google_event_id": appointment.google_event_id,
        "synced_to_calendar": bool(appointment.google_event_id),
        "rescheduled_from_id": appointment.rescheduled_from_id,
        "created_at": appointment.created_at.isoformat(),
    }


@router.get("/availability", summary="Open slots on the business calendar")
async def availability(
    business_id: ActiveBusiness,
    db: DbSession,
    _user: CurrentUserDep,
    date_from: Annotated[datetime | None, Query(description="ISO-8601 with offset.")] = None,
    date_to: Annotated[datetime | None, Query(description="ISO-8601 with offset.")] = None,
    staff_member_id: Annotated[str | None, Query()] = None,
    duration_minutes: Annotated[int | None, Query(ge=5, le=240)] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> dict:
    """Live availability, computed from Google Calendar's busy windows."""
    business = await _load_business(db, business_id)
    now = datetime.now(timezone.utc)
    start = date_from or now
    end = date_to or (start + timedelta(days=14))

    if end <= start:
        from app.core.errors import BadRequestError

        raise BadRequestError("date_to must be after date_from.")
    if (end - start).days > 60:
        from app.core.errors import BadRequestError

        raise BadRequestError("Availability can be queried for at most 60 days at a time.")

    staff_member = None
    if staff_member_id:
        staff_member = await scoped_get(db, StaffMember, staff_member_id, business_id, resource_name="StaffMember")

    slots = await gcal.find_available_slots(
        db,
        business,
        start=start,
        end=end,
        staff_member=staff_member,
        duration_minutes=duration_minutes,
        limit=limit,
    )
    return ok(
        [
            {
                "starts_at": s.starts_at.isoformat(),
                "ends_at": s.ends_at.isoformat(),
                "label": s.label(business.timezone),
                "staff_member_id": s.staff_member_id,
                "staff_member_name": s.staff_member_name,
            }
            for s in slots
        ]
    )


@router.get("", summary="List appointments")
async def list_appointments(
    business_id: ActiveBusiness,
    db: DbSession,
    paging: Paging,
    _user: CurrentUserDep,
    status: Annotated[AppointmentStatus | None, Query()] = None,
    staff_member_id: Annotated[str | None, Query()] = None,
    date_from: Annotated[datetime | None, Query()] = None,
    date_to: Annotated[datetime | None, Query()] = None,
    search: Annotated[str | None, Query(max_length=120, description="Customer name or phone.")] = None,
) -> dict:
    business = await _load_business(db, business_id)

    filters = [Appointment.business_id == business_id]
    if status:
        filters.append(Appointment.status == status)
    if staff_member_id:
        filters.append(Appointment.staff_member_id == staff_member_id)
    if date_from:
        filters.append(Appointment.starts_at >= date_from)
    if date_to:
        filters.append(Appointment.starts_at <= date_to)

    base = select(Appointment).where(*filters)
    if search:
        term = f"%{search.strip()}%"
        base = base.join(Customer, Appointment.customer_id == Customer.id).where(
            Customer.name.ilike(term) | Customer.phone.ilike(term)
        )

    total = (
        await db.execute(select(func.count()).select_from(base.subquery()))
    ).scalar_one()

    rows = (
        await db.execute(
            base.options(selectinload(Appointment.customer), selectinload(Appointment.staff_member))
            .order_by(Appointment.starts_at.desc())
            .offset(paging.offset)
            .limit(paging.page_size)
        )
    ).scalars().all()

    return paginated(
        [_serialize(a, business) for a in rows],
        page=paging.page,
        page_size=paging.page_size,
        total=total,
    )


@router.get("/{appointment_id}", summary="One appointment")
async def get_appointment(
    appointment_id: str, business_id: ActiveBusiness, db: DbSession, _user: CurrentUserDep
) -> dict:
    business = await _load_business(db, business_id)
    appointment = await scoped_get(
        db, Appointment, appointment_id, business_id, resource_name="Appointment"
    )
    await db.refresh(appointment, ["customer", "staff_member"])
    return ok(_serialize(appointment, business))


@router.post("", status_code=201, summary="Book an appointment from the dashboard")
async def create_appointment(
    payload: AppointmentCreate,
    business_id: ActiveBusiness,
    db: DbSession,
    request: Request,
    _user: CurrentUserDep,
) -> dict:
    """Same code path the voice agent uses, so both produce identical results."""
    business = await _load_business(db, business_id)

    appointment = await appointment_service.book_appointment(
        db,
        business,
        customer_name=payload.customer_name,
        customer_phone=payload.customer_phone,
        starts_at=payload.starts_at,
        staff_member_id=payload.staff_member_id,
        appointment_type_id=payload.appointment_type_id,
        duration_minutes=payload.duration_minutes,
        reason=payload.reason,
        notes=payload.notes,
        language=payload.preferred_language,
    )

    await write_audit_log(
        db,
        request,
        action="appointment.created",
        business_id=business_id,
        resource_type="appointment",
        resource_id=appointment.id,
        metadata={"starts_at": appointment.starts_at.isoformat()},
    )
    await db.commit()
    await db.refresh(appointment, ["customer", "staff_member"])

    return ok(
        _serialize(appointment, business),
        message="Appointment booked and added to the calendar.",
    )


@router.patch("/{appointment_id}/reschedule", summary="Move an appointment")
async def reschedule(
    appointment_id: str,
    payload: AppointmentReschedule,
    business_id: ActiveBusiness,
    db: DbSession,
    request: Request,
    _user: CurrentUserDep,
) -> dict:
    business = await _load_business(db, business_id)
    appointment = await scoped_get(
        db, Appointment, appointment_id, business_id, resource_name="Appointment"
    )
    previous = appointment.starts_at

    updated = await appointment_service.reschedule_appointment(
        db,
        business,
        appointment,
        starts_at=payload.starts_at,
        staff_member_id=payload.staff_member_id,
        reason=payload.reason,
    )

    await write_audit_log(
        db,
        request,
        action="appointment.rescheduled",
        business_id=business_id,
        resource_type="appointment",
        resource_id=updated.id,
        metadata={"from": previous.isoformat(), "to": updated.starts_at.isoformat()},
    )
    await db.commit()
    await db.refresh(updated, ["customer", "staff_member"])

    return ok(_serialize(updated, business), message="Appointment rescheduled.")


@router.patch("/{appointment_id}/cancel", summary="Cancel an appointment")
async def cancel(
    appointment_id: str,
    payload: AppointmentCancel,
    business_id: ActiveBusiness,
    db: DbSession,
    request: Request,
    _user: CurrentUserDep,
) -> dict:
    business = await _load_business(db, business_id)
    appointment = await scoped_get(
        db, Appointment, appointment_id, business_id, resource_name="Appointment"
    )

    updated = await appointment_service.cancel_appointment(
        db, business, appointment, reason=payload.reason, notify_customer=payload.notify_customer
    )

    await write_audit_log(
        db,
        request,
        action="appointment.cancelled",
        business_id=business_id,
        resource_type="appointment",
        resource_id=updated.id,
        metadata={"reason": payload.reason},
    )
    await db.commit()
    await db.refresh(updated, ["customer", "staff_member"])

    return ok(
        _serialize(updated, business),
        message="Appointment cancelled and removed from the calendar.",
    )
