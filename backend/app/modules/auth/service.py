"""Account-only extraction from my-note-app; see PORTING.md."""
import json
import re
import time
from datetime import timedelta
from fastapi import HTTPException, Request, status
from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.core.config import get_settings
from app.core.security import (create_access_token, create_one_time_token, create_refresh_token, hash_password, hash_refresh_token, hash_secret, password_hash_needs_upgrade, validate_password_strength, verify_password)
from app.modules.auth import audit, rate_limit, repository
from app.modules.auth import email as email_delivery
from app.modules.auth.google import verify_google_credential
from app.modules.auth.models import OAuthIdentity, OneTimeToken, RefreshSession, SessionFamily, User
from app.modules.auth.schemas import (AccountRecoveryConfirmRequest, GoogleConnectionResponse, GoogleCredentialRequest, MessageResponse, PasswordChangeRequest, PasswordResetConfirmRequest, PasswordSetRequest, RegisterRequest, SessionResponse, TokenResponse, UserResponse, UserUpdateRequest)

EMAIL_VERIFICATION = "email_verification"
PASSWORD_RESET = "password_reset"
ACCOUNT_RECOVERY = "account_recovery"
OAUTH_ONLY_PASSWORD_PREFIX = "!oauth-only:"
GOOGLE_PROVIDER = "google"

def public_registration_policy(db: Session) -> str:
    return "open" if get_settings().public_registration_enabled else "closed"

def to_user_response(user: User, db: Session | None = None) -> UserResponse:
    return UserResponse(
        subject=user.id, email=user.email, username=user.username,
        display_name=user.display_name, avatar_url=user.avatar_url,
        locale=user.locale or "ko", time_zone=user.time_zone,
        is_active=user.is_active, registration_status=user.registration_status,
        is_global_admin=user.is_global_admin,
        is_email_verified=user.email_verified_at is not None,
        email_verification_required=get_settings().email_verification_required,
        created_at=user.created_at, updated_at=user.updated_at,
        last_login_at=user.last_login_at, has_password=has_usable_password(user),
        connected_providers=sorted(identity.provider for identity in user.oauth_identities),
    )

def register(db: Session, data: RegisterRequest, request: Request) -> User:
    settings = get_settings()
    if not settings.public_registration_enabled:
        raise _error(403, "public_registration_disabled", "현재 공개 회원가입이 비활성화되어 있습니다.")
    key = audit.request_metadata(request)[0] or "unknown"
    rate_limit.check_rate_limit(db, action="auth.register", key=key,
        attempts=settings.registration_rate_limit_attempts,
        window_seconds=settings.registration_rate_limit_window_seconds)
    rate_limit.record_rate_limit_event(db, action="auth.register", key=key, succeeded=False)
    db.commit()
    _validate_new_password(data.password)
    email, username = str(data.email).lower(), data.username.lower()
    if db.scalar(select(User).where(or_(User.email == email, User.username == username))):
        raise _error(409, "account_conflict", "이미 사용 중인 이메일 또는 사용자명입니다.")
    now = int(time.time())
    user = User(email=email, username=username, display_name=data.display_name,
        locale=data.locale, time_zone=data.time_zone, password_hash=hash_password(data.password),
        is_active=True, registration_status="approved", terms_accepted_at=now,
        email_verified_at=None if settings.email_verification_required else now)
    try:
        db.add(user)
        db.flush()
        audit.add_audit_log(db, action="auth.register", actor_user_id=user.id,
            target_user_id=user.id, request=request)
        db.commit()
    except IntegrityError as error:
        db.rollback()
        raise _error(409, "account_conflict", "이미 사용 중인 이메일 또는 사용자명입니다.") from error
    db.refresh(user)
    if settings.email_verification_required:
        token = issue_one_time_token(db, user=user, purpose=EMAIL_VERIFICATION,
            expire_minutes=settings.email_verification_token_expire_minutes)
        if not email_delivery.send_verification_email(user.email, token):
            raise _error(503, "email_delivery_failed", "계정은 생성되었지만 인증 메일을 보내지 못했습니다. 인증 메일을 다시 요청해주세요.")
    return user

def _error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"code": code, "message": message},
    )


def _request_key(request: Request, subject: str) -> str:
    ip_address = audit.request_metadata(request)[0] or "unknown"
    return f"{subject.strip().lower()}|{ip_address}"


