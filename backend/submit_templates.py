#!/usr/bin/env python
"""Submit every WhatsApp template to Meta for approval.

Meta will not send a template that has not been approved, so nothing WhatsApp
does works until all of these exist. Entering fifteen of them by hand in
Business Manager is slow and, worse, lets the wording drift from what the code
actually sends: a template whose text differs from TEMPLATES is either rejected
at send time or delivers with the variables in the wrong order.

Submitting from the registry removes that gap entirely. What is approved is
what the code sends, because they are the same string.

Meta requires an example for every variable, so a sample value is supplied for
each known placeholder. Reviewers read these, and a template whose examples
look like test data is more likely to be rejected, so they are realistic.

Usage:
    python submit_templates.py              # show what would be submitted
    python submit_templates.py --apply      # submit for approval
    python submit_templates.py --status     # list templates and their status
"""

import argparse
import asyncio
import re
import sys

import httpx

from app.config import get_settings
from app.services.whatsapp import TEMPLATES

GRAPH = "https://graph.facebook.com/v21.0"

# Realistic values. A reviewer seeing "test test 123" is a reviewer looking for
# a reason to reject.
SAMPLES: dict[str, str] = {
    "customer_name": "Anjali Sharma",
    "business_name": "Sunrise Clinic",
    "appointment_time": "Tuesday 12 August, 4:30 PM",
    "staff_member_name": "Dr. Mehta",
    "business_phone": "+919876543210",
    "service_reason": "dental check-up",
    "calls_total": "12",
    "booked": "8",
    "cancelled": "2",
    "today_count": "5",
    "dashboard_url": "https://app.example.com",
    "reason": "a scheduling conflict",
    "old_time": "Monday 11 August, 3:00 PM",
    "new_time": "Tuesday 12 August, 4:30 PM",
}


def _examples(spec) -> list[str]:
    """One sample per {{n}}, in the order Meta will substitute them."""
    placeholders = sorted({int(n) for n in re.findall(r"\{\{(\d+)\}\}", spec.body)})
    values = []
    for index in placeholders:
        # variable_order is 1-indexed against the placeholders.
        key = spec.variable_order[index - 1] if index <= len(spec.variable_order) else ""
        values.append(SAMPLES.get(key, key.replace("_", " ") or "example"))
    return values


def _payload(spec) -> dict:
    components: list[dict] = [{"type": "BODY", "text": spec.body}]
    examples = _examples(spec)
    if examples:
        components[0]["example"] = {"body_text": [examples]}

    return {
        "name": spec.name,
        # UTILITY, not MARKETING: these follow a transaction the customer began
        # by calling. Marketing templates cost more and can be blocked by the
        # customer without affecting the appointment reminders they do want.
        "category": "UTILITY",
        "language": spec.language_code,
        "components": components,
    }


async def fetch_existing(client: httpx.AsyncClient, waba_id: str, token: str) -> dict[str, str]:
    existing: dict[str, str] = {}
    url = f"{GRAPH}/{waba_id}/message_templates?limit=200"
    while url:
        response = await client.get(url, headers={"Authorization": f"Bearer {token}"})
        if response.status_code != 200:
            print(f"  could not list templates: {response.status_code} {response.text[:200]}")
            return existing
        body = response.json()
        for item in body.get("data", []):
            existing[f"{item['name']}:{item['language']}"] = item.get("status", "UNKNOWN")
        url = (body.get("paging") or {}).get("next", "")
    return existing


async def run(apply: bool, status_only: bool) -> int:
    settings = get_settings()
    token = settings.whatsapp_access_token
    waba_id = settings.whatsapp_business_account_id

    if not (token and waba_id):
        print("WHATSAPP_ACCESS_TOKEN and WHATSAPP_BUSINESS_ACCOUNT_ID must be set.")
        return 1

    specs = list(dict.fromkeys(TEMPLATES.values()))

    async with httpx.AsyncClient(timeout=30.0) as client:
        existing = await fetch_existing(client, waba_id, token)

        if status_only:
            print(f"{len(existing)} template(s) on this WhatsApp account:\n")
            for spec in specs:
                key = f"{spec.name}:{spec.language_code}"
                print(f"  {spec.name:34} {existing.get(key, 'NOT SUBMITTED')}")
            return 0

        created = skipped = failed = 0

        for spec in specs:
            key = f"{spec.name}:{spec.language_code}"
            if key in existing:
                print(f"  {spec.name:34} already exists ({existing[key]})")
                skipped += 1
                continue

            if not apply:
                print(f"  {spec.name:34} would submit")
                continue

            response = await client.post(
                f"{GRAPH}/{waba_id}/message_templates",
                headers={"Authorization": f"Bearer {token}"},
                json=_payload(spec),
            )
            if response.status_code in (200, 201):
                print(f"  {spec.name:34} submitted")
                created += 1
            else:
                detail = response.text[:240]
                print(f"  {spec.name:34} FAILED {response.status_code}: {detail}")
                failed += 1

    print()
    if apply:
        print(f"{created} submitted, {skipped} already existed, {failed} failed.")
        if created:
            print("Approval usually takes a few hours. Re-run with --status to check.")
    else:
        print(f"{len(specs)} template(s) in the registry. Re-run with --apply to submit.")
    return 1 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Submit WhatsApp templates to Meta.")
    parser.add_argument("--apply", action="store_true", help="Actually submit them.")
    parser.add_argument("--status", action="store_true", help="Show approval status and exit.")
    args = parser.parse_args()
    return asyncio.run(run(args.apply, args.status))


if __name__ == "__main__":
    sys.exit(main())
