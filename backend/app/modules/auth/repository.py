import time
from sqlalchemy import delete, func, or_, select, true, update
from sqlalchemy.orm import Session
from app.modules.auth.models import (AuthRateLimitEvent, OAuthIdentity, OneTimeToken,
    RefreshSession, RevokedAccessToken, User)

def normalize_email(value: str) -> str:
    return value.strip().lower()


def find_user_by_login(db: Session, login_id: str) -> User | None:
    normalized = login_id.strip().lower()
    return db.scalar(
        select(User).where(
            or_(User.email == normalized, User.username == normalized)
        )
    )


def find_user_by_email(db: Session, email: str) -> User | None:
    return db.scalar(select(User).where(User.email == normalize_email(email)))


def find_user_by_id(db: Session, user_id: str) -> User | None:
    return db.get(User, user_id)


def find_oauth_identity(
    db: Session,
    *,
    provider: str,
    provider_subject: str,
) -> OAuthIdentity | None:
    return db.scalar(
        select(OAuthIdentity).where(
            OAuthIdentity.provider == provider,
            OAuthIdentity.provider_subject == provider_subject,
        )
    )


def find_user_oauth_identity(
    db: Session,
    *,
    user_id: str,
    provider: str,
) -> OAuthIdentity | None:
    return db.scalar(
        select(OAuthIdentity).where(
            OAuthIdentity.user_id == user_id,
            OAuthIdentity.provider == provider,
        )
    )


def find_refresh_session(db: Session, token_hash: str) -> RefreshSession | None:
    return db.scalar(
        select(RefreshSession).where(RefreshSession.token_hash == token_hash)
    )


def find_refresh_session_by_id(
    db: Session,
    session_id: str,
) -> RefreshSession | None:
    return db.get(RefreshSession, session_id)


def find_jwt_auth_state(
    db: Session,
    *,
    user_id: str,
    session_id: str,
    access_token_jti: str,
    now: int,
) -> tuple[User, RefreshSession, bool] | None:
    """Load the normal JWT security state in one database round trip.

    Session ownership is checked by the authentication dependency.
    """

    active_revocation = (
        select(RevokedAccessToken.jti)
        .where(
            RevokedAccessToken.jti == access_token_jti,
            RevokedAccessToken.expires_at > now,
        )
        .exists()
    )
    row = db.execute(
        select(
            User,
            RefreshSession,
            active_revocation.label("access_token_revoked"),
        )
        .select_from(User)
        .join(RefreshSession, true())
        .where(
            User.id == user_id,
            RefreshSession.id == session_id,
        )
    ).one_or_none()
    if row is None:
        return None
    return row[0], row[1], bool(row[2])


def list_active_sessions(db: Session, user_id: str) -> list[RefreshSession]:
    now = int(time.time())
    return list(
        db.scalars(
            select(RefreshSession)
            .where(
                RefreshSession.user_id == user_id,
                RefreshSession.revoked_at.is_(None),
                RefreshSession.expires_at > now,
            )
            .order_by(RefreshSession.created_at.desc())
        )
    )


def revoke_session(
    db: Session,
    *,
    session_id: str,
    user_id: str,
    revoked_at: int,
) -> bool:
    result = db.execute(
        update(RefreshSession)
        .where(
            RefreshSession.id == session_id,
            RefreshSession.user_id == user_id,
            RefreshSession.revoked_at.is_(None),
        )
        .values(revoked_at=revoked_at)
    )
    return bool(result.rowcount)


def revoke_all_sessions(db: Session, user_id: str, revoked_at: int) -> None:
    db.execute(
        update(RefreshSession)
        .where(
            RefreshSession.user_id == user_id,
            RefreshSession.revoked_at.is_(None),
        )
        .values(revoked_at=revoked_at)
    )


def consume_refresh_session(
    db: Session,
    session_id: str,
    revoked_at: int,
) -> bool:
    result = db.execute(
        update(RefreshSession)
        .where(
            RefreshSession.id == session_id,
            RefreshSession.revoked_at.is_(None),
            RefreshSession.expires_at > revoked_at,
        )
        .values(revoked_at=revoked_at, last_used_at=revoked_at)
    )
    return bool(result.rowcount)


def revoke_access_token(
    db: Session,
    *,
    jti: str,
    user_id: str,
    expires_at: int,
) -> None:
    if db.get(RevokedAccessToken, jti):
        return
    db.add(
        RevokedAccessToken(
            jti=jti,
            user_id=user_id,
            expires_at=expires_at,
        )
    )


def is_access_token_revoked(db: Session, jti: str) -> bool:
    revoked = db.get(RevokedAccessToken, jti)
    return bool(revoked and revoked.expires_at > int(time.time()))


def find_one_time_token(
    db: Session,
    *,
    token_hash: str,
    purpose: str,
) -> OneTimeToken | None:
    return db.scalar(
        select(OneTimeToken).where(
            OneTimeToken.token_hash == token_hash,
            OneTimeToken.purpose == purpose,
        )
    )


def invalidate_one_time_tokens(
    db: Session,
    *,
    user_id: str,
    purpose: str,
    consumed_at: int,
) -> None:
    db.execute(
        update(OneTimeToken)
        .where(
            OneTimeToken.user_id == user_id,
            OneTimeToken.purpose == purpose,
            OneTimeToken.consumed_at.is_(None),
        )
        .values(consumed_at=consumed_at)
    )


def count_rate_limit_events(
    db: Session,
    *,
    action: str,
    key_hash: str,
    since: int,
    failures_only: bool,
) -> int:
    query = select(func.count(AuthRateLimitEvent.id)).where(
        AuthRateLimitEvent.action == action,
        AuthRateLimitEvent.key_hash == key_hash,
        AuthRateLimitEvent.created_at >= since,
    )
    if failures_only:
        query = query.where(AuthRateLimitEvent.succeeded.is_(False))
    return int(db.scalar(query) or 0)


def prune_rate_limit_events(db: Session, before: int) -> None:
    db.execute(
        delete(AuthRateLimitEvent).where(
            AuthRateLimitEvent.created_at < before
        )
    )