def registration_access_error(user: User) -> tuple[str, str] | None:
    if user.registration_status == "pending":
        return (
            "registration_approval_pending",
            "가입 신청이 접수되어 관리자 승인을 기다리고 있습니다.",
        )
    if user.registration_status == "rejected":
        return (
            "registration_rejected",
            "가입 신청이 승인되지 않았습니다. 운영자에게 문의해주세요.",
        )
    if user.registration_status != "approved":
        return (
            "registration_review_required",
            "가입 상태를 확인할 수 없어 관리자 검토가 필요합니다.",
        )
    return None


def _validate_new_password(password: str) -> None:
    try:
        validate_password_strength(password)
    except ValueError as error:
        raise _error(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "weak_password",
            str(error),
        ) from error


def has_usable_password(user: User) -> bool:
    return not user.password_hash.startswith(OAUTH_ONLY_PASSWORD_PREFIX)


def authenticate(
    db: Session,
    login_id: str,
    password: str,
    request: Request,
) -> User:
    settings = get_settings()
    rate_key = _request_key(request, login_id)
    ip_key = audit.request_metadata(request)[0] or "unknown"
    account_key = login_id.strip().lower()
    rate_limit.check_rate_limit(
        db,
        action="login",
        key=rate_key,
        attempts=settings.login_rate_limit_attempts,
        window_seconds=settings.login_rate_limit_window_seconds,
        failures_only=True,
    )
    rate_limit.check_rate_limit(
        db,
        action="login.ip",
        key=ip_key,
        attempts=settings.login_ip_rate_limit_attempts,
        window_seconds=settings.login_rate_limit_window_seconds,
        failures_only=True,
    )
    rate_limit.check_rate_limit(
        db,
        action="login.account",
        key=account_key,
        attempts=settings.login_account_rate_limit_attempts,
        window_seconds=settings.login_account_rate_limit_window_seconds,
        failures_only=True,
    )

    user = repository.find_user_by_login(db, login_id)
    if (
        not user
        or not has_usable_password(user)
        or not verify_password(password, user.password_hash)
    ):
        rate_limit.record_rate_limit_event(
            db,
            action="login",
            key=rate_key,
            succeeded=False,
        )
        rate_limit.record_rate_limit_event(
            db,
            action="login.ip",
            key=ip_key,
            succeeded=False,
        )
        rate_limit.record_rate_limit_event(
            db,
            action="login.account",
            key=account_key,
            succeeded=False,
        )
        audit.add_audit_log(
            db,
            action="auth.login",
            outcome="failure",
            target_user_id=user.id if user else None,
            request=request,
        )
        db.commit()
        raise _error(
            status.HTTP_401_UNAUTHORIZED,
            "invalid_credentials",
            "아이디 또는 비밀번호가 올바르지 않습니다.",
        )
    registration_error = registration_access_error(user)
    if registration_error:
        code, message = registration_error
        audit.add_audit_log(
            db,
            action="auth.login",
            outcome="blocked",
            target_user_id=user.id,
            request=request,
            details={"reason": user.registration_status},
        )
        db.commit()
        raise _error(status.HTTP_403_FORBIDDEN, code, message)
    if not user.is_active:
        audit.add_audit_log(
            db,
            action="auth.login",
            outcome="blocked",
            target_user_id=user.id,
            request=request,
            details={"reason": "inactive"},
        )
        db.commit()
        raise _error(
            status.HTTP_403_FORBIDDEN,
            "account_inactive",
            "비활성화된 계정입니다. 계정 복구를 진행해주세요.",
        )
    if settings.email_verification_required and user.email_verified_at is None:
        audit.add_audit_log(
            db,
            action="auth.login",
            outcome="blocked",
            target_user_id=user.id,
            request=request,
            details={"reason": "email_unverified"},
        )
        db.commit()
        raise _error(
            status.HTTP_403_FORBIDDEN,
            "email_verification_required",
            "이메일 인증 후 로그인할 수 있습니다.",
        )

    if password_hash_needs_upgrade(user.password_hash):
        user.password_hash = hash_password(password)
    now = int(time.time())
    user.last_login_at = now
    user.updated_at = now
    rate_limit.record_rate_limit_event(
        db,
        action="login",
        key=rate_key,
        succeeded=True,
    )
    audit.add_audit_log(
        db,
        action="auth.login",
        actor_user_id=user.id,
        target_user_id=user.id,
        request=request,
    )
    db.commit()
    return user


