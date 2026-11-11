import smtplib
from email.message import EmailMessage

from app.core.config import get_settings


def _build_url(path: str, token: str) -> str:
    settings = get_settings()
    return f"{settings.frontend_url.rstrip('/')}{path}?token={token}"


def _send_email(*, recipient: str, subject: str, body: str) -> bool:
    settings = get_settings()
    if settings.email_provider == "disabled":
        return False
    if settings.email_provider == "console":
        print(
            "\n[LAYER_AUTH_EMAIL]\n"
            f"To: {recipient}\n"
            f"Subject: {subject}\n\n"
            f"{body}\n"
            "[/LAYER_AUTH_EMAIL]\n"
        )
        return True

    message = EmailMessage()
    message["From"] = settings.email_from
    message["To"] = recipient
    message["Subject"] = subject
    message.set_content(body)

    try:
        with smtplib.SMTP(
            settings.smtp_host,
            settings.smtp_port,
            timeout=settings.smtp_timeout_seconds,
        ) as smtp:
            if settings.smtp_use_tls:
                smtp.starttls()
            if settings.smtp_username:
                smtp.login(settings.smtp_username, settings.smtp_password or "")
            smtp.send_message(message)
        return True
    except (OSError, smtplib.SMTPException) as error:
        print(f"[LAYER_AUTH_EMAIL_ERROR] {type(error).__name__}: {error}")
        return False


def send_verification_email(email: str, token: str) -> bool:
    url = _build_url("/user/verify-email", token)
    return _send_email(
        recipient=email,
        subject="[LLM] 이메일 주소를 인증해주세요",
        body=(
            "LLM 이메일 인증 요청입니다.\n\n"
            f"아래 링크를 열어 인증을 완료하세요.\n{url}\n\n"
            "본인이 요청하지 않았다면 이 메일을 무시하세요."
        ),
    )


def send_password_reset_email(email: str, token: str) -> bool:
    url = _build_url("/user/reset-password", token)
    return _send_email(
        recipient=email,
        subject="[LLM] 비밀번호 재설정",
        body=(
            "LLM 비밀번호 재설정 요청입니다.\n\n"
            f"아래 링크를 열어 새 비밀번호를 설정하세요.\n{url}\n\n"
            "본인이 요청하지 않았다면 비밀번호는 변경되지 않습니다."
        ),
    )


def send_account_recovery_email(email: str, token: str) -> bool:
    url = _build_url("/user/recover", token)
    return _send_email(
        recipient=email,
        subject="[LLM] 계정 복구",
        body=(
            "LLM 비활성 계정 복구 요청입니다.\n\n"
            f"아래 링크를 열어 계정을 복구하세요.\n{url}\n\n"
            "본인이 요청하지 않았다면 이 메일을 무시하세요."
        ),
    )
