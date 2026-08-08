"""Patient endpoints. Read and light edit only.

There is no create endpoint: patients come into existence through a booking, so
a standalone create would produce records with no appointment and no call.
"""

import logging
from typing import Annotated

from fastapi import APIRouter, Query, Request
from sqlalchemy import func, select

from app.core.deps import (
    ActiveClinic,
    CurrentUserDep,
    DbSession,
    Paging,
    scoped_get,
    write_audit_log,
)
from app.core.response import ok, paginated
from app.db.models import Appointment, AppointmentStatus, Patient
from app.schemas.appointment import PatientUpdate

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/patients", tags=["patients"])


def _serialize(patient: Patient) -> dict:
    return {
        "id": patient.id,
        "name": patient.name,
        "phone": patient.phone,
        "email": patient.email,
        "preferred_language": patient.preferred_language.value,
        "notes": patient.notes,
        "created_at": patient.created_at.isoformat(),
    }


@router.get("", summary="List patients")
async def list_patients(
    clinic_id: ActiveClinic,
    db: DbSession,
    paging: Paging,
    _user: CurrentUserDep,
    search: Annotated[str | None, Query(max_length=120, description="Name or phone.")] = None,
) -> dict:
    filters = [Patient.clinic_id == clinic_id]
    if search:
        term = f"%{search.strip()}%"
        filters.append(Patient.name.ilike(term) | Patient.phone.ilike(term))

    base = select(Patient).where(*filters)
    total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one()

    rows = (
        await db.execute(
            base.order_by(Patient.created_at.desc()).offset(paging.offset).limit(paging.page_size)
        )
    ).scalars().all()

    return paginated(
        [_serialize(p) for p in rows],
        page=paging.page,
        page_size=paging.page_size,
        total=total,
    )


@router.get("/{patient_id}", summary="One patient with their appointment history")
async def get_patient(
    patient_id: str, clinic_id: ActiveClinic, db: DbSession, _user: CurrentUserDep
) -> dict:
    patient = await scoped_get(db, Patient, patient_id, clinic_id, resource_name="Patient")

    appointments = (
        await db.execute(
            select(Appointment)
            .where(Appointment.clinic_id == clinic_id, Appointment.patient_id == patient.id)
            .order_by(Appointment.starts_at.desc())
            .limit(50)
        )
    ).scalars().all()

    return ok(
        {
            **_serialize(patient),
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


@router.patch("/{patient_id}", summary="Update patient details")
async def update_patient(
    patient_id: str,
    payload: PatientUpdate,
    clinic_id: ActiveClinic,
    db: DbSession,
    request: Request,
    _user: CurrentUserDep,
) -> dict:
    """Phone is not editable here: it is the patient's identity within the
    clinic and the key WhatsApp messages are addressed to."""
    patient = await scoped_get(db, Patient, patient_id, clinic_id, resource_name="Patient")

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(patient, field, value)

    await write_audit_log(
        db,
        request,
        action="patient.updated",
        clinic_id=clinic_id,
        resource_type="patient",
        resource_id=patient.id,
        metadata={"fields": list(payload.model_dump(exclude_unset=True).keys())},
    )
    await db.commit()
    await db.refresh(patient)

    return ok(_serialize(patient), message="Patient updated.")