def _google_claims(credential: str) -> dict[str, object]:
    settings = get_settings()
    if not settings.google_oauth_enabled or not settings.google_client_id:
        raise _error(
            status.HTTP_404_NOT_FOUND,
            "google_oauth_disabled",
            "Google 로그인이 비활성화되어 있습니다.",
        )
    try:
        return verify_google_credential(
            credential,
            client_id=settings.google_client_id,
            hosted_domain=settings.google_hosted_domain,
        )
    except ValueError as error:
        raise _error(
            status.HTTP_401_UNAUTHORIZED,
            "invalid_google_credential",
            str(error),
        ) from error


def _available_google_username(db: Session, email: str, subject: str) -> str:
    local = email.split("@", 1)[0].lower()
    base = re.sub(r"[^a-z0-9_.-]+", "-", local).strip("._-") or "google-user"
    base = base[:40]
    candidate = base
    suffix = subject[-8:].lower()
    attempt = 0
    while repository.find_user_by_login(db, candidate):
        attempt += 1
        candidate = f"{base[:39]}-{suffix}" if attempt == 1 else f"{base[:35]}-{suffix}-{attempt}"
    return candidate[:50]


def authenticate_google(
    db: Session,
    data: GoogleCredentialRequest,
    request: Request,
    *,
    return_pending_user: bool = False,
) -> User:
    settings = get_settings()
    rate_key = _request_key(request, "google")
    rate_limit.check_rate_limit(
        db,
        action="auth.google",
        key=rate_key,
        attempts=max(settings.login_rate_limit_attempts * 2, 10),
        window_seconds=settings.login_rate_limit_window_seconds,
        failures_only=True,
    )
    try:
        claims = _google_claims(data.credential)
    except HTTPException:
        rate_limit.record_rate_limit_event(
            db,
            action="auth.google",
            key=rate_key,
            succeeded=False,
        )
        db.commit()
        raise
    subject = str(claims["sub"])
    email = str(claims["email"]).strip().lower()
    identity = repository.find_oauth_identity(
        db,
        provider=GOOGLE_PROVIDER,
        provider_subject=subject,
    )
    now = int(time.time())
    approval_requested = False
    if identity:
        user = identity.user
        registration_error = registration_access_error(user)
        if registration_error:
            code, message = registration_error
            audit.add_audit_log(
                db,
                action="auth.google",
                outcome="blocked",
                target_user_id=user.id,
                request=request,
                details={"reason": user.registration_status},
            )
            db.commit()
            raise _error(status.HTTP_403_FORBIDDEN, code, message)
        if not user.is_active:
            raise _error(
                status.HTTP_403_FORBIDDEN,
                "account_inactive",
                "비활성화된 계정입니다. 계정 복구를 진행해주세요.",
            )
        identity.provider_email = email
        identity.updated_at = now
    else:
        existing = repository.find_user_by_email(db, email)
        if existing:
            audit.add_audit_log(
                db,
                action="auth.google",
                outcome="blocked",
                target_user_id=existing.id,
                request=request,
                details={"reason": "link_required"},
            )
            db.commit()
            raise _error(
                status.HTTP_409_CONFLICT,
                "google_account_link_required",
                "같은 이메일의 LLM 계정이 있습니다. 비밀번호로 로그인한 뒤 계정 설정에서 Google을 연결해주세요.",
            )
        registration_policy = public_registration_policy(db)
        if registration_policy == "closed":
            raise _error(
                status.HTTP_403_FORBIDDEN,
                "public_registration_disabled",
                "현재 공개 회원가입이 비활성화되어 있습니다.",
            )
        if not data.accept_terms:
            raise _error(
                status.HTTP_428_PRECONDITION_REQUIRED,
                "google_terms_required",
                "Google 계정으로 처음 가입하려면 서비스 약관에 동의해야 합니다.",
            )
        rate_limit.record_rate_limit_event(
            db,
            action="auth.google",
            key=rate_key,
            succeeded=False,
        )
        db.commit()
        approval_requested = registration_policy == "approval"
        user = User(
            email=email,
            username=_available_google_username(db, email, subject),
            display_name=(
                str(claims["name"]).strip()[:100]
                if isinstance(claims.get("name"), str)
                else None
            ),
            avatar_url=(
                str(claims["picture"])[:2048]
                if isinstance(claims.get("picture"), str)
                else None
            ),
            password_hash=f"{OAUTH_ONLY_PASSWORD_PREFIX}{hash_secret(create_one_time_token())}",
            is_active=not approval_requested,
            registration_status=(
                "pending" if approval_requested else "approved"
            ),
            email_verified_at=now,
            terms_accepted_at=now,
            created_at=now,
            updated_at=now,
        )
        db.add(user)
        db.flush()
        identity = OAuthIdentity(
            user_id=user.id,
            provider=GOOGLE_PROVIDER,
            provider_subject=subject,
            provider_email=email,
            created_at=now,
            updated_at=now,
        )
        db.add(identity)
        audit.add_audit_log(
            db,
            action="auth.register.google",
            actor_user_id=user.id,
            target_user_id=user.id,
            request=request,
            details={
                "registration_policy": registration_policy,
                "registration_status": user.registration_status,
            },
        )

    if approval_requested:
        rate_limit.record_rate_limit_event(
            db,
            action="auth.google",
            key=rate_key,
            succeeded=True,
        )
        db.commit()
        db.refresh(user)
        if return_pending_user:
            return user
        raise _error(
            status.HTTP_403_FORBIDDEN,
            "registration_approval_pending",
            "가입 신청이 접수되어 관리자 승인을 기다리고 있습니다.",
        )

    user.last_login_at = now
    user.updated_at = now
    rate_limit.record_rate_limit_event(
        db,
        action="auth.google",
        key=rate_key,
        succeeded=True,
    )
    audit.add_audit_log(
        db,
        action="auth.login.google",
        actor_user_id=user.id,
        target_user_id=user.id,
        request=request,
    )
    db.commit()
    db.refresh(user)
    return user


