from __future__ import annotations

from typing import Any

from google.auth.exceptions import GoogleAuthError
from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2 import id_token


def verify_google_credential(
    credential: str,
    *,
    client_id: str,
    hosted_domain: str | None = None,
) -> dict[str, Any]:
    """Verify a Google Identity Services ID token against Google's public keys."""

    try:
        claims = id_token.verify_oauth2_token(
            credential,
            GoogleRequest(),
            client_id,
        )
    except (GoogleAuthError, ValueError) as error:
        raise ValueError("Google 인증 정보가 유효하지 않습니다.") from error

    issuer = claims.get("iss")
    if issuer not in {"accounts.google.com", "https://accounts.google.com"}:
        raise ValueError("Google 인증 발급자를 확인할 수 없습니다.")
    if claims.get("aud") != client_id:
        raise ValueError("다른 애플리케이션에 발급된 Google 인증 정보입니다.")
    if claims.get("email_verified") not in {True, "true"}:
        raise ValueError("인증된 Google 이메일이 필요합니다.")
    if not isinstance(claims.get("sub"), str) or not claims["sub"]:
        raise ValueError("Google 계정 식별자가 없습니다.")
    if not isinstance(claims.get("email"), str) or not claims["email"]:
        raise ValueError("Google 이메일을 확인할 수 없습니다.")
    if hosted_domain and claims.get("hd") != hosted_domain:
        raise ValueError("허용된 Google Workspace 계정이 아닙니다.")
    return claims
