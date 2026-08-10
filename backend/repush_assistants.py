#!/usr/bin/env python
"""Push the current assistant configuration to every business on VAPI.

Why this exists: an assistant is only re-pushed when a business edits a field
that appears in its prompt. Anything that comes from the code rather than the
database, the model, the transcriber, call duration limits, the tool list, the
greeting wording, therefore reaches only those businesses that happen to change
a setting afterwards.

That has already caused two silent divergences. A 900 second call limit was
lowered to 420 and stayed at 900 on the existing business until it was forced
through by hand, and a reworded greeting did the same. With three clients that
is an annoyance. With thirty it means a fix reaches an unknown subset and there
is no way to tell which.

This makes the rollout deliberate: after changing anything in the assistant
payload, run this.

Usage:
    python repush_assistants.py              # show what would change
    python repush_assistants.py --apply      # actually push
    python repush_assistants.py --apply --slug sunrise-clinic
"""

import argparse
import asyncio
import sys

from sqlalchemy import select

from app.db.models import Business, StaffMember
from app.db.session import SessionLocal
from app.services import vapi

# Fields worth showing a diff for. The full payload is large and mostly stable,
# and printing all of it hides the one line that actually changed.
def _system_prompt(payload: dict) -> str:
    for message in ((payload.get("model") or {}).get("messages") or []):
        if message.get("role") == "system":
            return message.get("content") or ""
    return ""


# Keys VAPI adds or rewrites on its own. Comparing them would report drift on
# every run and train whoever reads this to ignore the output.
IGNORED_KEYS = {
    "id", "orgId", "createdAt", "updatedAt", "isServerUrlSecretSet",
    "credentialIds", "keypadInputPlan", "compliancePlan", "artifactPlan",
}


def _summarise(value, depth: int = 0) -> str:
    """A short, readable rendering of a value for the diff output."""
    if isinstance(value, str) and len(value) > 60:
        return f"<{len(value)} chars>"
    if isinstance(value, list):
        return f"[{len(value)} items]"
    if isinstance(value, dict) and depth > 0:
        return "{...}"
    return repr(value)


def _diff(wanted, live, path: str = "") -> list[tuple[str, str, str]]:
    """Every field we send that differs from what VAPI holds.

    Compares the whole payload rather than a hand-listed set of fields. A
    curated list has now silently missed two changes in a row, the system prompt
    and then the endpointing plan, each time reporting "up to date" for
    assistants that were running old configuration. Only what we actually send
    is compared, so VAPI's own defaults do not show up as drift.
    """
    out: list[tuple[str, str, str]] = []

    if isinstance(wanted, dict):
        if not isinstance(live, dict):
            return [(path or "(root)", _summarise(live), _summarise(wanted))]
        for key, want_value in wanted.items():
            if key in IGNORED_KEYS:
                continue
            # VAPI never returns the webhook secret, so it always looks changed.
            if path == "server" and key == "secret":
                continue
            out.extend(_diff(want_value, live.get(key), f"{path}.{key}" if path else key))
        return out

    if isinstance(wanted, list):
        if not isinstance(live, list) or len(wanted) != len(live):
            return [(path, _summarise(live), _summarise(wanted))]
        for index, item in enumerate(wanted):
            out.extend(_diff(item, live[index], f"{path}[{index}]"))
        return out

    if wanted != live:
        return [(path, _summarise(live), _summarise(wanted))]
    return out


async def _live_assistant(assistant_id: str) -> dict | None:
    try:
        return await vapi._request("GET", f"/assistant/{assistant_id}")
    except vapi.VapiResourceMissing:
        return None


async def run(apply: bool, only_slug: str | None) -> int:
    async with SessionLocal() as db:
        query = select(Business).where(Business.is_active.is_(True))
        if only_slug:
            query = query.where(Business.slug == only_slug)
        businesses = (await db.execute(query.order_by(Business.created_at))).scalars().all()

        if not businesses:
            print("No active businesses found.")
            return 0

        changed = 0
        failed = 0

        for business in businesses:
            staff = (
                await db.execute(
                    select(StaffMember).where(
                        StaffMember.business_id == business.id,
                        StaffMember.is_active.is_(True),
                    )
                )
            ).scalars().all()

            wanted = vapi.build_assistant_payload(business, list(staff))
            print(f"\n{business.name} ({business.slug})")

            if not business.vapi_assistant_id:
                print("  no assistant yet", "-> would create" if not apply else "-> creating")
                if apply:
                    try:
                        business.vapi_assistant_id = await vapi.create_assistant(
                            business, list(staff)
                        )
                        await db.flush()
                        changed += 1
                        print(f"  created {business.vapi_assistant_id}")
                    except Exception as exc:  # noqa: BLE001
                        failed += 1
                        print(f"  FAILED: {exc}")
                continue

            live = await _live_assistant(business.vapi_assistant_id)
            if live is None:
                # The stored id is not in this account: a different API key, or
                # someone deleted it in the dashboard.
                print(f"  assistant {business.vapi_assistant_id} is missing from this account")
                if apply:
                    try:
                        business.vapi_assistant_id = await vapi.create_assistant(
                            business, list(staff)
                        )
                        await db.flush()
                        changed += 1
                        print(f"  recreated as {business.vapi_assistant_id}")
                    except Exception as exc:  # noqa: BLE001
                        failed += 1
                        print(f"  FAILED: {exc}")
                continue

            drift = _diff(wanted, live)

            if not drift:
                print("  up to date")
                continue

            for name, live_value, wanted_value in drift:
                print(f"  {name}: {live_value} -> {wanted_value}")

            if apply:
                try:
                    await vapi.update_assistant(business, list(staff))
                    changed += 1
                    print("  pushed")
                except Exception as exc:  # noqa: BLE001
                    failed += 1
                    print(f"  FAILED: {exc}")

        if apply:
            await db.commit()

    print()
    if apply:
        print(f"{changed} assistant(s) updated, {failed} failed.")
    else:
        print("Nothing was changed. Re-run with --apply to push.")
    return 1 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Re-push assistant config to VAPI.")
    parser.add_argument("--apply", action="store_true", help="Actually push the changes.")
    parser.add_argument("--slug", help="Limit to one business.")
    args = parser.parse_args()
    return asyncio.run(run(args.apply, args.slug))


if __name__ == "__main__":
    sys.exit(main())