def link_google_identity(
    db: Session,
    *,
    user: User,
    data: GoogleCredentialRequest,
    request: Request,
) -> GoogleConnectionResponse:
    claims = _google_claims(data.credential)
    subject = str(claims["sub"])
    email = str(claims["email"]).strip().lower()
    existing = repository.find_oauth_identity(
        db,
        provider=GOOGLE_PROVIDER,
        provider_subject=subject,
    )
    if existing and existing.user_id != user.id:
        raise _error(
            status.HTTP_409_CONFLICT,
            "google_identity_in_use",
            "이 Google 계정은 다른 LLM 계정에 연결되어 있습니다.",
        )
    current = repository.find_user_oauth_identity(
        db,
        user_id=user.id,
        provider=GOOGLE_PROVIDER,
    )
    now = int(time.time())
    if current and current.provider_subject != subject:
        raise _error(
            status.HTTP_409_CONFLICT,
            "google_provider_already_linked",
            "이미 다른 Google 계정이 연결되어 있습니다. 기존 연결을 먼저 해제해주세요.",
        )
    identity = current or existing
    if identity:
        identity.provider_email = email
        identity.updated_at = now
    else:
        identity = OAuthIdentity(
            user_id=user.id,
            provider=GOOGLE_PROVIDER,
            provider_subject=subject,
            provider_email=email,
            created_at=now,
            updated_at=now,
        )
        db.add(identity)
    audit.add_audit_log(
        db,
        action="user.oauth.link",
        actor_user_id=user.id,
        target_user_id=user.id,
        request=request,
        details={"provider": GOOGLE_PROVIDER},
    )
    db.commit()
    return GoogleConnectionResponse(
        email=email,
        connected_at=identity.created_at,
    )


def unlink_google_identity(
    db: Session,
    *,
    user: User,
    request: Request,
) -> None:
    identity = repository.find_user_oauth_identity(
        db,
        user_id=user.id,
        provider=GOOGLE_PROVIDER,
    )
    if not identity:
        raise _error(
            status.HTTP_404_NOT_FOUND,
            "google_identity_not_linked",
            "연결된 Google 계정이 없습니다.",
        )
    if not has_usable_password(user) and len(user.oauth_identities) <= 1:
        raise _error(
            status.HTTP_409_CONFLICT,
            "last_login_method",
            "로그인 수단이 없어지지 않도록 먼저 비밀번호를 설정해주세요.",
        )
    db.delete(identity)
    audit.add_audit_log(
        db,
        action="user.oauth.unlink",
        actor_user_id=user.id,
        target_user_id=user.id,
        request=request,
        details={"provider": GOOGLE_PROVIDER},
    )
    db.commit()


def set_password(
    db: Session,
    *,
    user: User,
    data: PasswordSetRequest,
    request: Request,
) -> None:
    if has_usable_password(user):
        raise _error(
            status.HTTP_409_CONFLICT,
            "password_already_set",
            "이미 비밀번호가 설정되어 있습니다. 비밀번호 변경을 이용해주세요.",
        )
    _validate_new_password(data.new_password)
    now = int(time.time())
    user.password_hash = hash_password(data.new_password)
    user.auth_version += 1
    user.updated_at = now
    repository.revoke_all_sessions(db, user.id, now)
    audit.add_audit_log(
        db,
        action="user.password.set",
        actor_user_id=user.id,
        target_user_id=user.id,
        request=request,
    )
    db.commit()


