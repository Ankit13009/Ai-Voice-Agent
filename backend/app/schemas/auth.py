"""Auth payloads.

`UserOut` deliberately has no `password_hash` field. Response models are built
by explicit construction, never by dumping an ORM row, so a column added to
`User` later cannot leak by accident.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.db.models import UserRole


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=1, max_length=128)


class RefreshRequest(BaseModel):
    refresh_token: str = Field(..., min_length=10)


class UserOut(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    id: str
    email: str
    full_name: str
    role: UserRole
    clinic_id: str | None
    last_login_at: datetime | None = None


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "Bearer"
    expires_in: int = Field(..., description="Access token lifetime in seconds.")


class LoginResponse(BaseModel):
    user: UserOut
    tokens: TokenPair


class CreateUserRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=10, max_length=72)
    full_name: str = Field(default="", max_length=255)
    role: UserRole = UserRole.STAFF

    @field_validator("password")
    @classmethod
    def _strength(cls, v: str) -> str:
        """Minimum viable strength check.

        Length does most of the work; requiring a letter and a digit blocks the
        pathological cases ("aaaaaaaaaa") without pushing users toward the
        predictable "Password1!" that complex rulesets produce.
        """
        if len(v.encode("utf-8")) > 72:
            raise ValueError("Password must be at most 72 bytes.")
        if not any(c.isalpha() for c in v) or not any(c.isdigit() for c in v):
            raise ValueError("Password must contain at least one letter and one number.")
        return v


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(..., min_length=1, max_length=128)
    new_password: str = Field(..., min_length=10, max_length=72)

    @field_validator("new_password")
    @classmethod
    def _strength(cls, v: str) -> str:
        if not any(c.isalpha() for c in v) or not any(c.isdigit() for c in v):
            raise ValueError("Password must contain at least one letter and one number.")
        return v
