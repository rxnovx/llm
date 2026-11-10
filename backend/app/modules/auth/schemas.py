import re
from typing import Literal
from urllib.parse import urlparse
from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator
from app.core.locale import validate_time_zone

class LocalePreferencesRequest(BaseModel):
    locale: Literal["ko", "en"] = "ko"
    time_zone: str | None = Field(default=None, max_length=100)

    _time_zone = field_validator("time_zone")(validate_time_zone)


class RegisterRequest(LocalePreferencesRequest):
    email: EmailStr
    username: str = Field(min_length=3, max_length=50, pattern=r"^[a-zA-Z0-9_.-]+$")
    password: str = Field(min_length=8, max_length=128)
    display_name: str | None = Field(default=None, max_length=100)
    accept_terms: Literal[True]

    @field_validator("username")
    @classmethod
    def normalize_username(cls, value: str) -> str:
        return value.strip().lower()

    @field_validator("display_name")
    @classmethod
    def normalize_display_name(cls, value: str | None) -> str | None:
        normalized = value.strip() if value else None
        return normalized or None


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=20)


class LogoutRequest(BaseModel):
    refresh_token: str = Field(min_length=20)
    all_sessions: bool = False


class UserResponse(BaseModel):
    subject: str
    email: EmailStr
    username: str
    display_name: str | None
    avatar_url: str | None
    locale: Literal["ko", "en"] = "ko"
    time_zone: str | None = None
    is_active: bool
    registration_status: Literal["approved"]
    is_global_admin: bool
    is_email_verified: bool
    email_verification_required: bool
    created_at: int
    updated_at: int
    last_login_at: int | None
    has_password: bool
    connected_providers: list[str]

    model_config = ConfigDict(from_attributes=True)


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class AuthSessionResponse(TokenResponse):
    user: UserResponse


class AuthConfigResponse(BaseModel):
    email_verification_required: bool
    google_oauth_enabled: bool
    google_client_id: str | None
    password_min_length: int
    public_registration_enabled: bool
    public_registration_policy: Literal["open", "closed"]


class GoogleCredentialRequest(BaseModel):
    credential: str = Field(min_length=100, max_length=10_000)
    accept_terms: bool = False


class GoogleConnectionResponse(BaseModel):
    provider: Literal["google"] = "google"
    email: EmailStr
    connected_at: int


class PasswordSetRequest(BaseModel):
    new_password: str = Field(min_length=8, max_length=128)


class MessageResponse(BaseModel):
    message: str
    token: str | None = None


class TokenConfirmRequest(BaseModel):
    token: str = Field(min_length=20)


class EmailTokenRequest(BaseModel):
    email: EmailStr


class PasswordResetConfirmRequest(TokenConfirmRequest):
    new_password: str = Field(min_length=8, max_length=128)


class AccountRecoveryConfirmRequest(TokenConfirmRequest):
    new_password: str = Field(min_length=8, max_length=128)


class UserUpdateRequest(BaseModel):
    email: EmailStr | None = None
    username: str | None = Field(
        default=None,
        min_length=3,
        max_length=50,
        pattern=r"^[a-zA-Z0-9_.-]+$",
    )
    display_name: str | None = Field(default=None, max_length=100)
    avatar_url: str | None = Field(default=None, max_length=2048)

    @field_validator("username")
    @classmethod
    def normalize_username(cls, value: str | None) -> str | None:
        return value.strip().lower() if value else value

    @field_validator("display_name")
    @classmethod
    def normalize_display_name(cls, value: str | None) -> str | None:
        normalized = value.strip() if value else None
        return normalized or None

    @field_validator("avatar_url")
    @classmethod
    def normalize_avatar_url(cls, value: str | None) -> str | None:
        normalized = value.strip() if value else None
        if not normalized:
            return None
        if re.fullmatch(r"/users/avatars/[a-fA-F0-9]{24}/[a-fA-F0-9]{32}\.[a-zA-Z0-9]{1,10}", normalized):
            return normalized
        try:
            parsed = urlparse(normalized)
        except ValueError as error:
            raise ValueError("프로필 사진 주소가 올바르지 않습니다.") from error
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.username
            or parsed.password
        ):
            raise ValueError("http 또는 https 프로필 사진 주소를 입력해주세요.")
        return normalized


class EmailChangeRequest(BaseModel):
    new_email: EmailStr
    current_password: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
    )
    google_credential: str | None = Field(
        default=None,
        min_length=100,
        max_length=10_000,
    )

    @model_validator(mode="after")
    def validate_reauthentication(self):
        methods = (
            self.current_password is not None,
            self.google_credential is not None,
        )
        if sum(methods) != 1:
            raise ValueError(
                "현재 비밀번호 또는 Google 재인증 중 하나가 필요합니다."
            )
        return self


class PasswordChangeRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


class AccountDeactivateRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)


class SessionResponse(BaseModel):
    id: str
    created_at: int
    last_used_at: int | None
    expires_at: int
    ip_address: str | None
    user_agent: str | None
    is_current: bool = False