def issue_tokens(
    db: Session,
    user: User,
    request: Request,
    *,
    refresh_expires_at: int | None = None,
    family_id: str | None = None,
) -> TokenResponse:
    registration_error = registration_access_error(user)
    if registration_error or not user.is_active:
        code, message = registration_error or (
            "account_inactive",
            "비활성화된 계정입니다.",
        )
        raise _error(status.HTTP_403_FORBIDDEN, code, message)
    settings = get_settings()
    now = int(time.time())
    refresh_token = create_refresh_token()
    ip_address, user_agent = audit.request_metadata(request)
    default_refresh_expires_at = int(
        time.time()
        + timedelta(days=settings.refresh_token_days).total_seconds()
    )
    if refresh_expires_at is not None and refresh_expires_at <= now:
        raise _error(
            status.HTTP_401_UNAUTHORIZED,
            "session_expired",
            "로그인 세션이 만료되었습니다. 다시 로그인해주세요.",
        )
    session = RefreshSession(
        user_id=user.id,
        token_hash=hash_refresh_token(refresh_token),
        expires_at=min(
            default_refresh_expires_at,
            refresh_expires_at or default_refresh_expires_at,
        ),
        last_used_at=now,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    db.add(session)
    db.flush()
    db.add(SessionFamily(session_id=session.id, family_id=family_id or session.id))
    access_token, expires_in = create_access_token(
        subject=user.id,
        claims={
            "email": user.email,
            "username": user.username,
            "is_global_admin": user.is_global_admin,
            "ver": user.auth_version,
            "sid": session.id,
        },
    )
    db.commit()
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=expires_in,
    )


def rotate_refresh_token_with_user(
    db: Session,
    raw_token: str,
    request: Request,
) -> tuple[TokenResponse, User]:
    """Rotate once and return the already-loaded user for one-call bootstrap."""
    now = int(time.time())
    session = repository.find_refresh_session(db, hash_refresh_token(raw_token))
    if not session or session.revoked_at is not None or session.expires_at <= now:
        raise _error(
            status.HTTP_401_UNAUTHORIZED,
            "invalid_refresh_token",
            "유효하지 않거나 만료된 Refresh Token입니다.",
        )
    user = repository.find_user_by_id(db, session.user_id)
    if (
        not user
        or not user.is_active
        or user.registration_status != "approved"
    ):
        raise _error(
            status.HTTP_401_UNAUTHORIZED,
            "invalid_session",
            "사용자 세션이 유효하지 않습니다.",
        )
    if not repository.consume_refresh_session(db, session.id, now):
        db.rollback()
        raise _error(
            status.HTTP_401_UNAUTHORIZED,
            "refresh_token_reused",
            "이미 교체된 Refresh Token입니다.",
        )
    lineage = db.get(SessionFamily, session.id)
    tokens = issue_tokens(
        db,
        user,
        request,
        family_id=lineage.family_id if lineage else session.id,
    )
    return tokens, user


def logout(
    db: Session,
    *,
    raw_refresh_token: str,
    all_sessions: bool,
    access_payload: dict[str, object] | None,
    request: Request,
) -> None:
    now = int(time.time())
    session = repository.find_refresh_session(
        db,
        hash_refresh_token(raw_refresh_token),
    )
    user_id = session.user_id if session else None
    if session:
        if all_sessions and session.revoked_at is None and session.expires_at > now:
            repository.revoke_all_sessions(db, session.user_id, now)
        else:
            session.revoked_at = now

    if access_payload:
        jti = access_payload.get("jti")
        subject = access_payload.get("sub")
        expires_at = access_payload.get("exp")
        if isinstance(jti, str) and isinstance(subject, str):
            repository.revoke_access_token(
                db,
                jti=jti,
                user_id=subject,
                expires_at=int(expires_at or now),
            )
            user_id = subject

    audit.add_audit_log(
        db,
        action="auth.logout_all" if all_sessions else "auth.logout",
        actor_user_id=user_id,
        target_user_id=user_id,
        request=request,
    )
    db.commit()


