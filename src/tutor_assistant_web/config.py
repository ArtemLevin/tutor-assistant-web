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

    materials_webhook_url: str = ""
    materials_webhook_token: str = ""
    materials_request_timeout: float = 60.0

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
        classroom_enabled = not enabled or bool(enabled & {"classroom", "materials", "dashboard"})
        if self.app_env.lower() == "production":
            if self.app_secret_key == "change-me-in-production":
                raise ValueError("APP_SECRET_KEY must be changed in production")
            if len(self.bootstrap_admin_password) < 12:
                raise ValueError("BOOTSTRAP_ADMIN_PASSWORD must contain at least 12 characters")
            if self.bbb_demo_mode and classroom_enabled:
                raise ValueError("BBB_DEMO_MODE must be false in production")
        if not self.bbb_demo_mode and (not self.bbb_base_url or not self.bbb_secret):
            raise ValueError("BBB_BASE_URL and BBB_SECRET are required when demo mode is off")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
