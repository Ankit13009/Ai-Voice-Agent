#!/usr/bin/env python
"""Print the environment variables to paste into Render.

Reuses the working values from your local `.env` (VAPI, Google, WhatsApp) and
generates fresh secrets for production.

Why fresh secrets: the local JWT_SECRET and ENCRYPTION_KEY have been in a file
on a laptop and in shell history. Production should not share them. The
consequence is that production starts with its own accounts and its own Google
connection, which is correct for a separate environment.

Usage:
    python generate_prod_env.py                     # placeholders for the URLs
    python generate_prod_env.py --api-url https://... --web-url https://...
"""

import argparse
import pathlib
import secrets

from cryptography.fernet import Fernet

# Copied across from local: these are external credentials, identical in both
# environments, and re-fetching them from Google and Meta would be busywork.
CARRY_OVER = [
    "VAPI_API_KEY",
    "VAPI_PUBLIC_KEY",
    "VAPI_WEBHOOK_SECRET",
    "VAPI_PHONE_NUMBER_ID",
    "GOOGLE_CLIENT_ID",
    "GOOGLE_CLIENT_SECRET",
    "WHATSAPP_ACCESS_TOKEN",
    "WHATSAPP_PHONE_NUMBER_ID",
    "WHATSAPP_BUSINESS_ACCOUNT_ID",
    "WHATSAPP_APP_SECRET",
    "WHATSAPP_VERIFY_TOKEN",
]


def read_local_env() -> dict[str, str]:
    path = pathlib.Path(".env")
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    return values


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-url", default="https://clinic-receptionist-api.onrender.com")
    parser.add_argument("--web-url", default="https://YOUR-APP.vercel.app")
    parser.add_argument("--database-url", default="PASTE_YOUR_NEON_CONNECTION_STRING")
    args = parser.parse_args()

    local = read_local_env()

    print("# " + "=" * 68)
    print("# Paste these into Render -> Environment")
    print("# " + "=" * 68)
    print()
    print("APP_ENV=production")
    print("PORT=8080")
    print("DEFAULT_TIMEZONE=Asia/Kolkata")
    print("REMINDER_SCHEDULER_ENABLED=true")
    print("REMINDER_POLL_SECONDS=300")
    print()
    print(f"DATABASE_URL={args.database_url}")
    print(f"PUBLIC_BASE_URL={args.api_url}")
    print(f"CORS_ORIGINS={args.web_url}")
    print(f"GOOGLE_OAUTH_REDIRECT_URI={args.api_url}/api/v1/integrations/google/callback")
    print()
    print("# Freshly generated. Do not reuse the local development values.")
    print(f"JWT_SECRET={secrets.token_urlsafe(48)}")
    print(f"ENCRYPTION_KEY={Fernet.generate_key().decode()}")
    print()
    print("# Carried over from your local .env")
    missing = []
    for key in CARRY_OVER:
        value = local.get(key, "")
        if value:
            print(f"{key}={value}")
        else:
            missing.append(key)

    if missing:
        print()
        print("# Still empty locally, so nothing to carry over:")
        for key in missing:
            print(f"#   {key}")

    print()
    print("# " + "=" * 68)
    print("# Reminders:")
    print("#  1. Add the redirect URI above to your Google OAuth client, exactly.")
    print(f"#  2. Point Meta's WhatsApp webhook at {args.api_url}/webhooks/whatsapp")
    print("#  3. Save Settings once in the dashboard to re-push the VAPI assistants.")
    print("#  4. TEST_CALLER_NUMBER is deliberately omitted: it is a development")
    print("#     convenience, and in production real calls carry a real caller ID.")
    print("# " + "=" * 68)


if __name__ == "__main__":
    main()