def issue_one_time_token(
    db: Session,
    *,
    user: User,
    purpose: str,
    expire_minutes: int,
    payload: dict[str, object] | None = None,
) -> str:
    now = int(time.time())
    repository.invalidate_one_time_tokens(
        db,
        user_id=user.id,
        purpose=purpose,
        consumed_at=now,
    )
    raw_token = create_one_time_token()
    db.add(
        OneTimeToken(
            user_id=user.id,
            purpose=purpose,
            token_hash=hash_secret(raw_token),
            payload_json=(
                json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
                if payload is not None
                else None
            ),
            expires_at=now + expire_minutes * 60,
        )
    )
    db.commit()
    return raw_token


def _response_token(raw_token: str | None) -> str | None:
    if raw_token and get_settings().expose_auth_tokens_in_response:
        return raw_token
    return None


def _consume_one_time_token(
    db: Session,
    *,
    raw_token: str,
    purpose: str,
) -> tuple[OneTimeToken, User]:
    now = int(time.time())
    token = repository.find_one_time_token(
        db,
        token_hash=hash_secret(raw_token),
        purpose=purpose,
    )
    if (
        not token
        or token.consumed_at is not None
        or token.expires_at <= now
    ):
        raise _error(
            status.HTTP_400_BAD_REQUEST,
            "invalid_or_expired_token",
            "유효하지 않거나 만료된 인증 링크입니다.",
        )
    user = repository.find_user_by_id(db, token.user_id)
    if not user:
        raise _error(
            status.HTTP_400_BAD_REQUEST,
            "invalid_or_expired_token",
            "유효하지 않거나 만료된 인증 링크입니다.",
        )
    consumed = db.execute(update(OneTimeToken).where(
        OneTimeToken.id == token.id,
        OneTimeToken.consumed_at.is_(None),
        OneTimeToken.expires_at > now,
    ).values(consumed_at=now))
    if consumed.rowcount != 1:
        db.rollback()
        raise _error(400, "invalid_or_expired_token", "이미 처리되었거나 만료된 인증 링크입니다.")
    return token, user


def request_email_verification(
    db: Session,
    *,
    user: User,
    request: Request,
) -> MessageResponse:
    settings = get_settings()
    if user.email_verified_at is not None:
        return MessageResponse(message="이미 인증된 이메일입니다.")
    rate_key = _request_key(request, user.email)
    rate_limit.check_rate_limit(
        db,
        action="email_verification",
        key=rate_key,
        attempts=settings.account_token_request_limit_attempts,
        window_seconds=settings.account_token_request_limit_window_seconds,
    )
    rate_limit.record_rate_limit_event(
        db,
        action="email_verification",
        key=rate_key,
        succeeded=True,
    )
    token = issue_one_time_token(
        db,
        user=user,
        purpose=EMAIL_VERIFICATION,
        expire_minutes=settings.email_verification_token_expire_minutes,
    )
    delivered = email_delivery.send_verification_email(user.email, token)
    audit.add_audit_log(
        db,
        action="auth.email_verification.request",
        outcome="success" if delivered else "failure",
        actor_user_id=user.id,
        target_user_id=user.id,
        request=request,
    )
    db.commit()
    if not delivered:
        raise _error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "email_delivery_failed",
            "인증 메일을 보내지 못했습니다.",
        )
    return MessageResponse(
        message="인증 안내를 전송했습니다.",
        token=_response_token(token),
    )


def resend_email_verification(
    db: Session,
    *,
    email: str,
    request: Request,
) -> MessageResponse:
    settings = get_settings()
    rate_key = _request_key(request, email)
    rate_limit.check_rate_limit(
        db,
        action="email_verification",
        key=rate_key,
        attempts=settings.account_token_request_limit_attempts,
        window_seconds=settings.account_token_request_limit_window_seconds,
    )
    rate_limit.record_rate_limit_event(
        db,
        action="email_verification",
        key=rate_key,
        succeeded=True,
    )
    user = repository.find_user_by_email(db, email)
    raw_token = None
    if (
        user
        and user.registration_status != "rejected"
        and user.email_verified_at is None
    ):
        raw_token = issue_one_time_token(
            db,
            user=user,
            purpose=EMAIL_VERIFICATION,
            expire_minutes=settings.email_verification_token_expire_minutes,
        )
        email_delivery.send_verification_email(user.email, raw_token)
    db.commit()
    return MessageResponse(
        message="인증이 필요한 계정이면 안내를 전송했습니다.",
        token=_response_token(raw_token),
    )


