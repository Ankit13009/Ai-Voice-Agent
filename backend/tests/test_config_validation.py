"""The production start-up gate.

This check is the last thing standing between a misconfigured deploy and a
live phone line, so its edges are worth pinning down. In particular the
WhatsApp rule is conditional: the secret is only required once WhatsApp is
actually configured, because demanding it earlier blocked deploying the voice
agent before the Meta account existed. That relaxation is only safe while
verify_meta_signature() rejects an empty secret, so both halves are asserted
here together.
"""

import pytest

from app.config import Settings, validate_production_config
from app.core.security import verify_meta_signature


def _prod_settings(**overrides) -> Settings:
    """A production config that passes, so each test can break one thing."""
    base = dict(
        app_env="production",
        jwt_secret="j" * 64,
        encryption_key="e" * 44,
        database_url="postgresql+asyncpg://u:p@host/db",
        cors_origins="https://app.example.com",
        public_base_url="https://api.example.com",
        vapi_webhook_secret="v" * 43,
    )
    base.update(overrides)
    return Settings(**base)


def test_valid_production_config_starts() -> None:
    validate_production_config(_prod_settings())


def test_whatsapp_secret_not_required_when_whatsapp_is_unconfigured() -> None:
    """The voice agent must be deployable before WhatsApp exists."""
    validate_production_config(_prod_settings(whatsapp_app_secret=""))


@pytest.mark.parametrize(
    "enabling_field",
    ["whatsapp_access_token", "whatsapp_phone_number_id"],
)
def test_whatsapp_secret_required_once_whatsapp_is_configured(enabling_field: str) -> None:
    """Turning WhatsApp on without a signing secret must refuse to start."""
    settings = _prod_settings(**{enabling_field: "something", "whatsapp_app_secret": ""})
    with pytest.raises(RuntimeError, match="WHATSAPP_APP_SECRET"):
        validate_production_config(settings)


def test_unsigned_whatsapp_webhook_is_rejected_without_a_secret() -> None:
    """The guarantee that makes the conditional check safe.

    If this ever returns True, an unconfigured deployment would accept forged
    webhooks and the relaxation above becomes a hole.
    """
    body = b'{"entry":[]}'
    assert verify_meta_signature(body, "sha256=" + "0" * 64, "") is False
    assert verify_meta_signature(body, "", "") is False


@pytest.mark.parametrize(
    "override,expected",
    [
        ({"jwt_secret": ""}, "JWT_SECRET"),
        ({"jwt_secret": "tooshort"}, "JWT_SECRET"),
        ({"encryption_key": ""}, "ENCRYPTION_KEY"),
        ({"vapi_webhook_secret": ""}, "VAPI_WEBHOOK_SECRET"),
        ({"public_base_url": "http://api.example.com"}, "PUBLIC_BASE_URL"),
        ({"cors_origins": "*"}, "CORS origin"),
        ({"cors_origins": "http://app.example.com"}, "CORS origin"),
    ],
)
def test_insecure_production_config_refuses_to_start(override: dict, expected: str) -> None:
    with pytest.raises(RuntimeError, match=expected):
        validate_production_config(_prod_settings(**override))


def test_development_only_warns(caplog: pytest.LogCaptureFixture) -> None:
    """The same gaps must not be fatal locally, or nobody can run the app."""
    settings = Settings(app_env="development", jwt_secret="", vapi_webhook_secret="")
    validate_production_config(settings)  # must not raise
    assert "JWT_SECRET" in caplog.text
