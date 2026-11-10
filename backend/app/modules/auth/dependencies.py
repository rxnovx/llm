import time
from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import Depends, HTTPException
from fastapi.security import OAuth2PasswordBearer
from jwt import InvalidTokenError
from sqlalchemy.orm import Session

from app.core.security import decode_access_token
from app.db.session import get_db
from app.modules.auth import repository
from app.modules.auth.models import User

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login", auto_error=False)


@dataclass
class AuthContext:
    user: User
    payload: dict[str, Any]


def authenticate_token(token: str | None, db: Session) -> AuthContext | None:
    if token is None:
        return None
    unauthorized = HTTPException(401, detail="로그인 세션이 유효하지 않습니다.",
                                 headers={"WWW-Authenticate": "Bearer"})
    try:
        payload = decode_access_token(token)
        subject, session_id, jti = (payload.get(key) for key in ("sub", "sid", "jti"))
        if not all(isinstance(value, str) for value in (subject, session_id, jti)):
            raise unauthorized
        now = int(time.time())
        state = repository.find_jwt_auth_state(db, user_id=subject,
            session_id=session_id, access_token_jti=jti, now=now)
        if not state:
            raise unauthorized
        user, session, revoked = state
        if (revoked or not user.is_active or user.registration_status != "approved"
            or user.auth_version != payload.get("ver") or session.user_id != user.id
            or session.revoked_at is not None or session.expires_at <= now):
            raise unauthorized
        return AuthContext(user, payload)
    except (InvalidTokenError, ValueError, TypeError) as error:
        raise unauthorized from error


def optional_context(token: Annotated[str | None, Depends(oauth2_scheme)],
                     db: Annotated[Session, Depends(get_db)]) -> AuthContext | None:
    return authenticate_token(token, db)


def require_context(context: Annotated[AuthContext | None, Depends(optional_context)]) -> AuthContext:
    if context is None:
        raise HTTPException(401, detail="로그인이 필요합니다.", headers={"WWW-Authenticate": "Bearer"})
    return context


Authenticated = Annotated[AuthContext, Depends(require_context)]
OptionalAuthenticated = Annotated[AuthContext | None, Depends(optional_context)]
