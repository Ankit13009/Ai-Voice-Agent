"""Data retention: purge old call transcripts and recordings.

Call recordings and transcripts of a clinic conversation are health data. India's
DPDP Act treats that as sensitive personal data, and "we kept everything forever
because nobody picked a number" is not a defensible position with a regulator or
a client's lawyer.

Two deliberate choices:

* **The call row survives.** Only the transcript, summary and recording URL are
  cleared. Timestamps, duration, outcome and cost stay, so the business keeps its
  analytics and billing history without keeping the conversation.

* **Recordings expire before transcripts.** Audio is the more sensitive artefact
  (a voice is biometric-adjacent) and the least often needed later, so it has a
  shorter default.

Per-business, because a dental practice and a gym have genuinely different
obligations. Zero means keep indefinitely, which a business has to choose rather
than fall into.
"""

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Business, Call
from app.db.session import SessionLocal

logger = logging.getLogger(__name__)


async def purge_business(db: AsyncSession, business: Business) -> dict[str, int]:
    """Apply one business's retention policy. Returns what was cleared."""
    now = datetime.now(timezone.utc)
    cleared = {"transcripts": 0, "recordings": 0}

    if business.transcript_retention_days > 0:
        cutoff = now - timedelta(days=business.transcript_retention_days)
        result = await db.execute(
            update(Call)
            .where(
                Call.business_id == business.id,
                Call.created_at < cutoff,
                # Only touch rows that still hold content, so a repeat run is a
                # no-op rather than a pointless write across the whole table.
                (Call.transcript != "") | (Call.summary != ""),
            )
            .values(transcript="", summary="")
        )
        cleared["transcripts"] = result.rowcount or 0

    if business.recording_retention_days > 0:
        cutoff = now - timedelta(days=business.recording_retention_days)
        result = await db.execute(
            update(Call)
            .where(
                Call.business_id == business.id,
                Call.created_at < cutoff,
                Call.recording_url != "",
            )
            .values(recording_url="")
        )
        cleared["recordings"] = result.rowcount or 0

    return cleared


async def run_retention_sweep() -> dict[str, int]:
    """Apply every active business's policy. Safe to run repeatedly."""
    totals = {"transcripts": 0, "recordings": 0, "businesses": 0}

    async with SessionLocal() as db:
        try:
            businesses = (
                await db.execute(select(Business).where(Business.is_active.is_(True)))
            ).scalars().all()

            for business in businesses:
                cleared = await purge_business(db, business)
                totals["transcripts"] += cleared["transcripts"]
                totals["recordings"] += cleared["recordings"]
                totals["businesses"] += 1
                if cleared["transcripts"] or cleared["recordings"]:
                    logger.info(
                        "Retention: cleared %d transcript(s) and %d recording(s) for %s",
                        cleared["transcripts"],
                        cleared["recordings"],
                        business.slug,
                    )

            await db.commit()
        except Exception:
            await db.rollback()
            logger.exception("Retention sweep failed; rolled back.")
            raise

    return totals
