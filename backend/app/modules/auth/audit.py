import hmac
import ipaddress
import json

from fastapi import Request
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.modules.auth.models import AuditLog


def _trusted_proxy_ip(request: Request) -> str | None:
    expected_secret = get_settings().auth_proxy_secret
    provided_secret = request.headers.get("x-gyeop-proxy-secret")
    if (
        not expected_secret
        or not provided_secret
        or not hmac.compare_digest(provided_secret, expected_secret)
    ):
        return None

    candidate = request.headers.get("x-gyeop-client-ip", "").strip()
    if not candidate or len(candidate) > 64:
        return None
    try:
        return str(ipaddress.ip_address(candidate))
    except ValueError:
        return None


def request_metadata(request: Request | None) -> tuple[str | None, str | None]:
    if request is None:
        return None, None
    ip_address = _trusted_proxy_ip(request)
    if ip_address is None:
        ip_address = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")
    return ip_address, user_agent[:512] if user_agent else None


def add_audit_log(
    db: Session,
    *,
    action: str,
    outcome: str = "success",
    actor_user_id: str | None = None,
    target_user_id: str | None = None,
    request: Request | None = None,
    details: dict[str, object] | None = None,
) -> None:
    ip_address, user_agent = request_metadata(request)
    db.add(
        AuditLog(
            action=action,
            outcome=outcome,
            actor_user_id=actor_user_id,
            target_user_id=target_user_id,
            ip_address=ip_address,
            user_agent=user_agent,
            details_json=(
                json.dumps(details, ensure_ascii=False, separators=(",", ":"))
                if details
                else None
            ),
        )
    )
