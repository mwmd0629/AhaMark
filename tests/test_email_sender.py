import smtplib
from unittest.mock import Mock, patch

import pytest
from app.core.config import Settings
from app.integrations.email_sender import EmailDeliveryError, send_auth_code


def test_development_without_smtp_returns_preview_without_network() -> None:
    settings = Settings(app_env="development", smtp_host=None)
    with (
        patch("app.integrations.email_sender.get_settings", return_value=settings),
        patch("app.integrations.email_sender.smtplib.SMTP") as smtp,
    ):
        assert send_auth_code("student@example.com", "123456", "verify_email", 600) is True
    smtp.assert_not_called()


def test_production_never_uses_development_preview() -> None:
    settings = Settings(app_env="development", smtp_host=None)
    settings.app_env = "production"
    with (
        patch("app.integrations.email_sender.get_settings", return_value=settings),
        pytest.raises(EmailDeliveryError, match="not configured"),
    ):
        send_auth_code("student@example.com", "123456", "reset_password", 600)


@pytest.mark.parametrize(
    ("purpose", "subject_text", "body_text"),
    [
        ("verify_email", "邮箱验证码", "验证恢复邮箱"),
        ("reset_password", "密码重置验证码", "重置密码"),
    ],
)
def test_smtp_sends_chinese_auth_message(
    purpose: str,
    subject_text: str,
    body_text: str,
) -> None:
    settings = Settings(
        smtp_host="smtp.example.com",
        smtp_port=587,
        smtp_username="service-account",
        smtp_password="smtp-password",
        smtp_from_email="no-reply@example.com",
        smtp_starttls=True,
        smtp_ssl=False,
    )
    connection, server = Mock(), Mock()
    connection.__enter__ = Mock(return_value=server)
    connection.__exit__ = Mock(return_value=False)
    context = Mock()
    with (
        patch("app.integrations.email_sender.get_settings", return_value=settings),
        patch("app.integrations.email_sender.ssl.create_default_context", return_value=context),
        patch("app.integrations.email_sender.smtplib.SMTP", return_value=connection) as smtp,
    ):
        preview = send_auth_code("student@example.com", "123456", purpose, 601)

    assert preview is False
    smtp.assert_called_once_with("smtp.example.com", 587, timeout=10.0)
    server.starttls.assert_called_once_with(context=context)
    server.login.assert_called_once_with("service-account", "smtp-password")
    sent = server.send_message.call_args.args[0]
    assert subject_text in str(sent["Subject"])
    assert body_text in sent.get_content()
    assert "123456" in sent.get_content()
    assert "11 分钟" in sent.get_content()
    assert server.send_message.call_args.kwargs == {
        "from_addr": "no-reply@example.com",
        "to_addrs": ["student@example.com"],
    }


def test_smtp_failure_raises_sanitized_delivery_error() -> None:
    settings = Settings(
        smtp_host="smtp.example.com",
        smtp_from_email="no-reply@example.com",
    )
    connection, server = Mock(), Mock()
    connection.__enter__ = Mock(return_value=server)
    connection.__exit__ = Mock(return_value=False)
    server.send_message.side_effect = smtplib.SMTPException("provider rejected message")
    with (
        patch("app.integrations.email_sender.get_settings", return_value=settings),
        patch("app.integrations.email_sender.smtplib.SMTP", return_value=connection),
        pytest.raises(EmailDeliveryError) as rejected,
    ):
        send_auth_code("student@example.com", "secret-code", "verify_email", 600)
    assert str(rejected.value) == "Authentication email delivery failed"
    assert "secret-code" not in str(rejected.value)


def test_smtp_ssl_uses_implicit_tls_without_starttls() -> None:
    settings = Settings(
        smtp_host="smtp.example.com",
        smtp_port=465,
        smtp_from_email="no-reply@example.com",
        smtp_starttls=False,
        smtp_ssl=True,
    )
    connection, server = Mock(), Mock()
    connection.__enter__ = Mock(return_value=server)
    connection.__exit__ = Mock(return_value=False)
    context = Mock()
    with (
        patch("app.integrations.email_sender.get_settings", return_value=settings),
        patch("app.integrations.email_sender.ssl.create_default_context", return_value=context),
        patch(
            "app.integrations.email_sender.smtplib.SMTP_SSL", return_value=connection
        ) as smtp_ssl,
    ):
        assert send_auth_code("student@example.com", "123456", "verify_email", 600) is False
    smtp_ssl.assert_called_once_with(
        "smtp.example.com", 465, timeout=10.0, context=context
    )
    server.starttls.assert_not_called()


@pytest.mark.parametrize("purpose", ["", "login", "password"])
def test_unknown_email_purpose_is_rejected_in_preview(purpose: str) -> None:
    settings = Settings(app_env="development", smtp_host=None)
    with (
        patch("app.integrations.email_sender.get_settings", return_value=settings),
        pytest.raises(EmailDeliveryError, match="Unsupported"),
    ):
        send_auth_code("student@example.com", "123456", purpose, 600)
