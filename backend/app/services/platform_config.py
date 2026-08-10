"""Operator-editable credentials, stored encrypted in the database.

These began as environment variables, which meant setting up WhatsApp for a
client required someone with the hosting login. That is fine for one deployment
and wrong for a product being sold: onboarding should not queue behind a
developer editing Render.

Database first, environment second. The fallback matters for two reasons: an
existing deployment keeps working without anyone re-entering anything, and the
values can still be injected in CI or a test without a database write.

Values are cached for a short time because they are read on every outbound
message and a database round trip per WhatsApp send is waste. The cache is
cleared on write, so the dashboard shows the effect of a save immediately
rather than up to a minute later, which would read as the save having failed.
"""

import logging
import time

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.security import decrypt_secret, encrypt_secret
# Imported as a module, not as a name: binding SessionLocal at import time
# freezes whichever database was configured then, which silently bypasses a
# test's database and would hide a broken read behind passing tests.
from app.db import session as db_session
from app.db.models import PlatformSetting

logger = logging.getLogger(__name__)

# Every key an operator can set, mapped to the environment variable it falls
# back to. Anything not listed here cannot be written through the API, so a
# crafted key cannot be used to set an unrelated value.
MANAGED_KEYS: dict[str, str] = {
    "whatsapp_access_token": "whatsapp_access_token",
    "whatsapp_phone_number_id": "whatsapp_phone_number_id",
    "whatsapp_business_account_id": "whatsapp_business_account_id",
    "whatsapp_app_secret": "whatsapp_app_secret",
    "whatsapp_verify_token": "whatsapp_verify_token",
}

# Which of these are secret. Non-secret ids are still stored encrypted, but
# their values may be shown back to the operator, since re-typing a phone
# number id from memory is a common way to break an integration.
SECRET_KEYS = {"whatsapp_access_token", "whatsapp_app_secret"}

CACHE_TTL_SECONDS = 60
_cache: dict[str, str] = {}
_cache_expires_at: float = 0.0


def clear_cache() -> None:
    global _cache_expires_at
    _cache.clear()
    _cache_expires_at = 0.0


async def _load_all() -> dict[str, str]:
    global _cache_expires_at
    now = time.monotonic()
    if _cache and now < _cache_expires_at:
        return _cache

    values: dict[str, str] = {}
    try:
        async with db_session.SessionLocal() as db:
            rows = (await db.execute(select(PlatformSetting))).scalars().all()
            for row in rows:
                if row.key in MANAGED_KEYS and row.encrypted_value:
                    values[row.key] = decrypt_secret(row.encrypted_value)
    except Exception:  # noqa: BLE001
        # A database problem must not take WhatsApp down harder than it already
        # is; fall back to the environment and let the caller decide.
        logger.exception("Could not read platform settings; falling back to environment.")
        return {}

    _cache.clear()
    _cache.update(values)
    _cache_expires_at = now + CACHE_TTL_SECONDS
    return _cache


async def get_value(key: str) -> str:
    """Database value if set, otherwise the environment variable."""
    if key not in MANAGED_KEYS:
        raise KeyError(f"{key} is not an operator-managed setting.")

    stored = (await _load_all()).get(key, "")
    if stored:
        return stored
    return getattr(get_settings(), MANAGED_KEYS[key], "") or ""


async def set_values(db: AsyncSession, updates: dict[str, str], updated_by: str) -> list[str]:
    """Write settings. Returns the keys that changed.

    An empty string clears the stored value and lets the environment take over
    again, which is the only way back out of a bad paste without a deploy.
    """
    changed: list[str] = []

    for key, value in updates.items():
        if key not in MANAGED_KEYS:
            continue

        row = (
            await db.execute(select(PlatformSetting).where(PlatformSetting.key == key))
        ).scalar_one_or_none()
        if row is None:
            row = PlatformSetting(key=key)
            db.add(row)

        row.encrypted_value = encrypt_secret(value.strip()) if value.strip() else ""
        row.updated_by = updated_by
        changed.append(key)

    await db.flush()
    clear_cache()
    return changed


async def describe() -> dict[str, dict]:
    """What the dashboard shows: whether each value is set, never the secret itself."""
    stored = await _load_all()
    out: dict[str, dict] = {}

    for key in MANAGED_KEYS:
        db_value = stored.get(key, "")
        env_value = getattr(get_settings(), MANAGED_KEYS[key], "") or ""
        value = db_value or env_value

        out[key] = {
            "set": bool(value),
            "source": "dashboard" if db_value else ("environment" if env_value else "none"),
            # Enough to recognise a value, not enough to use it. Operators need
            # to tell "the token I pasted" from "a different token".
            "preview": (f"...{value[-4:]}" if len(value) > 4 else "") if key in SECRET_KEYS else value,
        }
    return out
