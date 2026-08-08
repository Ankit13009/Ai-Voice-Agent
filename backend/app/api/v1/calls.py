"""Call log endpoints.

The list response deliberately omits `transcript`. A clinic's call log page
shows 20 rows, and full transcripts would turn a routine page load into
megabytes of text. The detail endpoint carries it.
"""

import logging
from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.core.deps import ActiveClinic, CurrentUserDep, DbSession, Paging, scoped_get
from app.core.response import ok, paginated
from app.db.models import Appointment, Call, CallOutcome

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/calls", tags=["calls"])


def _base_dict(call: Call) -> dict:
    return {
        "id": call.id,
        "vapi_call_id": call.vapi_call_id,
        "caller_number": call.caller_number,
        "started_at": call.started_at.isoformat() if call.started_at else None,
        "ended_at": call.ended_at.isoformat() if call.ended_at else None,
        "duration_seconds": call.duration_seconds,
        "language": call.language.value,
        "outcome": call.outcome.value,
        "summary": call.summary,
        "recording_url": call.recording_url,
        "ended_reason": call.ended_reason,
        "patient": (
            {"id": call.patient.id, "name": call.patient.name, "phone": call.patient.phone}
            if call.patient
            else None
        ),
        "created_at": call.created_at.isoformat(),
    }


@router.get("", summary="List calls")
async def list_calls(
    clinic_id: ActiveClinic,
    db: DbSession,
    paging: Paging,
    _user: CurrentUserDep,
    outcome: Annotated[CallOutcome | None, Query()] = None,
    search: Annotated[str | None, Query(max_length=120, description="Caller number.")] = None,
) -> dict:
    filters = [Call.clinic_id == clinic_id]
    if outcome:
        filters.append(Call.outcome == outcome)
    if search:
        filters.append(Call.caller_number.ilike(f"%{search.strip()}%"))

    base = select(Call).where(*filters)
    total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one()

    rows = (
        await db.execute(
            base.options(selectinload(Call.patient))
            .order_by(Call.created_at.desc())
            .offset(paging.offset)
            .limit(paging.page_size)
        )
    ).scalars().all()

    # One query for the whole page's appointment links, rather than one per row.
    call_ids = [c.id for c in rows]
    appointment_by_call: dict[str, str] = {}
    if call_ids:
        links = (
            await db.execute(
                select(Appointment.call_id, Appointment.id).where(
                    Appointment.clinic_id == clinic_id, Appointment.call_id.in_(call_ids)
                )
            )
        ).all()
        appointment_by_call = {call_id: appt_id for call_id, appt_id in links}

    return paginated(
        [{**_base_dict(c), "appointment_id": appointment_by_call.get(c.id)} for c in rows],
        page=paging.page,
        page_size=paging.page_size,
        total=total,
    )


@router.get("/{call_id}", summary="One call, with the full transcript")
async def get_call(
    call_id: str, clinic_id: ActiveClinic, db: DbSession, _user: CurrentUserDep
) -> dict:
    call = await scoped_get(db, Call, call_id, clinic_id, resource_name="Call")
    await db.refresh(call, ["patient"])

    appointment_id = (
        await db.execute(
            select(Appointment.id).where(
                Appointment.clinic_id == clinic_id, Appointment.call_id == call.id
            )
        )
    ).scalar_one_or_none()

    return ok(
        {
            **_base_dict(call),
            "transcript": call.transcript,
            "cost_paise": call.cost_paise,
            "appointment_id": appointment_id,
        }
    )
