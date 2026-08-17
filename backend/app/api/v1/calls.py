"""Call log endpoints.

The list response deliberately omits `transcript`. A business's call log page
shows 20 rows, and full transcripts would turn a routine page load into
megabytes of text. The detail endpoint carries it.
"""

import logging
from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.core.deps import ActiveBusiness, CurrentUserDep, DbSession, Paging, scoped_get
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
        "customer": (
            {"id": call.customer.id, "name": call.customer.name, "phone": call.customer.phone}
            if call.customer
            else None
        ),
        "created_at": call.created_at.isoformat(),
    }


@router.get("", summary="List calls")
async def list_calls(
    business_id: ActiveBusiness,
    db: DbSession,
    paging: Paging,
    _user: CurrentUserDep,
    outcome: Annotated[CallOutcome | None, Query()] = None,
    search: Annotated[str | None, Query(max_length=120, description="Caller number.")] = None,
) -> dict:
    filters = [Call.business_id == business_id]
    if outcome:
        filters.append(Call.outcome == outcome)
    if search:
        filters.append(Call.caller_number.ilike(f"%{search.strip()}%"))

    base = select(Call).where(*filters)
    total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one()

    rows = (
        await db.execute(
            base.options(selectinload(Call.customer))
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
                    Appointment.business_id == business_id, Appointment.call_id.in_(call_ids)
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
    call_id: str, business_id: ActiveBusiness, db: DbSession, _user: CurrentUserDep
) -> dict:
    call = await scoped_get(db, Call, call_id, business_id, resource_name="Call")
    await db.refresh(call, ["customer"])

    appointment_id = (
        await db.execute(
            select(Appointment.id).where(
                Appointment.business_id == business_id, Appointment.call_id == call.id
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


@router.get("/{call_id}/recording", summary="A playable link to this call's recording")
async def call_recording(
    call_id: str,
    business_id: ActiveBusiness,
    db: DbSession,
    _user: CurrentUserDep,
) -> dict:
    """Fetch a fresh, playable URL for the recording.

    The URL stored on the call is VAPI's private R2 object, which requires
    authorization and returns 400 to a browser forever. The playable form is a
    presigned URL that VAPI mints on request and expires within the hour, so it
    cannot be stored: by the time anyone opened the call it would be dead.

    Fetched through us rather than handing the dashboard a VAPI key, and scoped
    to the caller's own business so a recording of someone else's patient cannot
    be pulled by guessing an id.
    """
    from app.services import vapi

    call = await scoped_get(db, Call, call_id, business_id, resource_name="Call")

    if not call.vapi_call_id:
        return ok({"url": "", "reason": "This call has no recording."})

    try:
        detail = await vapi._request("GET", f"/call/{call.vapi_call_id}")
    except Exception:  # noqa: BLE001
        logger.warning("Could not fetch a recording URL for call %s", call_id, exc_info=True)
        return ok({"url": "", "reason": "The recording could not be fetched just now."})

    artifact = detail.get("artifact") or {}
    # Mono is the caller and agent mixed together, which is what someone
    # reviewing a call wants; stereo splits them onto separate channels.
    url = artifact.get("presignedMonoUrl") or artifact.get("presignedStereoUrl") or ""

    if not url:
        return ok({"url": "", "reason": "No recording was kept for this call."})

    return ok({"url": url, "expires_at": artifact.get("presignedUrlsExpiresAt", "")})
