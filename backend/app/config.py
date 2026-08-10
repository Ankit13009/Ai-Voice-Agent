"""Environment-driven settings.

Security posture: nothing here has a usable default in production. Secrets
default to empty and `validate_production_config()` refuses to boot if any are
missing when `APP_ENV=production`, so a misconfigured deploy fails loudly at
startup instead of quietly running with a signing key of "".
"""

import logging
import secrets
from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

# Query parameters libpq understands but asyncpg does not. Passing any of them
# through raises TypeError on connect, which reads as an unrelated crash.
_LIBPQ_ONLY_PARAMS = {
    "sslmode",
    "channel_binding",
    "sslrootcert",
    "sslcert",
    "sslkey",
    "gssencmode",
    "target_session_attrs",
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Server ---
    app_env: str = Field(default="development", description="development | staging | production")
    host: str = Field(default="0.0.0.0")
    port: int = Field(default=8000)
    log_level: str = Field(default="INFO")
    public_base_url: str = Field(default="", description="Public https origin, no trailing slash.")

    # --- CORS ---
    # Explicit allowlist only. A wildcard or a regex like `.*\.vercel\.app` lets
    # any attacker-controlled preview deployment read authenticated responses.
    cors_origins: str = Field(
        default="http://localhost:3000",
        description="Comma-separated exact origins allowed to call this API.",
    )

    # --- Auth ---
    jwt_secret: str = Field(default="", description="HS256 signing key. Min 32 chars in prod.")
    jwt_algorithm: str = Field(default="HS256")
    access_token_ttl_minutes: int = Field(default=30)
    refresh_token_ttl_days: int = Field(default=14)

    # Fernet key for encrypting third-party OAuth tokens at rest.
    # Generate with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    encryption_key: str = Field(default="")

    # --- Database ---
    database_url: str = Field(
        default="",
        description="Postgres DSN. Falls back to local SQLite when empty (dev only).",
    )

    # --- VAPI (voice agent) ---
    vapi_api_key: str = Field(default="", description="Private key. Server-side only.")
    # Safe to expose to the browser: it can only start a web call against an
    # assistant, not read or modify anything. Powers the dashboard test call.
    vapi_public_key: str = Field(default="")
    vapi_webhook_secret: str = Field(default="", description="Shared secret on VAPI's server webhook.")
    vapi_phone_number_id: str = Field(default="", description="Default VAPI number id for provisioning.")
    # Browser test calls carry no caller ID, because nobody dialled a number. On
    # a real call the phone system supplies one and the agent never asks. Without
    # a stand-in, browser testing exercises a branch that production never hits
    # and hides the flow that actually matters. Development only.
    test_caller_number: str = Field(default="")

    # --- Google Calendar ---
    google_client_id: str = Field(default="")
    google_client_secret: str = Field(default="")
    google_oauth_redirect_uri: str = Field(default="")

    # --- WhatsApp (Meta Cloud API, not WATI) ---
    whatsapp_access_token: str = Field(default="", description="Long-lived system-user token.")
    whatsapp_phone_number_id: str = Field(default="")
    whatsapp_business_account_id: str = Field(default="")
    whatsapp_app_secret: str = Field(default="", description="Verifies X-Hub-Signature-256.")
    whatsapp_verify_token: str = Field(default="", description="Echoed during webhook setup.")

    # --- Error reporting (optional) ---
    # Render's free tier keeps no log history, so an exception at 3pm is gone by
    # the time a client reports it at 5pm. Unset means simply off.
    sentry_dsn: str = Field(default="", description="Leave empty to disable error reporting.")

    # --- Scheduling ---
    default_timezone: str = Field(default="Asia/Kolkata")
    reminder_scheduler_enabled: bool = Field(default=True)
    reminder_poll_seconds: int = Field(default=300)

    @field_validator("app_env")
    @classmethod
    def _normalize_env(cls, v: str) -> str:
        return v.strip().lower()

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def dashboard_url(self) -> str:
        """Where to send an owner who needs to fix something.

        Derived from the first allowed CORS origin rather than configured
        separately: that value is already the deployed dashboard, and a second
        setting would only create a way for the two to disagree and for links
        in messages to point somewhere dead.
        """
        origins = self.cors_origin_list
        return origins[0] if origins else ""

    @property
    def sqlalchemy_url(self) -> str:
        """Normalize the DSN to an async driver.

        Hosting providers hand out `postgres://` or `postgresql://` URLs; SQLAlchemy's
        async engine needs the `+asyncpg` driver spelled out.
        """
        url = self.database_url.strip()
        if not url:
            return "sqlite+aiosqlite:///./receptionist.db"
        if url.startswith("postgres://"):
            url = "postgresql://" + url[len("postgres://") :]
        if url.startswith("postgresql://"):
            url = "postgresql+asyncpg://" + url[len("postgresql://") :]
        # asyncpg takes its TLS settings through connect_args and raises on
        # libpq-only query parameters. Hosted Postgres providers hand these out
        # by default (Neon appends both sslmode and channel_binding), so a URL
        # copied straight from their dashboard would fail at connect time.
        if "+asyncpg" in url and "?" in url:
            base, _, query = url.partition("?")
            kept = [
                part
                for part in query.split("&")
                if part and part.split("=")[0] not in _LIBPQ_ONLY_PARAMS
            ]
            url = base + ("?" + "&".join(kept) if kept else "")
        return url


def validate_production_config(settings: Settings) -> None:
    """Fail fast on a production deploy that is missing security-critical config.

    Called unconditionally from the startup hook, but only *fatal* when
    `APP_ENV=production`. In development the same gaps are logged as warnings so
    contributors can run the app without a full credential set, while still
    seeing exactly what a production deploy would reject.
    """
    problems: list[str] = []

    if not settings.jwt_secret:
        problems.append("JWT_SECRET is not set (tokens would be unsigned).")
    elif len(settings.jwt_secret) < 32:
        problems.append("JWT_SECRET is shorter than 32 characters.")

    if not settings.encryption_key:
        problems.append("ENCRYPTION_KEY is not set (OAuth tokens would be stored in plaintext).")

    if not settings.database_url:
        problems.append("DATABASE_URL is not set (would fall back to local SQLite).")

    for origin in settings.cors_origin_list:
        if origin == "*" or origin.startswith("http://"):
            problems.append(f"CORS origin {origin!r} is not a plain https origin.")

    if not settings.public_base_url.startswith("https://"):
        problems.append("PUBLIC_BASE_URL must be an https URL in production.")

    if not settings.vapi_webhook_secret:
        problems.append("VAPI_WEBHOOK_SECRET is not set (webhooks would be unauthenticated).")

    # Only demanded once WhatsApp is actually wired up. Requiring it always
    # would block deploying the phone agent before the Meta account exists,
    # and it is safe to defer: verify_meta_signature() returns False on an
    # empty secret, so an unconfigured deployment rejects every webhook rather
    # than trusting it. The moment a token or phone number id appears, the
    # endpoint is live and the secret becomes mandatory.
    whatsapp_configured = bool(
        settings.whatsapp_access_token or settings.whatsapp_phone_number_id
    )
    if whatsapp_configured and not settings.whatsapp_app_secret:
        problems.append(
            "WHATSAPP_APP_SECRET is not set, but WhatsApp is configured "
            "(webhooks would be unauthenticated)."
        )

    if not problems:
        return

    if settings.is_production:
        raise RuntimeError(
            "Refusing to start in production with insecure configuration:\n  - "
            + "\n  - ".join(problems)
        )

    logger.warning(
        "Development mode. These would block a production deploy:\n  - %s",
        "\n  - ".join(problems),
    )


@lru_cache
def get_settings() -> Settings:
    settings = Settings()

    # Development convenience: generate an ephemeral signing key so the app runs
    # out of the box. Tokens do not survive a restart, which is the correct
    # tradeoff locally and is refused outright in production above.
    if not settings.jwt_secret and not settings.is_production:
        settings.jwt_secret = secrets.token_urlsafe(48)
        logger.warning(
            "JWT_SECRET not set; generated an ephemeral development key. "
            "All sessions are invalidated on restart."
        )

    return settings
