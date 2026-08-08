"""Shared FastAPI dependencies: authentication, roles, and tenant scoping.

This module is the security boundary. Two rules hold everywhere:

1. The active clinic id comes from the verified JWT, never from a path, query,
   or body parameter. A caller cannot ask for another clinic's data because
   there is no input that would let them express the request.

2. Single-object reads go through `scoped_get()`, which filters by primary key
   *and* clinic id. Fetch-then-check is not used: it is one forgotten `if` away
   from an IDOR, and it also distinguishes "exists but not yours" from "does not
   exist" through response timing.
"""

import logging
from dataclasses import dataclass
from typing import Annotated, Any, TypeVar

from fastapi import Depends, Query, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import (
    ClinicAccessDeniedError,
    ForbiddenError,
    InsufficientRoleError,
    NotFoundError,
    UnauthenticatedError,
)
from app.core.security import decode_token
from app.db.models import User, UserRole
from app.db.session import get_db

logger = logging.getLogger(__name__)

# auto_error=False so a missing header raises our envelope-shaped 401 rather
# than FastAPI's bare `{"detail": ...}`.
_bearer = HTTPBearer(auto_error=False)

DbSession = Annotated[AsyncSession, Depends(get_db)]


@dataclass(frozen=True)
class CurrentUser:
    """The authenticated caller, resolved from the token and re-checked in the DB."""

    id: str
    email: str
    full_name: str
    role: UserRole
    clinic_id: str | None

    @property
    def is_superadmin(self) -> bool:
        return self.role == UserRole.SUPERADMIN

    @property
    def is_owner(self) -> bool:
        return self.role in (UserRole.OWNER, UserRole.SUPERADMIN)


async def get_current_user(
    request: Request,
    db: DbSession,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)] = None,
) -> CurrentUser:
    """Authenticate the caller.

    The user row is loaded on every request rather than trusted from the token
    body. That costs one indexed lookup and buys immediate revocation: a
    deactivated account stops working now, not whenever its access token happens
    to expire.
    """
    if credentials is None or not credentials.credentials:
        raise UnauthenticatedError()

    payload = decode_token(credentials.credentials, expected_type="access")
    user_id = payload.get("sub", "")

    user = (
        await db.execute(select(User).where(User.id == user_id))
    ).scalar_one_or_none()

    if user is None or not user.is_active:
        # Same message either way: do not reveal whether the account exists.
        raise UnauthenticatedError("Your session is no longer valid. Please sign in again.")

    # If the token's clinic claim disagrees with the database, the user was moved
    # or their clinic was reassigned after the token was issued. Refuse it.
    token_clinic = payload.get("clinic_id")
    if token_clinic != user.clinic_id:
        logger.warning(
            "Token clinic claim %r does not match user %s clinic %r; rejecting.",
            token_clinic,
            user.id,
            user.clinic_id,
        )
        raise UnauthenticatedError("Your session is no longer valid. Please sign in again.")

    current = CurrentUser(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        role=user.role,
        clinic_id=user.clinic_id,
    )
    # Stashed for the audit-log helper and request logging.
    request.state.current_user = current
    return current


CurrentUserDep = Annotated[CurrentUser, Depends(get_current_user)]


# --------------------------------------------------------------------------- #
# Tenant scoping
# --------------------------------------------------------------------------- #
async def get_active_clinic_id(
    user: CurrentUserDep,
    clinic_id: Annotated[
        str | None,
        Query(
            description=(
                "Superadmin only: act on this clinic. Ignored for clinic users, "
                "who are always scoped to their own clinic."
            )
        ),
    ] = None,
) -> str:
    """Resolve the clinic every query in this request must filter by.

    For a clinic user this is their own clinic and the `clinic_id` query
    parameter is ignored outright, so passing someone else's id changes nothing.
    Only a superadmin can target another tenant, and doing so is auditable.
    """
    if user.is_superadmin:
        if clinic_id:
            return clinic_id
        raise ClinicAccessDeniedError(
            "Superadmin requests must specify which clinic to act on via ?clinic_id="
        )

    if not user.clinic_id:
        raise ForbiddenError("Your account is not linked to a clinic.")

    if clinic_id and clinic_id != user.clinic_id:
        logger.warning(
            "User %s attempted to access clinic %s but belongs to %s.",
            user.id,
            clinic_id,
            user.clinic_id,
        )
        raise ClinicAccessDeniedError()

    return user.clinic_id


