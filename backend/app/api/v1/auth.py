"""Authentication: login, refresh, logout, current user, password change.

Refresh tokens are stored server-side as SHA-256 hashes so sessions can be
revoked, and they are rotated on every use: a refresh token is single-use, and
presenting an already-used one is treated as a possible theft and revokes the
whole family.
"""

import hashlib
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from sqlalchemy import select, update

from app.config import get_settings
from app.core.deps import CurrentUserDep, DbSession, write_audit_log
from app.core.errors import (
    ConflictError,
    InvalidCredentialsError,
    TokenInvalidError,
)
from app.core.response import ok
from app.core.security import (
    create_token,
    decode_token,
    dummy_password_verify,
    hash_password,
    verify_password,
)
from app.db.models import RefreshToken, User
from app.schemas.auth import (
    ChangePasswordRequest,
    LoginRequest,
    RefreshRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _user_dict(user: User) -> dict:
    """Explicit projection. Never dump the ORM row: `password_hash` lives on it."""
    return {
        "id": user.id,
        "email": user.email,
        "full_name": user.full_name,
        "role": user.role.value,
        "clinic_id": user.clinic_id,
        "last_login_at": user.last_login_at.isoformat() if user.last_login_at else None,
    }


async def _issue_tokens(db, user: User) -> dict:
    settings = get_settings()
    access = create_token(
        user_id=user.id, clinic_id=user.clinic_id, role=user.role.value, token_type="access"
    )
    refresh = create_token(
        user_id=user.id, clinic_id=user.clinic_id, role=user.role.value, token_type="refresh"
    )

    payload = decode_token(refresh, expected_type="refresh")
    db.add(
        RefreshToken(
            user_id=user.id,
            token_hash=_hash_token(refresh),
            expires_at=datetime.fromtimestamp(payload["exp"], tz=timezone.utc),
        )
    )
    await db.flush()

    return {
        "access_token": access,
        "refresh_token": refresh,
        "token_type": "Bearer",
        "expires_in": settings.access_token_ttl_minutes * 60,
    }


@router.post("/login", summary="Sign in with email and password")
async def login(payload: LoginRequest, request: Request, db: DbSession) -> dict:
    """Authenticate and issue a token pair.

    Both "no such user" and "wrong password" return the same error after the
    same amount of work, so the endpoint cannot be used to enumerate which
    email addresses have accounts.
    """
    user = (
        await db.execute(select(User).where(User.email == payload.email.lower()))
    ).scalar_one_or_none()

    if user is None:
        dummy_password_verify()
        raise InvalidCredentialsError()

    if not verify_password(payload.password, user.password_hash):
        logger.info("Failed login for user %s", user.id)
        raise InvalidCredentialsError()

    if not user.is_active:
        raise InvalidCredentialsError("This account has been deactivated.")

    user.last_login_at = datetime.now(timezone.utc)
    tokens = await _issue_tokens(db, user)

    await write_audit_log(
        db, request, action="auth.login", clinic_id=user.clinic_id, resource_type="user", resource_id=user.id
    )
    await db.commit()

    return ok(
        {"user": _user_dict(user), "tokens": tokens},
        message="Signed in successfully.",
    )


@router.post("/refresh", summary="Exchange a refresh token for a new token pair")
async def refresh(payload: RefreshRequest, db: DbSession) -> dict:
    """Rotate the refresh token.

    Reuse detection: the presented token is revoked immediately. If it was
    already revoked, every other session for that user is revoked too, on the
    assumption that a token was stolen and replayed.
    """
    token_payload = decode_token(payload.refresh_token, expected_type="refresh")
    token_hash = _hash_token(payload.refresh_token)

    stored = (
        await db.execute(select(RefreshToken).where(RefreshToken.token_hash == token_hash))
    ).scalar_one_or_none()

    if stored is None:
        raise TokenInvalidError("This session is no longer valid. Please sign in again.")

    if stored.revoked_at is not None:
        logger.warning(
            "Refresh token reuse detected for user %s; revoking all sessions.", stored.user_id
        )
        await db.execute(
            update(RefreshToken)
            .where(RefreshToken.user_id == stored.user_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=datetime.now(timezone.utc))
        )
        await db.commit()
        raise TokenInvalidError("This session is no longer valid. Please sign in again.")

    if stored.expires_at <= datetime.now(timezone.utc):
        raise TokenInvalidError("Your session has expired. Please sign in again.")

    user = (
        await db.execute(select(User).where(User.id == token_payload["sub"]))
    ).scalar_one_or_none()
    if user is None or not user.is_active:
        raise TokenInvalidError("This session is no longer valid. Please sign in again.")

    stored.revoked_at = datetime.now(timezone.utc)
    tokens = await _issue_tokens(db, user)
    await db.commit()

    return ok({"user": _user_dict(user), "tokens": tokens}, message="Session refreshed.")


@router.post("/logout", summary="Revoke the current refresh token")
async def logout(payload: RefreshRequest, request: Request, user: CurrentUserDep, db: DbSession) -> dict:
    await db.execute(
        update(RefreshToken)
        .where(
            RefreshToken.token_hash == _hash_token(payload.refresh_token),
            RefreshToken.user_id == user.id,
        )
        .values(revoked_at=datetime.now(timezone.utc))
    )
    await write_audit_log(db, request, action="auth.logout", clinic_id=user.clinic_id)
    await db.commit()
    return ok(None, message="Signed out.")


@router.get("/me", summary="The signed-in user")
async def me(user: CurrentUserDep, db: DbSession) -> dict:
    row = (await db.execute(select(User).where(User.id == user.id))).scalar_one()
    return ok(_user_dict(row))


@router.post("/change-password", summary="Change your own password")
async def change_password(
    payload: ChangePasswordRequest, request: Request, user: CurrentUserDep, db: DbSession
) -> dict:
    row = (await db.execute(select(User).where(User.id == user.id))).scalar_one()

    if not verify_password(payload.current_password, row.password_hash):
        raise InvalidCredentialsError("Your current password is incorrect.")
    if payload.current_password == payload.new_password:
        raise ConflictError("The new password must be different from the current one.")

    row.password_hash = hash_password(payload.new_password)

    # Force every other session to re-authenticate with the new password.
    await db.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == row.id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=datetime.now(timezone.utc))
    )

    await write_audit_log(
        db, request, action="auth.password_changed", clinic_id=row.clinic_id, resource_type="user", resource_id=row.id
    )
    await db.commit()

    return ok(None, message="Password updated. Please sign in again on your other devices.")
