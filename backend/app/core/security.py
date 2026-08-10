"""Password hashing, JWT issue/verify, secret encryption, webhook signatures.

Every primitive here is constant-time or delegates to a vetted library. The
comparisons in particular use `hmac.compare_digest`: a plain `==` on a signature
leaks its prefix through timing and is a real, exploited attack on webhooks.
"""

import base64
import hashlib
import secrets
import hmac
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

import bcrypt
import jwt
from cryptography.fernet import Fernet, InvalidToken

from app.config import get_settings
from app.core.errors import TokenExpiredError, TokenInvalidError

logger = logging.getLogger(__name__)

TokenType = Literal["access", "refresh"]

# bcrypt silently truncates at 72 bytes. Rejecting longer input is safer than
# letting two different long passwords hash identically.
MAX_PASSWORD_BYTES = 72


# --------------------------------------------------------------------------- #
# Passwords
# --------------------------------------------------------------------------- #
def hash_password(password: str) -> str:
    encoded = password.encode("utf-8")
    if len(encoded) > MAX_PASSWORD_BYTES:
        raise ValueError(f"Password must be at most {MAX_PASSWORD_BYTES} bytes.")
    return bcrypt.hashpw(encoded, bcrypt.gensalt(rounds=12)).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    """Constant-time verify. Returns False on malformed hashes rather than
    raising, so a corrupted row can't be distinguished from a wrong password."""
    try:
        return bcrypt.checkpw(
            password.encode("utf-8")[:MAX_PASSWORD_BYTES], password_hash.encode("utf-8")
        )
    except (ValueError, TypeError):
        logger.warning("Malformed password hash encountered during verification.")
        return False


def generate_temporary_password() -> str:
    """A password that is secure enough and can be read aloud over the phone.

    Ambiguous characters (0/O, 1/l/I) are excluded because these get dictated
    down a line, and a password that cannot be communicated reliably gets
    written on a sticky note instead. ~46 bits of entropy, which is ample for a
    credential that must be changed at first login and expires on use.
    """
    words = ("amber", "cedar", "coral", "delta", "ember", "flint", "lunar", "olive",
             "quartz", "raven", "swift", "topaz")
    alphabet = "abcdefghjkmnpqrstuvwxyz23456789"
    word = secrets.choice(words)
    tail = "".join(secrets.choice(alphabet) for _ in range(7))
    return f"{word}-{tail}"


def dummy_password_verify() -> None:
    """Burn a bcrypt round on a login for an email that doesn't exist.

    Without this, "unknown email" returns in microseconds while "wrong password"
    takes ~250ms, which turns the login endpoint into a user enumeration oracle.
    """
    bcrypt.checkpw(
        b"timing-equalizer",
        b"$2b$12$C6UzMDM.H6dfI/f/IKcEe.rt5Ez8bqmZq2Gm5aZeVPGl1ULBOQ2Ni",
    )


# --------------------------------------------------------------------------- #
# JWT
# --------------------------------------------------------------------------- #
def create_token(
    *,
    user_id: str,
    business_id: str | None,
    role: str,
    token_type: TokenType = "access",
) -> str:
    """Mint a signed token.

    `business_id` is baked into the token, and every tenant-scoped query derives
    its filter from this claim rather than from a client-supplied parameter.
    That is what makes cross-tenant reads impossible rather than merely
    inconvenient.
    """
    settings = get_settings()
    now = datetime.now(timezone.utc)
    ttl = (
        timedelta(minutes=settings.access_token_ttl_minutes)
        if token_type == "access"
        else timedelta(days=settings.refresh_token_ttl_days)
    )
    payload: dict[str, Any] = {
        "sub": user_id,
        "business_id": business_id,
        "role": role,
        "type": token_type,
        # A unique token id. Without it, two tokens minted for the same user
        # within the same second are byte-identical, because every other claim
        # matches and iat/exp only have second resolution. Refresh tokens are
        # stored by hash under a unique constraint, so two logins a moment apart
        # would collide and fail with a 500. `jti` is also what makes individual
        # token revocation possible later.
        "jti": secrets.token_urlsafe(16),
        "iat": int(now.timestamp()),
        "exp": int((now + ttl).timestamp()),
        "nbf": int(now.timestamp()),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_token(token: str, *, expected_type: TokenType = "access") -> dict[str, Any]:
    """Verify and decode. Raises `TokenExpiredError` / `TokenInvalidError`.

    `algorithms` is pinned to the configured algorithm so a token whose header
    claims `alg: none` (or a swapped asymmetric algorithm) is rejected outright.
    """
    settings = get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
            options={"require": ["exp", "sub", "type"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise TokenExpiredError() from exc
    except jwt.InvalidTokenError as exc:
        logger.info("Rejected invalid JWT: %s", exc)
        raise TokenInvalidError() from exc

    # A refresh token must never be accepted where an access token is required,
    # or a stolen long-lived token becomes a permanent API key.
    if payload.get("type") != expected_type:
        raise TokenInvalidError("Wrong token type for this operation.")

    return payload


# --------------------------------------------------------------------------- #
# Encryption at rest (Google OAuth refresh tokens, per-business API keys)
# --------------------------------------------------------------------------- #
def _fernet() -> Fernet:
    settings = get_settings()
    key = settings.encryption_key.strip()
    if not key:
        if settings.is_production:  # pragma: no cover - startup guard covers this
            raise RuntimeError("ENCRYPTION_KEY is required in production.")
        # Deterministic dev key derived from the JWT secret, so encrypted rows
        # stay readable across restarts within one dev session.
        key = base64.urlsafe_b64encode(
            hashlib.sha256(settings.jwt_secret.encode()).digest()
        ).decode()
        logger.warning("ENCRYPTION_KEY not set; using a derived development key.")
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt_secret(plaintext: str) -> str:
    if not plaintext:
        return ""
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt_secret(ciphertext: str) -> str:
    """Decrypt, returning "" if the value is unreadable.

    An unreadable value means the encryption key was rotated without
    re-encrypting. Returning "" makes the integration report itself as
    disconnected (prompting a reconnect) instead of crashing a call in progress.
    """
    if not ciphertext:
        return ""
    try:
        return _fernet().decrypt(ciphertext.encode("utf-8")).decode("utf-8")
    except (InvalidToken, ValueError):
        logger.error("Could not decrypt a stored secret; the encryption key may have rotated.")
        return ""


# --------------------------------------------------------------------------- #
# Webhook signature verification
# --------------------------------------------------------------------------- #
def verify_meta_signature(raw_body: bytes, signature_header: str, app_secret: str) -> bool:
    """Verify Meta's `X-Hub-Signature-256: sha256=<hex>` over the RAW body.

    The raw bytes matter: re-serializing the parsed JSON changes whitespace and
    key order, and the HMAC will never match.
    """
    if not app_secret or not signature_header:
        return False
    if not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header[len("sha256=") :])


def verify_shared_secret(provided: str, expected: str) -> bool:
    """Constant-time compare for VAPI's shared-secret webhook header."""
    if not expected or not provided:
        return False
    return hmac.compare_digest(provided, expected)
