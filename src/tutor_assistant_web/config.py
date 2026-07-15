from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "Tutor Assistant"
    app_env: str = "development"
    app_secret_key: str = "change-me-in-production"
    # Deprecated compatibility value. When set, it is used as the development
    # bootstrap password unless BOOTSTRAP_ADMIN_PASSWORD is configured.
    app_access_token: str = ""
    app_timezone: str = "Europe/Moscow"
    app_host: str = "127.0.0.1"
    app_port: int = Field(default=8000, ge=1, le=65535)
    app_reload: bool = False
    public_base_url: str = "http://localhost:8000"
    trusted_hosts: str = "localhost,127.0.0.1,testserver"
    trusted_proxy_ips: str = "127.0.0.1"

    database_url: str = "sqlite:///./data/tutor-assistant.db"
    auto_migrate: bool = True
    database_pool_size: int = Field(default=10, ge=1, le=100)
    database_max_overflow: int = Field(default=20, ge=0, le=200)
    database_pool_timeout: int = Field(default=30, ge=1, le=300)
    database_pool_recycle: int = Field(default=1800, ge=60, le=86400)
    database_statement_timeout_ms: int = Field(default=30_000, ge=1000, le=600_000)
    database_lock_timeout_ms: int = Field(default=5000, ge=100, le=120_000)
    redis_url: str = "redis://localhost:6379/0"
    task_eager: bool = True
    celery_visibility_timeout: int = Field(default=10_800, ge=300, le=86_400)
    worker_shutdown_timeout: float = Field(default=30.0, ge=1, le=300)

    bbb_base_url: str = ""
    bbb_secret: str = ""
    bbb_demo_mode: bool = True
    bbb_request_timeout: float = 15.0

    transcription_provider: str = "auto"
    transcription_webhook_url: str = ""
    transcription_webhook_token: str = ""
    transcription_model: str = "small"
    transcription_language: str = "ru"
    transcription_device: str = "cpu"
    transcription_compute_type: str = "int8"
    transcription_request_timeout: float = 300.0
    transcription_max_download_mb: int = Field(default=500, ge=1, le=4096)

    workflow_max_retries: int = Field(default=5, ge=0, le=20)
    workflow_max_attempts: int = Field(default=6, ge=1, le=20)
    workflow_retry_base_seconds: int = Field(default=30, ge=1, le=3600)
    workflow_retry_max_seconds: int = Field(default=3600, ge=1, le=86_400)
    workflow_soft_time_limit: int = Field(default=7200, ge=60, le=86_400)
    workflow_hard_time_limit: int = Field(default=7500, ge=60, le=86_400)
    job_lease_seconds: int = Field(default=300, ge=30, le=3600)
    job_recovery_poll_seconds: int = Field(default=30, ge=5, le=600)
    job_recovery_batch_size: int = Field(default=100, ge=1, le=1000)
    outbox_batch_size: int = Field(default=20, ge=1, le=500)
    outbox_poll_seconds: int = Field(default=10, ge=1, le=300)
    outbox_max_attempts: int = Field(default=12, ge=1, le=100)
    outbox_dispatch_lease_seconds: int = Field(default=300, ge=30, le=3600)

    circuit_breaker_failure_threshold: int = Field(default=5, ge=1, le=100)
    circuit_breaker_recovery_seconds: int = Field(default=60, ge=1, le=3600)

    materials_webhook_url: str = ""
    materials_webhook_token: str = ""
    materials_request_timeout: float = 60.0

    document_engine_provider: str = "local"
    document_engine_url: str = ""
    document_engine_token: str = ""
    document_engine_timeout: float = 120.0
    document_max_pdf_mb: int = Field(default=50, ge=1, le=500)
    artifact_storage_provider: str = "local"
    artifact_storage_root: str = "./data/artifacts"
    artifact_s3_endpoint_url: str = ""
    artifact_s3_region: str = "us-east-1"
    artifact_s3_bucket: str = "tutor-artifacts"
    artifact_s3_access_key: str = ""
    artifact_s3_secret_key: str = ""
    artifact_s3_server_side_encryption: str = "auto"
    artifact_max_size_mb: int = Field(default=500, ge=1, le=4096)
    artifact_allowed_mime_types: str = (
        "application/pdf,application/json,application/x-tex,text/html,text/plain,"
        "image/png,image/jpeg,audio/wav,audio/mpeg,audio/mp4,video/mp4"
    )
    artifact_clamav_enabled: bool = False
    artifact_clamav_host: str = "clamav"
    artifact_clamav_port: int = Field(default=3310, ge=1, le=65535)
    artifact_clamav_timeout: float = Field(default=60.0, ge=1, le=600)
    artifact_retention_days: int = Field(default=365, ge=1, le=3650)
    artifact_delete_grace_days: int = Field(default=30, ge=1, le=365)
    artifact_abort_multipart_days: int = Field(default=1, ge=1, le=30)
    artifact_integrity_batch_size: int = Field(default=100, ge=1, le=1000)
    artifact_maintenance_poll_seconds: int = Field(default=3600, ge=60, le=86400)

    seed_demo_data: bool = True
    session_cookie_secure: bool = False
    session_cookie_name: str = "tutor_session"
    session_same_site: str = "lax"
    session_max_age: int = Field(default=60 * 60 * 12, ge=300)
    session_idle_timeout: int = Field(default=60 * 60, ge=300)
    session_rotation_seconds: int = Field(default=15 * 60, ge=60)
    rate_limit_login: int = Field(default=10, ge=1, le=1000)
    rate_limit_invitations: int = Field(default=30, ge=1, le=1000)
    rate_limit_callbacks: int = Field(default=120, ge=1, le=10000)
    rate_limit_downloads: int = Field(default=120, ge=1, le=10000)
    rate_limit_window_seconds: int = Field(default=60, ge=10, le=3600)
    security_csp: str = (
        "default-src 'self'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'; "
        "object-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
        "font-src 'self'; connect-src 'self'"
    )

    log_level: str = "INFO"
    log_json: bool = True
    otel_service_name: str = "tutor-assistant-web"
    otel_exporter_otlp_endpoint: str = ""
    sentry_dsn: str = ""
    sentry_environment: str = ""
    metrics_enabled: bool = True
    metrics_bearer_token: str = ""
    readiness_timeout_seconds: float = Field(default=3.0, ge=0.2, le=30)

    app_secret_key_file: str = ""
    database_url_file: str = ""
    redis_url_file: str = ""
    bbb_secret_file: str = ""
    bootstrap_admin_password_file: str = ""
    artifact_s3_secret_key_file: str = ""
    transcription_webhook_token_file: str = ""
    materials_webhook_token_file: str = ""
    document_engine_token_file: str = ""
    metrics_bearer_token_file: str = ""
    sentry_dsn_file: str = ""
    enabled_modules: str = ""

    bootstrap_organization_name: str = "Tutor Workspace"
    bootstrap_organization_slug: str = "default"
    bootstrap_admin_email: str = "admin@localhost"
    bootstrap_admin_name: str = "Администратор"
    bootstrap_admin_password: str = ""
    invitation_ttl_hours: int = Field(default=72, ge=1, le=24 * 30)

    @property
    def effective_bootstrap_password(self) -> str:
        return self.bootstrap_admin_password or self.app_access_token or "admin"

    @model_validator(mode="after")
    def validate_production(self) -> Settings:
        for value_field, file_field in (
            ("app_secret_key", "app_secret_key_file"),
            ("database_url", "database_url_file"),
            ("redis_url", "redis_url_file"),
            ("bbb_secret", "bbb_secret_file"),
            ("bootstrap_admin_password", "bootstrap_admin_password_file"),
            ("artifact_s3_secret_key", "artifact_s3_secret_key_file"),
            ("transcription_webhook_token", "transcription_webhook_token_file"),
            ("materials_webhook_token", "materials_webhook_token_file"),
            ("document_engine_token", "document_engine_token_file"),
            ("metrics_bearer_token", "metrics_bearer_token_file"),
            ("sentry_dsn", "sentry_dsn_file"),
        ):
            path = str(getattr(self, file_field, "")).strip()
            if path:
                setattr(self, value_field, Path(path).read_text(encoding="utf-8").strip())
        enabled = {item.strip() for item in self.enabled_modules.split(",") if item.strip()}
        classroom_enabled = not enabled or bool(
            enabled & {"classroom", "materials", "automation", "portal", "dashboard"}
        )
        automation_enabled = not enabled or bool(enabled & {"automation", "portal"})
        if self.app_env.lower() == "production":
            database = make_url(self.database_url)
            if database.get_backend_name() != "postgresql":
                raise ValueError("DATABASE_URL must use PostgreSQL in production")
            if database.get_driver_name() != "psycopg":
                raise ValueError("DATABASE_URL must use the postgresql+psycopg driver")
            if self.auto_migrate:
                raise ValueError("AUTO_MIGRATE must be false in production; use a migration job")
            if self.app_secret_key == "change-me-in-production":
                raise ValueError("APP_SECRET_KEY must be changed in production")
            if len(self.app_secret_key) < 32:
                raise ValueError("APP_SECRET_KEY must contain at least 32 characters")
            if any(
                marker in self.app_secret_key.lower()
                for marker in ("change-me", "replace-with", "demo-secret", "test-secret")
            ):
                raise ValueError("APP_SECRET_KEY must not use a demo or placeholder value")
            if len(self.bootstrap_admin_password) < 12:
                raise ValueError("BOOTSTRAP_ADMIN_PASSWORD must contain at least 12 characters")
            normalized_password = self.bootstrap_admin_password.strip().lower()
            if normalized_password in {
                "administrator",
                "password1234",
                "test-password",
            } or any(
                marker in normalized_password
                for marker in ("change-this", "change-me", "demo-password")
            ):
                raise ValueError(
                    "BOOTSTRAP_ADMIN_PASSWORD must not use a demo or placeholder value"
                )
            if self.bbb_demo_mode and classroom_enabled:
                raise ValueError("BBB_DEMO_MODE must be false in production")
            if not self.public_base_url.startswith("https://"):
                raise ValueError("PUBLIC_BASE_URL must use https in production")
            if automation_enabled and self.transcription_provider.lower() in {
                "disabled",
                "demo",
            }:
                raise ValueError("A production transcription provider is required for automation")
            if (
                automation_enabled
                and self.transcription_provider.lower() == "auto"
                and not self.transcription_webhook_url
            ):
                raise ValueError("Configure TRANSCRIPTION_PROVIDER for production automation")
            materials_enabled = not enabled or bool(
                enabled & {"materials", "automation", "portal", "dashboard"}
            )
            if materials_enabled and self.document_engine_provider.lower() == "local":
                raise ValueError(
                    "DOCUMENT_ENGINE_PROVIDER must use a production compiler for materials"
                )
            if automation_enabled and self.task_eager:
                raise ValueError("TASK_EAGER must be false in production")
            if materials_enabled and self.artifact_storage_provider.lower() != "s3":
                raise ValueError("ARTIFACT_STORAGE_PROVIDER must be s3 in production")
            if materials_enabled and not self.artifact_clamav_enabled:
                raise ValueError("ARTIFACT_CLAMAV_ENABLED must be true in production")
            if not self.session_cookie_secure:
                raise ValueError("SESSION_COOKIE_SECURE must be true in production")
            if self.session_same_site not in {"lax", "strict"}:
                raise ValueError("SESSION_SAME_SITE must be lax or strict in production")
            hosts = {item.strip() for item in self.trusted_hosts.split(",") if item.strip()}
            if not hosts or "*" in hosts:
                raise ValueError("TRUSTED_HOSTS must explicitly list production hosts")
            proxies = {item.strip() for item in self.trusted_proxy_ips.split(",") if item.strip()}
            if not proxies or "*" in proxies:
                raise ValueError("TRUSTED_PROXY_IPS must explicitly list trusted proxy addresses")
            if self.seed_demo_data:
                raise ValueError("SEED_DEMO_DATA must be false in production")
            if self.bootstrap_admin_email.endswith("@localhost"):
                raise ValueError("BOOTSTRAP_ADMIN_EMAIL must not use a demo address")
            if not self.log_json:
                raise ValueError("LOG_JSON must be true in production")
            if self.app_reload:
                raise ValueError("APP_RELOAD must be false in production")
            if not self.metrics_enabled:
                raise ValueError("METRICS_ENABLED must be true in production")
            if len(self.metrics_bearer_token) < 24:
                raise ValueError("METRICS_BEARER_TOKEN must contain at least 24 characters")
            if any(
                marker in self.metrics_bearer_token.lower()
                for marker in ("change-me", "replace-with", "demo-token", "test-token")
            ):
                raise ValueError("METRICS_BEARER_TOKEN must not use a placeholder value")
        if make_url(self.redis_url).get_backend_name() not in {"redis", "rediss"}:
            raise ValueError("REDIS_URL must use redis:// or rediss://")
        if self.workflow_soft_time_limit >= self.workflow_hard_time_limit:
            raise ValueError("WORKFLOW_HARD_TIME_LIMIT must exceed WORKFLOW_SOFT_TIME_LIMIT")
        if self.celery_visibility_timeout <= self.workflow_hard_time_limit:
            raise ValueError("CELERY_VISIBILITY_TIMEOUT must exceed WORKFLOW_HARD_TIME_LIMIT")
        if not self.bbb_demo_mode and (not self.bbb_base_url or not self.bbb_secret):
            raise ValueError("BBB_BASE_URL and BBB_SECRET are required when demo mode is off")
        provider = self.transcription_provider.lower()
        if provider not in {"auto", "disabled", "demo", "webhook", "faster-whisper"}:
            raise ValueError("TRANSCRIPTION_PROVIDER is not supported")
        if provider == "webhook" and not self.transcription_webhook_url:
            raise ValueError("TRANSCRIPTION_WEBHOOK_URL is required for webhook transcription")
        document_provider = self.document_engine_provider.lower()
        if document_provider not in {"local", "latex-for-everyone"}:
            raise ValueError("DOCUMENT_ENGINE_PROVIDER is not supported")
        if document_provider == "latex-for-everyone" and (
            not self.document_engine_url or not self.document_engine_token
        ):
            raise ValueError(
                "DOCUMENT_ENGINE_URL and DOCUMENT_ENGINE_TOKEN are required for latex-for-everyone"
            )
        storage_provider = self.artifact_storage_provider.lower()
        if storage_provider not in {"local", "s3"}:
            raise ValueError("ARTIFACT_STORAGE_PROVIDER is not supported")
        if storage_provider == "s3" and not self.artifact_s3_bucket:
            raise ValueError("ARTIFACT_S3_BUCKET is required for S3 storage")
        if self.session_idle_timeout > self.session_max_age:
            raise ValueError("SESSION_IDLE_TIMEOUT must not exceed SESSION_MAX_AGE")
        if self.session_rotation_seconds > self.session_max_age:
            raise ValueError("SESSION_ROTATION_SECONDS must not exceed SESSION_MAX_AGE")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
