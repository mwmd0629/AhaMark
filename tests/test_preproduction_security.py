from unittest.mock import Mock, patch

import pytest
from app.api.auth import check_rate_limit, rate_limit_key
from app.core.config import Settings
from fastapi import HTTPException
from pydantic import ValidationError


def production_settings(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "app_env": "production",
        "database_url": "postgresql+psycopg://app:strong-db-value@postgres:5432/app",
        "redis_url": "redis://redis:6379/0",
        "cors_origins": ["https://localhost:9443"],
        "trusted_hosts": ["localhost"],
        "csrf_trusted_origins": ["https://localhost:9443"],
        "minio_access_key": "preproduction-access",
        "minio_secret_key": "strong-storage-secret-value",
        "session_hmac_secret": "a" * 48,
        "demo_actor_enabled": False,
        "auth_cookie_secure": True,
        "recognition_provider": "unavailable",
        "grading_provider": "unavailable",
    }
    values.update(overrides)
    return values


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("demo_actor_enabled", True),
        ("recognition_provider", "fake"),
        ("formula_recognition_provider", "fake"),
        ("answer_recognition_provider", "fake"),
        ("grading_provider", "fake"),
        ("assignment_generation_provider", "fake"),
        ("assignment_generation_suggestion_only", False),
        ("session_hmac_secret", "change-me"),
        ("database_url", "sqlite:///production.db"),
        ("minio_secret_key", "change-me-in-production"),
        ("csrf_trusted_origins", []),
        ("cors_origins", ["*"]),
        ("trusted_hosts", ["*"]),
        ("debug", True),
        ("auth_cookie_secure", False),
    ],
)
def test_production_configuration_rejects_unsafe_values(field: str, value: object) -> None:
    with pytest.raises(ValidationError) as rejected:
        Settings(**production_settings(**{field: value}))
    assert "production configuration rejected" in str(rejected.value)
    assert "strong-storage-secret-value" not in str(rejected.value)


def test_production_configuration_accepts_explicit_safe_values() -> None:
    assert Settings(**production_settings()).app_env == "production"


def test_production_formula_http_provider_requires_strong_token_and_host_allowlist() -> None:
    with pytest.raises(ValidationError, match="FORMULA_RECOGNITION_API_KEY"):
        Settings(
            **production_settings(
                formula_recognition_provider="http",
                formula_recognition_base_url="https://formula.internal",
                formula_recognition_api_key="weak",
                formula_recognition_allowed_hosts=["formula.internal"],
            )
        )
    configured = Settings(
        **production_settings(
            formula_recognition_provider="http",
            formula_recognition_base_url="https://formula.internal",
            formula_recognition_api_key="f" * 48,
            formula_recognition_allowed_hosts=["formula.internal"],
        )
    )
    assert configured.formula_recognition_provider == "http"


def test_rate_limit_key_is_namespaced_hmac_without_plaintext() -> None:
    key = rate_limit_key("192.0.2.1:teacher@example.com")
    assert key.startswith("ahamark:auth:login:")
    assert "teacher" not in key and "192.0.2.1" not in key


def test_shared_rate_limit_uses_redis_and_rejects_across_calls() -> None:
    settings = Settings(**production_settings(auth_login_max_attempts=2))
    client = Mock()
    client.incr.side_effect = [1, 2, 3]
    with (
        patch("app.api.auth.get_settings", return_value=settings),
        patch("app.api.auth.redis.Redis.from_url", return_value=client),
    ):
        expected_key = rate_limit_key("shared")
        check_rate_limit("shared")
        check_rate_limit("shared")
        with pytest.raises(HTTPException) as rejected:
            check_rate_limit("shared")
    assert rejected.value.status_code == 429
    client.expire.assert_called_once_with(expected_key, settings.auth_login_window_seconds)


def test_production_rate_limit_fails_closed_when_redis_is_unavailable() -> None:
    settings = Settings(**production_settings())
    with (
        patch("app.api.auth.get_settings", return_value=settings),
        patch("app.api.auth.redis.Redis.from_url", side_effect=ConnectionError),
        pytest.raises(HTTPException) as rejected,
    ):
        check_rate_limit("shared")
    assert rejected.value.status_code == 503
