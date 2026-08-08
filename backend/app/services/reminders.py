"""Background worker that sends due WhatsApp messages.

Design: reminders are rows, not timers. Every appointment writes its
confirmation and both reminders into `whatsapp_messages` with a `scheduled_for`
time, and this loop repeatedly asks "what is pending and due now?". That means
pending reminders survive a restart, a deploy, or a crash, which an in-memory
`asyncio.sleep(24h)` would not.

Concurrency: a `SELECT ... FOR UPDATE SKIP LOCKED` claim marks rows before
sending, so running two API instances does not double-text patients. SQLite has
no such locking, and degrades to a plain select, which is fine because local
development runs a single process.
"""

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.models import Clinic, MessageStatus, WhatsAppMessage
from app.db.session import SessionLocal, engine
from app.services import whatsapp

logger = logging.getLogger(__name__)

BATCH_SIZE = 50
# Messages older than this were missed during an outage. Sending a "your
# appointment is in 2 hours" reminder six hours late is worse than not sending
# it, so they are expired instead.
MAX_LATENESS_SECONDS = 3 * 60 * 60


async def _claim_due_messages(db: AsyncSession, now: datetime) -> list[WhatsAppMessage]:
    """Select and lock the messages this worker will send."""
    stmt = (
        select(WhatsAppMessage)
        .where(
            WhatsAppMessage.status == MessageStatus.PENDING,
            WhatsAppMessage.scheduled_for.is_not(None),
            WhatsAppMessage.scheduled_for <= now,
        )
        .order_by(WhatsAppMessage.scheduled_for.asc())
        .limit(BATCH_SIZE)
    )
    if engine.dialect.name != "sqlite":
        stmt = stmt.with_for_update(skip_locked=True)

    return list((await db.execute(stmt)).scalars().all())


async def process_due_messages() -> int:
    """One pass. Returns how many messages were sent successfully."""
    sent = 0
    now = datetime.now(timezone.utc)

    async with SessionLocal() as db:
        try:
            messages = await _claim_due_messages(db, now)
            if not messages:
                await db.commit()
                return 0

            # Load each clinic once rather than per message.
            clinic_ids = {m.clinic_id for m in messages}
            clinics = {
                c.id: c
                for c in (
                    await db.execute(select(Clinic).where(Clinic.id.in_(clinic_ids)))
                ).scalars()
            }

            for message in messages:
                scheduled = message.scheduled_for
                if scheduled and (now - scheduled).total_seconds() > MAX_LATENESS_SECONDS:
                    message.status = MessageStatus.CANCELLED
                    message.last_error = "Expired: too late to be useful."
                    logger.warning(
                        "Expired stale WhatsApp message %s (was due %s)", message.id, scheduled
                    )
                    continue

                clinic = clinics.get(message.clinic_id)
                if clinic is None or not clinic.is_active:
                    message.status = MessageStatus.CANCELLED
                    message.last_error = "Clinic is inactive."
                    continue

                if await whatsapp.send_message(db, message, clinic):
                    sent += 1

            await db.commit()
        except Exception:
            await db.rollback()
            logger.exception("Reminder batch failed; rolled back.")
            raise

    if sent:
        logger.info("Sent %d WhatsApp message(s).", sent)
    return sent


async def reminder_loop(stop_event: asyncio.Event) -> None:
    """Run `process_due_messages` on an interval until asked to stop.

    Never dies: a failure in one batch is logged and the loop continues, because
    a scheduler that exits on the first transient network error silently stops
    every clinic's reminders.
    """
    settings = get_settings()
    interval = max(30, settings.reminder_poll_seconds)
    logger.info("Reminder scheduler started (every %ss).", interval)

    while not stop_event.is_set():
        try:
            await process_due_messages()
        except Exception:  # noqa: BLE001
            logger.exception("Reminder loop iteration failed; continuing.")

        try:
            # Wait on the stop event rather than sleeping, so shutdown is prompt
            # instead of waiting out a full interval.
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
        except asyncio.TimeoutError:
            continue

    logger.info("Reminder scheduler stopped.")