def confirm_email_verification(
    db: Session,
    *,
    raw_token: str,
    request: Request,
) -> User:
    _, user = _consume_one_time_token(
        db,
        raw_token=raw_token,
        purpose=EMAIL_VERIFICATION,
    )
    now = int(time.time())
    user.email_verified_at = now
    user.updated_at = now
    audit.add_audit_log(
        db,
        action="auth.email_verification.confirm",
        actor_user_id=user.id,
        target_user_id=user.id,
        request=request,
    )
    db.commit()
    db.refresh(user)
    return user


def request_password_reset(
    db: Session,
    *,
    email: str,
    request: Request,
) -> MessageResponse:
    settings = get_settings()
    rate_key = _request_key(request, email)
    rate_limit.check_rate_limit(
        db,
        action="password_reset",
        key=rate_key,
        attempts=settings.account_token_request_limit_attempts,
        window_seconds=settings.account_token_request_limit_window_seconds,
    )
    rate_limit.record_rate_limit_event(
        db,
        action="password_reset",
        key=rate_key,
        succeeded=True,
    )
    user = repository.find_user_by_email(db, email)
    raw_token = None
    if user and user.is_active:
        raw_token = issue_one_time_token(
            db,
            user=user,
            purpose=PASSWORD_RESET,
            expire_minutes=settings.password_reset_token_expire_minutes,
        )
        email_delivery.send_password_reset_email(user.email, raw_token)
        audit.add_audit_log(
            db,
            action="auth.password_reset.request",
            target_user_id=user.id,
            request=request,
        )
    db.commit()
    return MessageResponse(
        message="해당 계정이 존재하면 비밀번호 재설정 안내를 전송했습니다.",
        token=_response_token(raw_token),
    )


def confirm_password_reset(
    db: Session,
    *,
    data: PasswordResetConfirmRequest,
    request: Request,
) -> None:
    _validate_new_password(data.new_password)
    _, user = _consume_one_time_token(
        db,
        raw_token=data.token,
        purpose=PASSWORD_RESET,
    )
    registration_error = registration_access_error(user)
    if registration_error:
        code, message = registration_error
        raise _error(status.HTTP_403_FORBIDDEN, code, message)
    if not user.is_active:
        raise _error(
            status.HTTP_403_FORBIDDEN,
            "account_inactive",
            "비활성 계정은 계정 복구를 이용해주세요.",
        )
    now = int(time.time())
    user.password_hash = hash_password(data.new_password)
    user.auth_version += 1
    user.updated_at = now
    repository.revoke_all_sessions(db, user.id, now)
    audit.add_audit_log(
        db,
        action="auth.password_reset.confirm",
        actor_user_id=user.id,
        target_user_id=user.id,
        request=request,
    )
    db.commit()


def request_account_recovery(
    db: Session,
    *,
    email: str,
    request: Request,
) -> MessageResponse:
    settings = get_settings()
    rate_key = _request_key(request, email)
    rate_limit.check_rate_limit(
        db,
        action="account_recovery",
        key=rate_key,
        attempts=settings.account_token_request_limit_attempts,
        window_seconds=settings.account_token_request_limit_window_seconds,
    )
    rate_limit.record_rate_limit_event(
        db,
        action="account_recovery",
        key=rate_key,
        succeeded=True,
    )
    user = repository.find_user_by_email(db, email)
    raw_token = None
    if (
        user
        and not user.is_active
        and user.registration_status == "approved"
    ):
        raw_token = issue_one_time_token(
            db,
            user=user,
            purpose=ACCOUNT_RECOVERY,
            expire_minutes=settings.account_recovery_token_expire_minutes,
        )
        email_delivery.send_account_recovery_email(user.email, raw_token)
        audit.add_audit_log(
            db,
            action="auth.account_recovery.request",
            target_user_id=user.id,
            request=request,
        )
    db.commit()
    return MessageResponse(
        message="복구 가능한 계정이면 안내를 전송했습니다.",
        token=_response_token(raw_token),
    )


def confirm_account_recovery(
    db: Session,
    *,
    data: AccountRecoveryConfirmRequest,
    request: Request,
) -> None:
    _validate_new_password(data.new_password)
    _, user = _consume_one_time_token(
        db,
        raw_token=data.token,
        purpose=ACCOUNT_RECOVERY,
    )
    if user.registration_status != "approved":
        registration_error = registration_access_error(user)
        code, message = registration_error or (
            "registration_review_required",
            "관리자 승인 후 계정을 복구할 수 있습니다.",
        )
        raise _error(status.HTTP_403_FORBIDDEN, code, message)
    now = int(time.time())
    user.password_hash = hash_password(data.new_password)
    user.is_active = True
    user.deactivated_at = None
    user.email_verified_at = user.email_verified_at or now
    user.auth_version += 1
    user.updated_at = now
    repository.revoke_all_sessions(db, user.id, now)
    audit.add_audit_log(
        db,
        action="auth.account_recovery.confirm",
        actor_user_id=user.id,
        target_user_id=user.id,
        request=request,
    )
    db.commit()


