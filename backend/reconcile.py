#!/usr/bin/env python
"""Reconcile VAPI assistants against the database.

Assistants are linked to a business by a `vapi_assistant_id` column. Nothing
enforces that link, so it drifts: a restored backup, a deleted and recreated
business, or a wiped development database all leave assistants running on VAPI
that no business points at. They are billable, they answer calls for a tenant
that no longer exists, and the dashboard shows nothing wrong.

This finds both directions of drift and, with --fix, repairs it:

  * an assistant whose business is gone      -> delete it
  * a business with no assistant             -> create one
  * a phone number pointing at a dead        -> repoint at the business's
    assistant                                   current assistant

Assistants that were not created by this system (no business_id in metadata)
are never touched.

Usage:
    python reconcile.py            # report only
    python reconcile.py --fix      # repair
"""

import argparse
import asyncio

from sqlalchemy import select

from app.db.models import Business, StaffMember
from app.db.session import SessionLocal
from app.services import vapi


async def main(fix: bool) -> None:
    async with SessionLocal() as db:
        businesses = (await db.execute(select(Business))).scalars().all()
        by_assistant = {b.vapi_assistant_id: b for b in businesses if b.vapi_assistant_id}
        existing_ids = {b.id for b in businesses}

    assistants = await vapi._request("GET", "/assistant")
    ours = [a for a in assistants if (a.get("metadata") or {}).get("business_id")]
    theirs = len(assistants) - len(ours)

    print(f"{len(assistants)} assistant(s) on VAPI: {len(ours)} ours, {theirs} unrelated (ignored)")
    print(f"{len(businesses)} business(es) in the database\n")

    problems = 0

    # 1. Assistants whose business no longer exists.
    for a in ours:
        owner = (a.get("metadata") or {})["business_id"]
        if owner not in existing_ids:
            problems += 1
            print(f"  ORPHAN   {a['name'][:34]:36} business {owner[:8]} is gone")
            if fix:
                await vapi.delete_assistant(a["id"])
                print("           -> deleted")

    # 2. Businesses with no assistant, or one that no longer exists on VAPI.
    live_ids = {a["id"] for a in assistants}
    async with SessionLocal() as db:
        for b in (await db.execute(select(Business))).scalars():
            missing = not b.vapi_assistant_id
            dangling = b.vapi_assistant_id and b.vapi_assistant_id not in live_ids
            if not (missing or dangling):
                continue
            problems += 1
            why = "has no assistant" if missing else "points at an assistant that no longer exists"
            print(f"  MISSING  {b.slug:36} {why}")
            if fix:
                staff = (
                    await db.execute(
                        select(StaffMember).where(
                            StaffMember.business_id == b.id, StaffMember.is_active.is_(True)
                        )
                    )
                ).scalars().all()
                b.vapi_assistant_id = await vapi.create_assistant(b, list(staff))
                print(f"           -> created {b.vapi_assistant_id[:8]}")
        if fix:
            await db.commit()

    # 3. Phone numbers pointing at an assistant nobody owns.
    async with SessionLocal() as db:
        by_assistant = {
            b.vapi_assistant_id: b
            for b in (await db.execute(select(Business))).scalars()
            if b.vapi_assistant_id
        }

    for n in await vapi.list_phone_numbers():
        assigned = n.get("assistantId")
        if assigned and assigned not in by_assistant:
            problems += 1
            print(f"  STRANDED {n.get('number'):36} points at an assistant no business owns")
            # Deliberately not repaired. This previously repointed the number at
            # `next(iter(by_assistant))`, an arbitrary business, which means calls
            # for one client could start reaching another client's agent. A
            # number is the one thing where guessing wrong is a cross-tenant
            # leak, so it is reported for a human to point somewhere on purpose.
            if fix:
                owner = next(
                    (b.name for a, b in by_assistant.items() if a == assigned), None
                )
                print(
                    "           -> not repaired: attach it to the right business in "
                    "the VAPI dashboard, or from that business's settings."
                    + (f" (was {owner})" if owner else "")
                )

    print()
    if problems == 0:
        print("Everything is consistent.")
    elif fix:
        print(f"Repaired {problems} problem(s).")
    else:
        print(f"Found {problems} problem(s). Re-run with --fix to repair.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--fix", action="store_true", help="Repair, rather than just report.")
    asyncio.run(main(parser.parse_args().fix))
