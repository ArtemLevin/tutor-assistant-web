from __future__ import annotations

from functools import lru_cache

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


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
    public_base_url: str = "http://localhost:8000"

    database_url: str = "sqlite:///./data/tutor-assistant.db"
    auto_migrate: bool = True
    redis_url: str = "redis://localhost:6379/0"
    task_eager: bool = True

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
    workflow_retry_base_seconds: int = Field(default=30, ge=1, le=3600)
    outbox_batch_size: int = Field(default=20, ge=1, le=500)
    outbox_poll_seconds: int = Field(default=10, ge=1, le=300)
    outbox_max_attempts: int = Field(default=12, ge=1, le=100)

    materials_webhook_url: str = ""
    materials_webhook_token: str = ""
    materials_request_timeout: float = 60.0

    document_engine_provider: str = "local"
    document_engine_url: str = ""
    document_engine_token: str = ""
    document_engine_timeout: float = 120.0
    document_max_pdf_mb: int = Field(default=50, ge=1, le=500)
    artifact_storage_root: str = "./data/artifacts"

    seed_demo_data: bool = True
    session_cookie_secure: bool = False
    session_max_age: int = Field(default=60 * 60 * 12, ge=300)
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
        enabled = {item.strip() for item in self.enabled_modules.split(",") if item.strip()}
        classroom_enabled = not enabled or bool(
            enabled & {"classroom", "materials", "automation", "dashboard"}
        )
        automation_enabled = not enabled or "automation" in enabled
        if self.app_env.lower() == "production":
            if self.app_secret_key == "change-me-in-production":
                raise ValueError("APP_SECRET_KEY must be changed in production")
            if len(self.bootstrap_admin_password) < 12:
                raise ValueError("BOOTSTRAP_ADMIN_PASSWORD must contain at least 12 characters")
            if self.bbb_demo_mode and classroom_enabled:
                raise ValueError("BBB_DEMO_MODE must be false in production")
            if classroom_enabled and not self.public_base_url.startswith("https://"):
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
                enabled & {"materials", "automation", "dashboard"}
            )
            if materials_enabled and self.document_engine_provider.lower() == "local":
                raise ValueError(
                    "DOCUMENT_ENGINE_PROVIDER must use a production compiler for materials"
                )
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
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