def update_profile(
    db: Session,
    *,
    user: User,
    data: UserUpdateRequest,
    request: Request,
) -> tuple[User, str | None]:
    values = data.model_dump(exclude_unset=True)
    if not values:
        return user, None

    if "email" in values:
        email = (
            str(values["email"]).strip().lower()
            if values["email"] is not None
            else user.email
        )
        if email != user.email:
            raise _error(
                status.HTTP_409_CONFLICT,
                "email_immutable",
                "로그인 이메일은 가입 후 변경할 수 없습니다.",
            )
        values.pop("email")

    if "username" in values:
        username = (
            str(values["username"]).strip().lower()
            if values["username"] is not None
            else user.username
        )
        if username != user.username:
            raise _error(
                status.HTTP_409_CONFLICT,
                "username_immutable",
                "사용자명은 가입 후 변경할 수 없습니다.",
            )
        values.pop("username")

    if not values:
        return user, None

    if "display_name" in values:
        user.display_name = values["display_name"]

    if "avatar_url" in values:
        user.avatar_url = values["avatar_url"]

    user.updated_at = int(time.time())
    audit.add_audit_log(
        db,
        action="user.profile.update",
        actor_user_id=user.id,
        target_user_id=user.id,
        request=request,
        details={"fields": sorted(values)},
    )
    db.commit()
    db.refresh(user)
    return user, None


def change_password(
    db: Session,
    *,
    user: User,
    data: PasswordChangeRequest,
    request: Request,
) -> None:
    if (
        not has_usable_password(user)
        or not verify_password(data.current_password, user.password_hash)
    ):
        raise _error(
            status.HTTP_400_BAD_REQUEST,
            "invalid_current_password",
            "현재 비밀번호가 올바르지 않습니다.",
        )
    _validate_new_password(data.new_password)
    if verify_password(data.new_password, user.password_hash):
        raise _error(
            status.HTTP_400_BAD_REQUEST,
            "password_reused",
            "현재 비밀번호와 다른 비밀번호를 사용해주세요.",
        )
    now = int(time.time())
    user.password_hash = hash_password(data.new_password)
    user.auth_version += 1
    user.updated_at = now
    repository.revoke_all_sessions(db, user.id, now)
    audit.add_audit_log(
        db,
        action="user.password.change",
        actor_user_id=user.id,
        target_user_id=user.id,
        request=request,
    )
    db.commit()


def deactivate_account(
    db: Session,
    *,
    user: User,
    current_password: str,
    request: Request,
) -> None:
    if not verify_password(current_password, user.password_hash):
        raise _error(
            status.HTTP_400_BAD_REQUEST,
            "invalid_current_password",
            "현재 비밀번호가 올바르지 않습니다.",
        )
    now = int(time.time())
    user.is_active = False
    user.deactivated_at = now
    user.auth_version += 1
    user.updated_at = now
    repository.revoke_all_sessions(db, user.id, now)
    audit.add_audit_log(
        db,
        action="user.account.deactivate",
        actor_user_id=user.id,
        target_user_id=user.id,
        request=request,
    )
    db.commit()


def list_sessions(
    db: Session,
    *,
    user: User,
    current_session_id: str | None,
) -> list[SessionResponse]:
    return [
        SessionResponse(
            id=session.id,
            created_at=session.created_at,
            last_used_at=session.last_used_at,
            expires_at=session.expires_at,
            ip_address=session.ip_address,
            user_agent=session.user_agent,
            is_current=session.id == current_session_id,
        )
        for session in repository.list_active_sessions(db, user.id)
    ]


def revoke_user_session(
    db: Session,
    *,
    user: User,
    session_id: str,
    request: Request,
) -> None:
    if not repository.revoke_session(
        db,
        session_id=session_id,
        user_id=user.id,
        revoked_at=int(time.time()),
    ):
        raise _error(
            status.HTTP_404_NOT_FOUND,
            "session_not_found",
            "세션을 찾을 수 없습니다.",
        )
    audit.add_audit_log(
        db,
        action="user.session.revoke",
        actor_user_id=user.id,
        target_user_id=user.id,
        request=request,
        details={"session_id": session_id},
    )
    db.commit()