ActiveClinic = Annotated[str, Depends(get_active_clinic_id)]


# --------------------------------------------------------------------------- #
# Role gates
# --------------------------------------------------------------------------- #
def require_role(*allowed: UserRole):
    """Dependency factory gating an endpoint on the caller's role.

    Superadmin passes every gate; encoding that here means no endpoint has to
    remember to include it in its own list.
    """

    async def _check(user: CurrentUserDep) -> CurrentUser:
        if user.role == UserRole.SUPERADMIN or user.role in allowed:
            return user
        raise InsufficientRoleError(
            f"This action requires one of: {', '.join(sorted(r.value for r in allowed))}."
        )

    return _check


RequireOwner = Annotated[CurrentUser, Depends(require_role(UserRole.OWNER))]
RequireSuperadmin = Annotated[CurrentUser, Depends(require_role(UserRole.SUPERADMIN))]


# --------------------------------------------------------------------------- #
# Pagination
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class PageParams:
    page: int
    page_size: int

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size


async def get_page_params(
    page: Annotated[int, Query(ge=1, description="1-indexed page number.")] = 1,
    page_size: Annotated[int, Query(ge=1, le=100, description="Items per page, max 100.")] = 20,
) -> PageParams:
    """Bounded pagination. The `le=100` cap is load protection: without it a
    caller can request every row in one query."""
    return PageParams(page=page, page_size=page_size)


Paging = Annotated[PageParams, Depends(get_page_params)]


# --------------------------------------------------------------------------- #
# Tenant-safe single-object fetch
# --------------------------------------------------------------------------- #
ModelT = TypeVar("ModelT")


async def scoped_get(
    db: AsyncSession,
    model: type[ModelT],
    resource_id: str,
    clinic_id: str,
    *,
    resource_name: str | None = None,
) -> ModelT:
    """Load one row by id, constrained to the active clinic.

    Prefer this over `db.get()` plus a manual ownership check. The clinic filter
    is part of the query, so there is no window in which the object exists in
    memory before anyone has verified the caller may see it. Missing and
    not-yours both raise the same 404, which keeps the endpoint from confirming
    that an id belongs to some other clinic.
    """
    stmt = select(model).where(
        model.id == resource_id,  # type: ignore[attr-defined]
        model.clinic_id == clinic_id,  # type: ignore[attr-defined]
    )
    obj = (await db.execute(stmt)).scalar_one_or_none()
    if obj is None:
        raise NotFoundError(resource_name or model.__name__)
    return obj


def client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else ""


async def write_audit_log(
    db: AsyncSession,
    request: Request,
    *,
    action: str,
    clinic_id: str | None = None,
    resource_type: str = "",
    resource_id: str = "",
    metadata: dict[str, Any] | None = None,
) -> None:
    """Append an audit row. Never raises: a logging failure must not roll back
    the business action it was recording."""
    from app.db.models import AuditLog

    try:
        user: CurrentUser | None = getattr(request.state, "current_user", None)
        db.add(
            AuditLog(
                clinic_id=clinic_id,
                actor_user_id=user.id if user else None,
                actor_label=user.email if user else "system",
                action=action,
                resource_type=resource_type,
                resource_id=resource_id,
                metadata_json=metadata or {},
                ip_address=client_ip(request),
                request_id=getattr(request.state, "request_id", ""),
            )
        )
        await db.flush()
    except Exception:  # noqa: BLE001
        logger.exception("Failed to write audit log for action=%s", action)
