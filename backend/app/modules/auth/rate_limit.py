import hashlib
import time

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.modules.auth import repository
from app.modules.auth.models import AuthRateLimitEvent


def _key_hash(value: str) -> str:
    return hashlib.sha256(value.strip().lower().encode("utf-8")).hexdigest()


def check_rate_limit(
    db: Session,
    *,
    action: str,
    key: str,
    attempts: int,
    window_seconds: int,
    failures_only: bool = False,
) -> None:
    now = int(time.time())
    count = repository.count_rate_limit_events(
        db,
        action=action,
        key_hash=_key_hash(key),
        since=now - window_seconds,
        failures_only=failures_only,
    )
    if count >= attempts:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "code": "rate_limit_exceeded",
                "message": "요청이 너무 많습니다. 잠시 후 다시 시도해주세요.",
            },
            headers={"Retry-After": str(window_seconds)},
        )


def record_rate_limit_event(
    db: Session,
    *,
    action: str,
    key: str,
    succeeded: bool,
) -> None:
    repository.prune_rate_limit_events(db, int(time.time()) - 86400)
    db.add(
        AuthRateLimitEvent(
            action=action,
            key_hash=_key_hash(key),
            succeeded=succeeded,
        )
    )
