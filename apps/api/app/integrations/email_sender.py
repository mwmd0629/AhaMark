import math
import smtplib
import ssl
from email.message import EmailMessage

from pydantic import EmailStr, TypeAdapter, ValidationError

from app.core.config import get_settings

_EMAIL = TypeAdapter(EmailStr)
_PURPOSES = {
    "verify_email": ("AhaMark 邮箱验证码", "验证恢复邮箱"),
    "reset_password": ("AhaMark 密码重置验证码", "重置密码"),
}


class EmailDeliveryError(RuntimeError):
    """An authentication email could not be prepared or delivered."""


def _validate_payload(code: str, purpose: str, ttl_seconds: int) -> None:
    if purpose not in _PURPOSES:
        raise EmailDeliveryError("Unsupported authentication email purpose")
    if not code or len(code) > 64 or "\r" in code or "\n" in code:
        raise EmailDeliveryError("Invalid authentication code")
    if ttl_seconds <= 0:
        raise EmailDeliveryError("Authentication code TTL must be positive")


def _validated_email(value: str, *, field: str) -> str:
    candidate = value.strip()
    if "\r" in candidate or "\n" in candidate:
        raise EmailDeliveryError(f"Invalid {field}")
    try:
        return str(_EMAIL.validate_python(candidate))
    except ValidationError as exc:
        raise EmailDeliveryError(f"Invalid {field}") from exc


def _message(
    recipient: str,
    code: str,
    purpose: str,
    ttl_seconds: int,
    from_email: str,
) -> EmailMessage:
    subject, action = _PURPOSES[purpose]
    minutes = max(1, math.ceil(ttl_seconds / 60))
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = from_email
    message["To"] = recipient
    message["Auto-Submitted"] = "auto-generated"
    message["X-Auto-Response-Suppress"] = "All"
    message.set_content(
        "您好：\n\n"
        f"您正在使用 AhaMark {action}。验证码为：{code}\n\n"
        f"验证码将在 {minutes} 分钟后失效，请勿向任何人泄露。\n"
        "如果这不是您的操作，请忽略本邮件并及时联系老师或系统管理员。\n\n"
        "AhaMark"
    )
    return message


def send_auth_code(recipient: str, code: str, purpose: str, ttl_seconds: int) -> bool:
    """Send an authentication code.

    Returns ``True`` only for the non-production development preview path. SMTP
    delivery returns ``False``. The code is never included in raised errors.
    """

    _validate_payload(code, purpose, ttl_seconds)
    valid_recipient = _validated_email(recipient, field="recipient email")
    settings = get_settings()
    smtp_host = (settings.smtp_host or "").strip()
    if not smtp_host:
        if settings.app_env.lower() == "production":
            raise EmailDeliveryError("Authentication email delivery is not configured")
        return True

    if settings.smtp_from_email is None:
        raise EmailDeliveryError("SMTP sender address is not configured")
    if settings.smtp_starttls and settings.smtp_ssl:
        raise EmailDeliveryError("SMTP STARTTLS and SSL cannot both be enabled")
    if settings.smtp_username and settings.smtp_password is None:
        raise EmailDeliveryError("SMTP password is not configured")
    if settings.smtp_password is not None and not settings.smtp_username:
        raise EmailDeliveryError("SMTP username is not configured")

    valid_from = _validated_email(str(settings.smtp_from_email), field="sender email")
    message = _message(valid_recipient, code, purpose, ttl_seconds, valid_from)
    password = (
        settings.smtp_password.get_secret_value()
        if settings.smtp_password is not None
        else None
    )

    try:
        context = ssl.create_default_context()
        connection: smtplib.SMTP
        if settings.smtp_ssl:
            connection = smtplib.SMTP_SSL(
                smtp_host,
                settings.smtp_port,
                timeout=settings.smtp_timeout_seconds,
                context=context,
            )
        else:
            connection = smtplib.SMTP(
                smtp_host,
                settings.smtp_port,
                timeout=settings.smtp_timeout_seconds,
            )
        with connection as server:
            server.ehlo()
            if settings.smtp_starttls:
                server.starttls(context=context)
                server.ehlo()
            if settings.smtp_username and password is not None:
                server.login(settings.smtp_username, password)
            server.send_message(
                message,
                from_addr=valid_from,
                to_addrs=[valid_recipient],
            )
    except (OSError, smtplib.SMTPException) as exc:
        raise EmailDeliveryError("Authentication email delivery failed") from exc
    return False
