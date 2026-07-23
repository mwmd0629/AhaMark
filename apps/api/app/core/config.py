from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    app_env: str = "development"
    log_level: str = "INFO"
    database_url: str = "sqlite:///./ahamark.db"
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "redis://localhost:6379/0"
    celery_result_backend: str = "redis://localhost:6379/1"
    cors_origins: list[str] = ["http://localhost:3000"]
    minio_endpoint: str = "localhost:9000"
    minio_public_endpoint: str | None = None
    minio_access_key: str = "ahamark-local"
    minio_secret_key: str = "change-me-in-production"
    minio_bucket: str = "ahamark-files"
    minio_region: str = "us-east-1"
    minio_secure: bool = False
    minio_public_secure: bool = False
    max_upload_bytes: int = 10 * 1024 * 1024
    allowed_upload_types: list[str] = ["application/pdf", "image/jpeg", "image/png"]
    signed_url_expiry_seconds: int = 900
    demo_actor_enabled: bool = True
    demo_actor_email: str = "demo-teacher@ahamark.local"
    auth_cookie_name: str = "ahamark_session"
    auth_session_hours: int = 12
    auth_cookie_secure: bool = False
    auth_login_max_attempts: int = 5
    auth_login_window_seconds: int = 300
    import_max_bytes: int = 5 * 1024 * 1024
    import_max_rows: int = 2000
    import_expiry_hours: int = 24
    assignment_max_file_bytes: int = 25 * 1024 * 1024
    assignment_max_files: int = 20
    recognition_provider: str = "unavailable"
    recognition_pdf_dpi: int = 180
    recognition_max_pdf_pages: int = 100
    recognition_max_image_pixels: int = 40_000_000
    recognition_low_confidence: float = 0.70
    recognition_high_confidence: float = 0.90
    recognition_config_version: str = "2026-07-22"
    grading_provider: str = "unavailable"
    grading_prompt_version: str = "subjective-v1"
    grading_config_version: str = "2026-07-22"
    grading_auto_accept_confidence: float = 0.95
    submission_max_files: int = 100
    submission_batch_max_bytes: int = 250 * 1024 * 1024
    submission_match_threshold: float = 0.95

    @field_validator("cors_origins", "allowed_upload_types", mode="before")
    @classmethod
    def split_csv(cls, v: object) -> object:
        return [x.strip() for x in v.split(",")] if isinstance(v, str) else v


@lru_cache
def get_settings() -> Settings:
    return Settings()
