from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import get_db
from app.modules.auth import service
from app.modules.auth.dependencies import Authenticated
from app.modules.auth.schemas import (
    AccountRecoveryConfirmRequest, AuthConfigResponse, AuthSessionResponse,
    EmailTokenRequest, GoogleCredentialRequest, LocalePreferencesRequest,
    LogoutRequest, MessageResponse, PasswordResetConfirmRequest, RefreshRequest,
    RegisterRequest, TokenConfirmRequest, UserResponse,
)

router = APIRouter(prefix="/auth", tags=["auth"])
DbSession = Annotated[Session, Depends(get_db)]


@router.get("/config", response_model=AuthConfigResponse)
def config():
    settings = get_settings()
    return AuthConfigResponse(email_verification_required=settings.email_verification_required,
        google_oauth_enabled=settings.google_oauth_enabled,
        google_client_id=settings.google_client_id if settings.google_oauth_enabled else None,
        password_min_length=settings.password_min_length,
        public_registration_enabled=settings.public_registration_enabled,
        public_registration_policy="open" if settings.public_registration_enabled else "closed")


@router.post("/register", response_model=UserResponse, status_code=201)
def register(data: RegisterRequest, request: Request, db: DbSession):
    return service.to_user_response(service.register(db, data, request))


@router.post("/login", response_model=AuthSessionResponse)
def login(request: Request, form: Annotated[OAuth2PasswordRequestForm, Depends()], db: DbSession):
    user = service.authenticate(db, form.username, form.password, request)
    return AuthSessionResponse(**service.issue_tokens(db, user, request).model_dump(),
                               user=service.to_user_response(user))


@router.post("/google", response_model=AuthSessionResponse)
def google(data: GoogleCredentialRequest, request: Request, db: DbSession):
    user = service.authenticate_google(db, data, request)
    return AuthSessionResponse(**service.issue_tokens(db, user, request).model_dump(),
                               user=service.to_user_response(user))


@router.post("/refresh", response_model=AuthSessionResponse)
def refresh(data: RefreshRequest, request: Request, db: DbSession):
    token, user = service.rotate_refresh_token_with_user(db, data.refresh_token, request)
    return AuthSessionResponse(**token.model_dump(), user=service.to_user_response(user))


@router.post("/logout", status_code=204)
def logout(data: LogoutRequest, request: Request, db: DbSession):
    # The opaque refresh token is itself sufficient to revoke its own session.
    # An expired/rotated access token must never prevent logout.
    service.logout(db, raw_refresh_token=data.refresh_token, all_sessions=data.all_sessions,
                   access_payload=None, request=request)
    return Response(status_code=204)


@router.get("/me", response_model=UserResponse)
def me(context: Authenticated):
    return service.to_user_response(context.user)


@router.patch("/me/locale", response_model=UserResponse)
def locale(data: LocalePreferencesRequest, db: DbSession, context: Authenticated):
    context.user.locale = data.locale
    context.user.time_zone = data.time_zone
    db.commit()
    return service.to_user_response(context.user)


@router.post("/email-verification/request", response_model=MessageResponse)
def request_verification(request: Request, db: DbSession, context: Authenticated):
    return service.request_email_verification(db, user=context.user, request=request)


@router.post("/email-verification/resend", response_model=MessageResponse)
def resend(data: EmailTokenRequest, request: Request, db: DbSession):
    return service.resend_email_verification(db, email=str(data.email), request=request)


@router.post("/email-verification/confirm", response_model=UserResponse)
def verify(data: TokenConfirmRequest, request: Request, db: DbSession):
    return service.to_user_response(service.confirm_email_verification(db, raw_token=data.token, request=request))


@router.post("/password-reset/request", response_model=MessageResponse)
def request_reset(data: EmailTokenRequest, request: Request, db: DbSession):
    return service.request_password_reset(db, email=str(data.email), request=request)


@router.post("/password-reset/confirm", response_model=MessageResponse)
def reset(data: PasswordResetConfirmRequest, request: Request, db: DbSession):
    service.confirm_password_reset(db, data=data, request=request)
    return MessageResponse(message="비밀번호를 재설정했습니다. 다시 로그인해주세요.")


@router.post("/account-recovery/request", response_model=MessageResponse)
def request_recovery(data: EmailTokenRequest, request: Request, db: DbSession):
    return service.request_account_recovery(db, email=str(data.email), request=request)


@router.post("/account-recovery/confirm", response_model=MessageResponse)
def recover(data: AccountRecoveryConfirmRequest, request: Request, db: DbSession):
    service.confirm_account_recovery(db, data=data, request=request)
    return MessageResponse(message="계정을 복구했습니다. 다시 로그인해주세요.")
