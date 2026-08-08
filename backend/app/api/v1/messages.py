"""WhatsApp message log and manual resend."""

import logging
from datetime import datetime, timezone
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
from app.core.errors import ConflictError
from app.core.response import ok, paginated
from app.db.models import Clinic, MessageKind, MessageStatus, WhatsAppMessage
from app.services import whatsapp

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/messages", tags=["messages"])


def _serialize(message: WhatsAppMessage) -> dict:
    return {
        "id": message.id,
        "appointment_id": message.appointment_id,
        "to_phone": message.to_phone,
        "kind": message.kind.value,
        "status": message.status.value,
        "template_name": message.template_name,
        "language_code": message.language_code,
        "rendered_preview": message.rendered_preview,
        "scheduled_for": message.scheduled_for.isoformat() if message.scheduled_for else None,
        "sent_at": message.sent_at.isoformat() if message.sent_at else None,
        "delivered_at": message.delivered_at.isoformat() if message.delivered_at else None,
        "attempt_count": message.attempt_count,
        "last_error": message.last_error,
        "created_at": message.created_at.isoformat(),
    }


@router.get("", summary="WhatsApp message log")
async def list_messages(
    clinic_id: ActiveClinic,
    db: DbSession,
    paging: Paging,
    _user: CurrentUserDep,
    status: Annotated[MessageStatus | None, Query()] = None,
    kind: Annotated[MessageKind | None, Query()] = None,
    appointment_id: Annotated[str | None, Query()] = None,
) -> dict:
    filters = [WhatsAppMessage.clinic_id == clinic_id]
    if status:
        filters.append(WhatsAppMessage.status == status)
    if kind:
        filters.append(WhatsAppMessage.kind == kind)
    if appointment_id:
        filters.append(WhatsAppMessage.appointment_id == appointment_id)

    base = select(WhatsAppMessage).where(*filters)
    total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one()

    rows = (
        await db.execute(
            base.order_by(WhatsAppMessage.created_at.desc())
            .offset(paging.offset)
            .limit(paging.page_size)
        )
    ).scalars().all()

    return paginated(
        [_serialize(m) for m in rows],
        page=paging.page,
        page_size=paging.page_size,
        total=total,
    )


@router.post("/{message_id}/retry", summary="Retry a failed message")
async def retry_message(
    message_id: str,
    clinic_id: ActiveClinic,
    db: DbSession,
    request: Request,
    _user: CurrentUserDep,
) -> dict:
    """Resend a message that failed, after the clinic has fixed the cause.

    Most failures are a template still awaiting Meta approval or a number that
    is not on WhatsApp. Both are fixable, and neither should require rebooking
    the appointment.
    """
    message = await scoped_get(
        db, WhatsAppMessage, message_id, clinic_id, resource_name="Message"
    )

    if message.status not in (MessageStatus.FAILED, MessageStatus.PENDING):
        raise ConflictError(f"This message is already {message.status.value}.")

    clinic = (await db.execute(select(Clinic).where(Clinic.id == clinic_id))).scalar_one()

    # Reset the attempt counter: this is a deliberate human retry, not the
    # automatic one that the send path caps at three.
    message.attempt_count = 0
    message.status = MessageStatus.PENDING
    message.scheduled_for = datetime.now(timezone.utc)

    sent = await whatsapp.send_message(db, message, clinic)

    await write_audit_log(
        db,
        request,
        action="message.retried",
        clinic_id=clinic_id,
        resource_type="whatsapp_message",
        resource_id=message.id,
        metadata={"succeeded": sent},
    )
    await db.commit()
    await db.refresh(message)

    return ok(
        _serialize(message),
        message="Message sent." if sent else "The message could not be sent. See the error on the row.",
    )
