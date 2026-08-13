from functools import lru_cache

from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    app_env: str = "development"
    debug: bool = False
    log_level: str = "INFO"
    database_url: str = "sqlite:///./ahamark.db"
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "redis://localhost:6379/0"
    celery_result_backend: str = "redis://localhost:6379/1"
    celery_visibility_timeout: int = 3600
    cors_origins: list[str] = ["http://localhost:3000"]
    trusted_hosts: list[str] = ["localhost", "127.0.0.1", "testserver"]
    csrf_trusted_origins: list[str] = []
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
    session_hmac_secret: str = "development-only-session-secret"
    auth_cookie_name: str = "ahamark_session"
    auth_session_hours: int = 12
    auth_cookie_secure: bool = False
    auth_login_max_attempts: int = 5
    auth_login_window_seconds: int = 300
    auth_rate_limit_fail_closed: bool = True
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
    answer_recognition_provider: str = "unavailable"
    answer_recognition_base_url: str | None = None
    answer_recognition_api_key: str | None = None
    answer_recognition_model: str | None = None
    answer_recognition_timeout_seconds: float = 30.0
    answer_recognition_max_attempts: int = 3
    answer_recognition_margin_pixels: int = 12
    answer_recognition_config_version: str = "answer-evidence-v1"
    grading_provider: str = "unavailable"
    grading_base_url: str | None = None
    grading_api_key: str | None = None
    grading_model: str | None = None
    grading_timeout_seconds: float = 30.0
    grading_prompt_version: str = "subjective-v1"
    grading_config_version: str = "2026-07-22"
    grading_auto_accept_confidence: float = 0.95
    # Shared, server-only OpenAI connection. Feature-specific values below may
    # override the base URL or key temporarily for backwards compatibility.
    ai_external_requests_enabled: bool = False
    openai_base_url: str = "https://api.openai.com/v1"
    openai_api_key: SecretStr | None = None
    openai_organization: str | None = None
    openai_project: str | None = None
    ai_safety_hmac_secret: SecretStr | None = None
    openai_allow_private_base_url_for_tests: bool = False
    ai_grading_provider: str = "unavailable"
    assignment_generation_enabled: bool = True
    assignment_generation_provider: str = "unavailable"
    assignment_generation_allow_external_provider_requests: bool = False
    assignment_generation_allow_teacher_start: bool = True
    assignment_generation_suggestion_only: bool = True
    assignment_generation_real_provider_quality_passed: bool = False
    assignment_generation_provider_config_version: str = "assignment-generation-provider-v1"
    assignment_generation_prompt_version: str = "assignment-generation-prompt-v1"
    assignment_generation_schema_version: str = "assignment-generation-schema-v1"
    assignment_generation_max_attempts: int = 3
    assignment_generation_base_url: str | None = None
    assignment_generation_api_key: str | None = None
    assignment_generation_model: str | None = None
    assignment_generation_model_snapshot: str | None = None
    assignment_generation_timeout_seconds: float = 45.0
    assignment_generation_max_retries: int = 2
    assignment_generation_max_input_tokens: int = 16000
    assignment_generation_max_output_tokens: int = 4000
    assignment_generation_max_images: int = 8
    assignment_generation_max_image_bytes: int = 5 * 1024 * 1024
    assignment_generation_max_total_image_bytes: int = 20 * 1024 * 1024
    assignment_generation_max_estimated_cost: float = 1.0
    assignment_generation_input_cost_per_million: float = 0.0
    assignment_generation_output_cost_per_million: float = 0.0
    assignment_generation_allow_private_base_url_for_tests: bool = False
    ai_grading_base_url: str | None = None
    ai_grading_api_key: str | None = None
    ai_grading_model: str | None = None
    ai_grading_timeout_seconds: float = 45.0
    ai_grading_max_retries: int = 2
    ai_grading_max_input_tokens: int = 16000
    ai_grading_max_output_tokens: int = 4000
    ai_grading_max_images: int = 4
    ai_grading_max_image_bytes: int = 5 * 1024 * 1024
    ai_grading_max_total_pixels: int = 24_000_000
    ai_grading_max_request_bytes: int = 20 * 1024 * 1024
    ai_grading_max_cost_per_question: float = 0.25
    ai_grading_max_cost_per_batch: float = 25.0
    ai_grading_input_cost_per_million: float = 0.0
    ai_grading_output_cost_per_million: float = 0.0
    ai_grading_prompt_version: str = "ai-grading-v1"
    ai_grading_schema_version: str = "criterion-suggestion-v1"
    ai_grading_config_version: str = "stage4-v1"
    ai_grading_store_responses: bool = False
    ai_grading_review_provider: str | None = None
    ai_grading_review_model: str | None = None
    ai_tutor_provider: str = "unavailable"
    ai_tutor_model: str | None = None
    ai_tutor_timeout_seconds: float = 45.0
    ai_tutor_max_retries: int = 2
    ai_tutor_max_input_tokens: int = 12000
    ai_tutor_max_output_tokens: int = 2000
    ai_tutor_max_conversation_messages: int = 20
    ai_tutor_max_questions_per_hour: int = 30
    ai_tutor_prompt_version: str = "wrong-question-tutor-v1"
    ai_tutor_schema_version: str = "wrong-question-reply-v1"
    student_learning_provider: str = "unavailable"
    student_learning_model: str | None = None
    student_learning_timeout_seconds: float = 60.0
    student_learning_max_retries: int = 2
    student_learning_max_input_tokens: int = 24000
    student_learning_max_output_tokens: int = 3000
    student_learning_max_grade_releases: int = 50
    student_learning_max_requests_per_day: int = 10
    student_learning_retry_cooldown_seconds: int = 300
    student_learning_prompt_version: str = "student-learning-analysis-v1"
    student_learning_schema_version: str = "student-learning-analysis-v1"
    student_upload_max_unattached_files: int = 50
    student_upload_max_files_per_hour: int = 30
    submission_max_files: int = 100
    submission_batch_max_bytes: int = 250 * 1024 * 1024
    submission_match_threshold: float = 0.95

    @field_validator(
        "cors_origins",
        "trusted_hosts",
        "csrf_trusted_origins",
        "allowed_upload_types",
        mode="before",
    )
    @classmethod
    def split_csv(cls, v: object) -> object:
        return [x.strip() for x in v.split(",")] if isinstance(v, str) else v

    @model_validator(mode="after")
    def production_guard(self) -> "Settings":
        if self.app_env.lower() != "production":
            return self
        errors: list[str] = []
        weak = {
            "",
            "change-me",
            "change-me-in-production",
            "password",
            "secret",
            "ahamark-local",
            "development-only-session-secret",
        }
        if self.debug:
            errors.append("DEBUG must be false")
        if self.demo_actor_enabled:
            errors.append("DEMO_ACTOR_ENABLED must be false")
        if self.recognition_provider.lower() == "fake":
            errors.append("RECOGNITION_PROVIDER cannot be fake")
        if self.grading_provider.lower() == "fake":
            errors.append("GRADING_PROVIDER cannot be fake")
        if self.ai_grading_provider.lower() == "fake":
            errors.append("AI_GRADING_PROVIDER cannot be fake")
        if self.ai_tutor_provider.lower() == "fake":
            errors.append("AI_TUTOR_PROVIDER cannot be fake")
        if self.student_learning_provider.lower() == "fake":
            errors.append("STUDENT_LEARNING_PROVIDER cannot be fake")
        if self.assignment_generation_provider.lower() == "fake":
            errors.append("ASSIGNMENT_GENERATION_PROVIDER cannot be fake")
        if not self.assignment_generation_suggestion_only:
            errors.append("ASSIGNMENT_GENERATION_SUGGESTION_ONLY must be true")
        if self.session_hmac_secret.lower() in weak or len(self.session_hmac_secret) < 32:
            errors.append("SESSION_HMAC_SECRET must be a strong value of at least 32 characters")
        if self.minio_access_key.lower() in weak:
            errors.append("MINIO_ACCESS_KEY cannot use a default or placeholder")
        if self.minio_secret_key.lower() in weak or len(self.minio_secret_key) < 16:
            errors.append("MINIO_SECRET_KEY cannot use a default or placeholder")
        database_lower = self.database_url.lower()
        if database_lower.startswith("sqlite:"):
            errors.append("DATABASE_URL must use PostgreSQL")
        if any(marker in database_lower for marker in (":password@", ":change-me", "localhost")):
            errors.append("DATABASE_URL cannot use a default, placeholder, or localhost")
        if not self.csrf_trusted_origins:
            errors.append("CSRF_TRUSTED_ORIGINS is required")
        if not self.cors_origins or "*" in self.cors_origins:
            errors.append("CORS_ORIGINS must be an explicit allowlist")
        if not self.trusted_hosts or "*" in self.trusted_hosts:
            errors.append("TRUSTED_HOSTS must be an explicit allowlist")
        if not self.auth_cookie_secure:
            errors.append("AUTH_COOKIE_SECURE must be true")
        if not self.auth_rate_limit_fail_closed:
            errors.append("AUTH_RATE_LIMIT_FAIL_CLOSED must be true")
        if not self.minio_public_endpoint:
            errors.append("MINIO_PUBLIC_ENDPOINT is required for browser downloads")
        if not self.minio_public_secure:
            errors.append("MINIO_PUBLIC_SECURE must be true")
        real_ai_providers = {
            self.ai_grading_provider.lower(),
            self.ai_tutor_provider.lower(),
            self.student_learning_provider.lower(),
        } & {"openai", "openai_compatible"}
        if self.ai_external_requests_enabled and real_ai_providers:
            shared_key = (
                self.openai_api_key.get_secret_value().strip()
                if self.openai_api_key is not None
                else ""
            )
            tutor_or_learning_enabled = any(
                value.lower() in {"openai", "openai_compatible"}
                for value in (self.ai_tutor_provider, self.student_learning_provider)
            )
            has_usable_key = bool(shared_key) or (
                not tutor_or_learning_enabled and bool((self.ai_grading_api_key or "").strip())
            )
            if not has_usable_key:
                errors.append("OPENAI_API_KEY is required when an OpenAI provider is enabled")
            if not self.openai_base_url.startswith("https://"):
                errors.append("OPENAI_BASE_URL must use HTTPS")
            safety_secret = (
                self.ai_safety_hmac_secret.get_secret_value().strip()
                if self.ai_safety_hmac_secret is not None
                else ""
            )
            if len(safety_secret) < 32 or safety_secret == self.session_hmac_secret:
                errors.append(
                    "AI_SAFETY_HMAC_SECRET must be at least 32 characters and distinct "
                    "from SESSION_HMAC_SECRET"
                )
            if self.ai_grading_provider.lower() in {"openai", "openai_compatible"}:
                if (
                    self.ai_grading_input_cost_per_million <= 0
                    or self.ai_grading_output_cost_per_million <= 0
                ):
                    errors.append(
                        "AI_GRADING_INPUT_COST_PER_MILLION and "
                        "AI_GRADING_OUTPUT_COST_PER_MILLION must be positive"
                    )
                if (
                    self.ai_grading_max_cost_per_question <= 0
                    or self.ai_grading_max_cost_per_batch <= 0
                ):
                    errors.append("AI grading cost budgets must be positive")
        if errors:
            raise ValueError("production configuration rejected: " + "; ".join(errors))
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
