import json
import os
import re
from functools import lru_cache
from typing import Annotated

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


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
    cors_origins: Annotated[list[str], NoDecode] = ["http://localhost:3000"]
    trusted_hosts: Annotated[list[str], NoDecode] = ["localhost", "127.0.0.1", "testserver"]
    csrf_trusted_origins: Annotated[list[str], NoDecode] = []
    minio_endpoint: str = "localhost:9000"
    minio_public_endpoint: str | None = None
    minio_access_key: str = "ahamark-local"
    minio_secret_key: str = "change-me-in-production"
    minio_bucket: str = "ahamark-files"
    minio_region: str = "us-east-1"
    minio_secure: bool = False
    minio_public_secure: bool = False
    max_upload_bytes: int = 10 * 1024 * 1024
    allowed_upload_types: Annotated[list[str], NoDecode] = [
        "application/pdf",
        "image/jpeg",
        "image/png",
    ]
    signed_url_expiry_seconds: int = 900
    demo_actor_enabled: bool = True
    demo_actor_email: str = "demo-teacher@ahamark.local"
    synthetic_demo_reset_enabled: bool = False
    synthetic_demo_reset_bucket: str = "ahamark-business-e2e-files"
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
    recognition_rapidocr_runtime_enabled: bool = False
    recognition_rapidocr_model_download_allowed: bool = False
    recognition_rapidocr_artifact_root: str | None = None
    recognition_rapidocr_manifest_sha256: str | None = None
    recognition_tesseract_runtime_enabled: bool = False
    recognition_tesseract_binary_path: str | None = None
    recognition_tesseract_data_root: str | None = None
    recognition_tesseract_license_path: str | None = None
    recognition_tesseract_expected_version: str | None = None
    recognition_tesseract_binary_sha256: str | None = None
    recognition_tesseract_chi_sim_sha256: str | None = None
    recognition_tesseract_eng_sha256: str | None = None
    recognition_tesseract_license_sha256: str | None = None
    recognition_tesseract_timeout_seconds: float = Field(default=20.0, gt=0, le=120)
    formula_recognition_provider: str = "unavailable"
    formula_recognition_base_url: str | None = None
    formula_recognition_api_key: SecretStr | None = None
    formula_recognition_allowed_hosts: Annotated[list[str], NoDecode] = []
    formula_recognition_timeout_seconds: float = Field(default=20.0, gt=0, le=120)
    formula_recognition_max_image_bytes: int = Field(
        default=5 * 1024 * 1024, ge=1024, le=20 * 1024 * 1024
    )
    formula_recognition_max_pixels: int = Field(default=8_000_000, ge=1, le=40_000_000)
    formula_recognition_max_candidates: int = Field(default=5, ge=1, le=10)
    formula_recognition_config_version: str = "formula-recognition-v1"
    formula_recognition_allow_local_http: bool = False
    formula_region_detection_enabled: bool = False
    formula_region_detection_model_download_allowed: bool = False
    answer_recognition_provider: str = "unavailable"
    answer_recognition_base_url: str | None = None
    answer_recognition_api_key: str | None = None
    answer_recognition_model: str | None = None
    answer_recognition_timeout_seconds: float = 30.0
    answer_recognition_max_attempts: int = 3
    answer_recognition_margin_pixels: int = 12
    answer_recognition_config_version: str = "answer-evidence-v1"
    grading_provider: str = "unavailable"
    grading_allow_external_provider_requests: bool = False
    grading_allow_local_provider_requests: bool = False
    grading_allowed_local_hosts: Annotated[list[str], NoDecode] = []
    grading_base_url: str | None = None
    grading_api_key: str | None = None
    grading_model: str | None = None
    grading_timeout_seconds: float = 30.0
    grading_max_output_tokens: int = Field(default=4000, ge=128, le=8000)
    grading_prompt_version: str = "subjective-v1"
    grading_config_version: str = "2026-07-22"
    grading_auto_accept_confidence: float = 0.95
    ai_grading_provider: str = "unavailable"
    ai_grading_allow_external_provider_requests: bool = False
    ai_grading_allow_local_provider_requests: bool = False
    ai_grading_allowed_local_hosts: Annotated[list[str], NoDecode] = []
    assignment_generation_enabled: bool = True
    assignment_generation_provider: str = "unavailable"
    assignment_generation_allow_external_provider_requests: bool = False
    assignment_generation_allow_local_provider_requests: bool = False
    assignment_generation_allowed_local_hosts: Annotated[list[str], NoDecode] = []
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
    assignment_generation_local_timeout_seconds: float = 900.0
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
    codex_local_enabled: bool = False
    codex_local_internal_token: SecretStr | None = None
    codex_local_lease_seconds: int = Field(default=300, ge=30, le=3600)
    codex_local_max_claim: int = Field(default=20, ge=1, le=100)
    student_learning_assistant_enabled: bool = False
    submission_max_files: int = 100
    submission_batch_max_bytes: int = 250 * 1024 * 1024
    submission_match_threshold: float = 0.95

    @field_validator(
        "cors_origins",
        "trusted_hosts",
        "csrf_trusted_origins",
        "allowed_upload_types",
        "formula_recognition_allowed_hosts",
        "grading_allowed_local_hosts",
        "ai_grading_allowed_local_hosts",
        "assignment_generation_allowed_local_hosts",
        mode="before",
    )
    @classmethod
    def split_csv(cls, v: object) -> object:
        if not isinstance(v, str):
            return v
        text = v.strip()
        if not text:
            return []
        try:
            decoded = json.loads(text)
        except json.JSONDecodeError:
            if text.startswith("[") and text.endswith("]"):
                text = text[1:-1]
            return [item.strip().strip("'\"") for item in text.split(",") if item.strip()]
        return decoded

    @model_validator(mode="after")
    def production_guard(self) -> "Settings":
        weak_codex_tokens = {
            "",
            "change-me",
            "change-me-in-production",
            "password",
            "secret",
            "codex-local",
            "development-only-codex-token",
        }
        if self.codex_local_enabled:
            token = (
                self.codex_local_internal_token.get_secret_value()
                if self.codex_local_internal_token is not None
                else ""
            )
            if token.lower() in weak_codex_tokens or len(token) < 32:
                raise ValueError(
                    "CODEX_LOCAL_INTERNAL_TOKEN must be a strong value of at least "
                    "32 characters when CODEX_LOCAL_ENABLED is true"
                )
        if self.student_learning_assistant_enabled:
            if self.ai_grading_provider != "local_openai_compatible":
                raise ValueError(
                    "STUDENT_LEARNING_ASSISTANT_ENABLED requires a local OpenAI-compatible provider"
                )
            if not self.ai_grading_allow_local_provider_requests:
                raise ValueError(
                    "STUDENT_LEARNING_ASSISTANT_ENABLED requires local provider requests enabled"
                )
            if self.ai_grading_allow_external_provider_requests:
                raise ValueError("student learning assistant cannot use external provider requests")
        formula_region_errors = []
        if self.formula_region_detection_enabled:
            formula_region_errors.append(
                "FORMULA_REGION_DETECTION_ENABLED must remain false until a separately authorized "
                "product integration is reviewed"
            )
        if self.formula_region_detection_model_download_allowed:
            formula_region_errors.append(
                "FORMULA_REGION_DETECTION_MODEL_DOWNLOAD_ALLOWED must remain false; "
                "runtime model downloads are prohibited"
            )
        if formula_region_errors:
            raise ValueError(
                "formula region detection configuration rejected: "
                + "; ".join(formula_region_errors)
            )
        rapidocr_errors: list[str] = []
        if self.recognition_rapidocr_runtime_enabled:
            if self.recognition_provider != "rapidocr":
                rapidocr_errors.append("RECOGNITION_PROVIDER must be rapidocr")
            if not self.recognition_rapidocr_artifact_root or not os.path.isabs(
                self.recognition_rapidocr_artifact_root
            ):
                rapidocr_errors.append(
                    "RECOGNITION_RAPIDOCR_ARTIFACT_ROOT must be an absolute path"
                )
            if not self.recognition_rapidocr_manifest_sha256 or not re.fullmatch(
                r"[0-9a-f]{64}", self.recognition_rapidocr_manifest_sha256
            ):
                rapidocr_errors.append(
                    "RECOGNITION_RAPIDOCR_MANIFEST_SHA256 must be lowercase SHA-256"
                )
        if self.recognition_rapidocr_model_download_allowed:
            rapidocr_errors.append(
                "RECOGNITION_RAPIDOCR_MODEL_DOWNLOAD_ALLOWED must remain false; "
                "runtime model downloads are prohibited"
            )
        if rapidocr_errors:
            raise ValueError("RapidOCR configuration rejected: " + "; ".join(rapidocr_errors))
        tesseract_fields = {
            "RECOGNITION_TESSERACT_BINARY_PATH": self.recognition_tesseract_binary_path,
            "RECOGNITION_TESSERACT_DATA_ROOT": self.recognition_tesseract_data_root,
            "RECOGNITION_TESSERACT_LICENSE_PATH": self.recognition_tesseract_license_path,
            "RECOGNITION_TESSERACT_EXPECTED_VERSION": self.recognition_tesseract_expected_version,
            "RECOGNITION_TESSERACT_BINARY_SHA256": self.recognition_tesseract_binary_sha256,
            "RECOGNITION_TESSERACT_CHI_SIM_SHA256": self.recognition_tesseract_chi_sim_sha256,
            "RECOGNITION_TESSERACT_ENG_SHA256": self.recognition_tesseract_eng_sha256,
            "RECOGNITION_TESSERACT_LICENSE_SHA256": self.recognition_tesseract_license_sha256,
        }
        if self.recognition_tesseract_runtime_enabled:
            missing = [name for name, value in tesseract_fields.items() if not value]
            if self.recognition_provider != "tesseract":
                missing.append("RECOGNITION_PROVIDER=tesseract")
            if missing:
                raise ValueError(
                    "Tesseract configuration rejected: missing " + ", ".join(sorted(missing))
                )
        elif self.recognition_provider == "tesseract":
            # Keep the provider selectable for a stable fail-closed readiness response.
            pass
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
        if self.synthetic_demo_reset_enabled:
            errors.append("SYNTHETIC_DEMO_RESET_ENABLED must be false")
        if self.codex_local_enabled:
            errors.append("CODEX_LOCAL_ENABLED must be false")
        if self.recognition_provider.lower() == "fake":
            errors.append("RECOGNITION_PROVIDER cannot be fake")
        if self.formula_recognition_provider.lower() == "fake":
            errors.append("FORMULA_RECOGNITION_PROVIDER cannot be fake")
        if self.formula_recognition_provider.lower() == "http":
            formula_token = (
                self.formula_recognition_api_key.get_secret_value()
                if self.formula_recognition_api_key is not None
                else ""
            )
            if len(formula_token) < 32 or formula_token.lower() in weak:
                errors.append(
                    "FORMULA_RECOGNITION_API_KEY must be a strong value of at least 32 characters"
                )
            if not self.formula_recognition_allowed_hosts:
                errors.append("FORMULA_RECOGNITION_ALLOWED_HOSTS is required")
            if self.formula_recognition_allow_local_http and not any(
                self.formula_recognition_allowed_hosts
            ):
                errors.append("local formula provider requires an allowed host")
        if self.answer_recognition_provider.lower() == "fake":
            errors.append("ANSWER_RECOGNITION_PROVIDER cannot be fake")
        if self.grading_provider.lower() == "fake":
            errors.append("GRADING_PROVIDER cannot be fake")
        if self.grading_provider.lower() == "local_openai_compatible":
            if not self.grading_allow_local_provider_requests:
                errors.append("local GRADING_PROVIDER requests must be explicitly enabled")
            if not self.grading_allowed_local_hosts:
                errors.append("local GRADING_PROVIDER requires an allowed host")
            if not self.grading_base_url or not self.grading_model:
                errors.append("local GRADING_PROVIDER configuration is incomplete")
            if not self.grading_api_key or len(self.grading_api_key) < 32:
                errors.append("local GRADING_PROVIDER API key must be at least 32 characters")
        if self.ai_grading_provider.lower() == "fake":
            errors.append("AI_GRADING_PROVIDER cannot be fake")
        if self.ai_grading_provider.lower() == "local_openai_compatible":
            if not self.ai_grading_allow_local_provider_requests:
                errors.append("local AI_GRADING_PROVIDER requests must be explicitly enabled")
            if not self.ai_grading_allowed_local_hosts:
                errors.append("local AI_GRADING_PROVIDER requires an allowed host")
            if not self.ai_grading_base_url or not self.ai_grading_model:
                errors.append("local AI_GRADING_PROVIDER configuration is incomplete")
            if not self.ai_grading_api_key or len(self.ai_grading_api_key) < 32:
                errors.append("local AI_GRADING_PROVIDER API key must be at least 32 characters")
        if self.assignment_generation_provider.lower() == "fake":
            errors.append("ASSIGNMENT_GENERATION_PROVIDER cannot be fake")
        if self.assignment_generation_provider.lower() == "local_openai_compatible":
            if not self.assignment_generation_allow_local_provider_requests:
                errors.append("local ASSIGNMENT_GENERATION_PROVIDER requests must be enabled")
            if not self.assignment_generation_allowed_local_hosts:
                errors.append("local ASSIGNMENT_GENERATION_PROVIDER requires an allowed host")
            if not self.assignment_generation_base_url or not self.assignment_generation_model:
                errors.append("local ASSIGNMENT_GENERATION_PROVIDER configuration is incomplete")
            if (
                not self.assignment_generation_api_key
                or len(self.assignment_generation_api_key) < 32
            ):
                errors.append(
                    "local ASSIGNMENT_GENERATION_PROVIDER API key must be at least 32 characters"
                )
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
        if errors:
            raise ValueError("production configuration rejected: " + "; ".join(errors))
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
