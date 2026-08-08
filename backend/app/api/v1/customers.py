"""Customer endpoints. Read and light edit only.

There is no create endpoint: customers come into existence through a booking, so
a standalone create would produce records with no appointment and no call.
"""

import logging
from typing import Annotated

from fastapi import APIRouter, Query, Request
from sqlalchemy import func, select

from app.core.deps import (
    ActiveBusiness,
    CurrentUserDep,
    DbSession,
    Paging,
    scoped_get,
    write_audit_log,
)
from app.core.response import ok, paginated
from app.db.models import Appointment, AppointmentStatus, Customer
from app.schemas.appointment import CustomerUpdate

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/customers", tags=["customers"])


def _serialize(customer: Customer) -> dict:
    return {
        "id": customer.id,
        "name": customer.name,
        "phone": customer.phone,
        "email": customer.email,
        "preferred_language": customer.preferred_language.value,
        "notes": customer.notes,
        "created_at": customer.created_at.isoformat(),
    }


@router.get("", summary="List customers")
async def list_customers(
    business_id: ActiveBusiness,
    db: DbSession,
    paging: Paging,
    _user: CurrentUserDep,
    search: Annotated[str | None, Query(max_length=120, description="Name or phone.")] = None,
) -> dict:
    filters = [Customer.business_id == business_id]
    if search:
        term = f"%{search.strip()}%"
        filters.append(Customer.name.ilike(term) | Customer.phone.ilike(term))

    base = select(Customer).where(*filters)
    total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one()

    rows = (
        await db.execute(
            base.order_by(Customer.created_at.desc()).offset(paging.offset).limit(paging.page_size)
        )
    ).scalars().all()

    return paginated(
        [_serialize(p) for p in rows],
        page=paging.page,
        page_size=paging.page_size,
        total=total,
    )


@router.get("/{customer_id}", summary="One customer with their appointment history")
async def get_customer(
    customer_id: str, business_id: ActiveBusiness, db: DbSession, _user: CurrentUserDep
) -> dict:
    customer = await scoped_get(db, Customer, customer_id, business_id, resource_name="Customer")

    appointments = (
        await db.execute(
            select(Appointment)
            .where(Appointment.business_id == business_id, Appointment.customer_id == customer.id)
            .order_by(Appointment.starts_at.desc())
            .limit(50)
        )
    ).scalars().all()

    return ok(
        {
            **_serialize(customer),
            "appointments": [
                {
                    "id": a.id,
                    "starts_at": a.starts_at.isoformat(),
                    "status": a.status.value,
                    "reason": a.reason,
                }
                for a in appointments
            ],
            "total_appointments": len(appointments),
            "upcoming_appointments": sum(
                1
                for a in appointments
                if a.status in (AppointmentStatus.SCHEDULED, AppointmentStatus.RESCHEDULED)
            ),
        }
    )


@router.patch("/{customer_id}", summary="Update customer details")
async def update_customer(
    customer_id: str,
    payload: CustomerUpdate,
    business_id: ActiveBusiness,
    db: DbSession,
    request: Request,
    _user: CurrentUserDep,
) -> dict:
    """Phone is not editable here: it is the customer's identity within the
    business and the key WhatsApp messages are addressed to."""
    customer = await scoped_get(db, Customer, customer_id, business_id, resource_name="Customer")

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(customer, field, value)

    await write_audit_log(
        db,
        request,
        action="customer.updated",
        business_id=business_id,
        resource_type="customer",
        resource_id=customer.id,
        metadata={"fields": list(payload.model_dump(exclude_unset=True).keys())},
    )
    await db.commit()
    await db.refresh(customer)

    return ok(_serialize(customer), message="Customer updated.")
