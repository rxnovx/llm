import base64
import hashlib
import hmac
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from jwt import InvalidTokenError

from app.core.config import get_settings

SCRYPT_DKLEN = 32
PASSWORD_CHAR_GROUPS = (
    str.islower,
    str.isupper,
    str.isdigit,
    lambda value: not value.isalnum(),
)


def validate_password_strength(password: str) -> None:
    settings = get_settings()
    if len(password) < settings.password_min_length:
        raise ValueError(
            f"비밀번호는 {settings.password_min_length}자 이상이어야 합니다."
        )
    if len(password) > 128:
        raise ValueError("비밀번호는 128자를 초과할 수 없습니다.")
    matched_groups = sum(
        any(check(character) for character in password)
        for check in PASSWORD_CHAR_GROUPS
    )
    if matched_groups < 3:
        raise ValueError(
            "비밀번호에는 영문 대문자·소문자·숫자·특수문자 중 "
            "3종류 이상을 사용해주세요."
        )


def _derive_scrypt(
    password: str,
    salt: bytes,
    *,
    n: int,
    r: int,
    p: int,
    length: int = SCRYPT_DKLEN,
) -> bytes:
    return hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=n,
        r=r,
        p=p,
        dklen=length,
        maxmem=max(64 * 1024 * 1024, 128 * n * r * 2),
    )


def hash_password(password: str) -> str:
    settings = get_settings()
    salt = secrets.token_bytes(16)
    derived = _derive_scrypt(
        password,
        salt,
        n=settings.scrypt_n,
        r=settings.scrypt_r,
        p=settings.scrypt_p,
    )
    return "scrypt$v2${}${}${}${}${}".format(
        settings.scrypt_n,
        settings.scrypt_r,
        settings.scrypt_p,
        base64.urlsafe_b64encode(salt).decode("ascii"),
        base64.urlsafe_b64encode(derived).decode("ascii"),
    )


def verify_password(password: str, encoded: str) -> bool:
    try:
        parts = encoded.split("$")
        if len(parts) == 3 and parts[0] == "scrypt":
            # 기존 사용자 해시 형식: scrypt$<salt>$<digest>
            settings = get_settings()
            _, salt_text, digest_text = parts
            n, r, p = 2**14, 8, 1
        elif len(parts) == 7 and parts[:2] == ["scrypt", "v2"]:
            _, _, n_text, r_text, p_text, salt_text, digest_text = parts
            n, r, p = int(n_text), int(r_text), int(p_text)
        else:
            return False
        salt = base64.urlsafe_b64decode(salt_text.encode("ascii"))
        expected = base64.urlsafe_b64decode(digest_text.encode("ascii"))
        actual = _derive_scrypt(
            password,
            salt,
            n=n,
            r=r,
            p=p,
            length=len(expected),
        )
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def password_hash_needs_upgrade(encoded: str) -> bool:
    settings = get_settings()
    try:
        parts = encoded.split("$")
        if len(parts) != 7 or parts[:2] != ["scrypt", "v2"]:
            return True
        return (
            int(parts[2]) != settings.scrypt_n
            or int(parts[3]) != settings.scrypt_r
            or int(parts[4]) != settings.scrypt_p
        )
    except (ValueError, TypeError):
        return True


def create_access_token(
    *,
    subject: str,
    claims: dict[str, Any],
    expires_in_seconds: int | None = None,
) -> tuple[str, int]:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    expires_at = (
        now + timedelta(seconds=expires_in_seconds)
        if expires_in_seconds is not None
        else now + timedelta(minutes=settings.access_token_minutes)
    )
    payload = {
        **claims,
        "sub": subject,
        "type": "access",
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "iat": now,
        "exp": expires_at,
        "jti": str(uuid.uuid4()),
    }
    token = jwt.encode(
        payload,
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )
    return token, int((expires_at - now).total_seconds())


def decode_access_token(token: str) -> dict[str, Any]:
    settings = get_settings()
    payload = jwt.decode(
        token,
        settings.jwt_secret_key,
        algorithms=[settings.jwt_algorithm],
        issuer=settings.jwt_issuer,
        audience=settings.jwt_audience,
        options={"require": ["sub", "iss", "aud", "iat", "exp", "jti"]},
    )
    if payload.get("type") != "access":
        raise InvalidTokenError("Access token required")
    return payload


def create_refresh_token() -> str:
    return secrets.token_urlsafe(48)


def create_one_time_token() -> str:
    return secrets.token_urlsafe(32)



def hash_secret(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def hash_refresh_token(token: str) -> str:
    return hash_secret(token)
