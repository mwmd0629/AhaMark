"""Exercise production startup refusal without opening a listener or writing business data."""

import os
from collections.abc import Callable

from app.cli.recovery_v7_guard import require_recovery_environment
from app.core.config import Settings
from app.failure_recovery import recovery_fault_checkpoint


def safe_settings() -> dict[str, object]:
    return {
        "_env_file": None,
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


def rejected(overrides: dict[str, object]) -> bool:
    try:
        Settings(**(safe_settings() | overrides))
    except ValueError:
        return True
    return False


def fault_checkpoint_rejected() -> bool:
    previous = {
        name: os.environ.get(name)
        for name in ("APP_ENV", "RECOVERY_V7_ENABLED", "RECOVERY_V7_FAULT_CHECKPOINT")
    }
    os.environ.update(
        {
            "APP_ENV": "production",
            "RECOVERY_V7_ENABLED": "true",
            "RECOVERY_V7_FAULT_CHECKPOINT": "recognition-running",
        }
    )
    try:
        recovery_fault_checkpoint("recognition-running")
    except Exception:
        return True
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
    return False


def recovery_fixture_rejected() -> bool:
    previous = os.environ.get("APP_ENV")
    os.environ["APP_ENV"] = "production"
    try:
        require_recovery_environment()
    except Exception:
        return True
    finally:
        if previous is None:
            os.environ.pop("APP_ENV", None)
        else:
            os.environ["APP_ENV"] = previous
    return False


checks: dict[str, Callable[[], bool]] = {
    "missing_session_secret": lambda: rejected({"session_hmac_secret": ""}),
    "weak_session_secret": lambda: rejected({"session_hmac_secret": "change-me"}),
    "default_database_password": lambda: rejected(
        {"database_url": "postgresql+psycopg://app:change-me@postgres:5432/app"}
    ),
    "default_minio_credentials": lambda: rejected(
        {"minio_access_key": "ahamark-local", "minio_secret_key": "change-me-in-production"}
    ),
    "sqlite": lambda: rejected({"database_url": "sqlite:///production.db"}),
    "wildcard_cors": lambda: rejected({"cors_origins": ["*"]}),
    "wildcard_trusted_host": lambda: rejected({"trusted_hosts": ["*"]}),
    "missing_csrf_origin": lambda: rejected({"csrf_trusted_origins": []}),
    "insecure_cookie": lambda: rejected({"auth_cookie_secure": False}),
    "fake_grading": lambda: rejected({"grading_provider": "fake"}),
    "fake_ocr": lambda: rejected({"recognition_provider": "fake"}),
    "demo_actor": lambda: rejected({"demo_actor_enabled": True}),
    "failure_checkpoint": fault_checkpoint_rejected,
    "recovery_fixture": recovery_fixture_rejected,
}


if __name__ == "__main__":
    results = {name: check() for name, check in checks.items()}
    for name, result in results.items():
        print(f"{name}={'PASS' if result else 'FAIL'}")
    if not all(results.values()):
        raise SystemExit(1)
