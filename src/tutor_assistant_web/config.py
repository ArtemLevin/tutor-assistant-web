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
    app_access_token: str = ""
    app_timezone: str = "Europe/Moscow"
    public_base_url: str = "http://localhost:8000"

    database_url: str = "sqlite:///./data/tutor-assistant.db"
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

    @model_validator(mode="after")
    def validate_production(self) -> Settings:
        if self.app_env.lower() == "production":
            if self.app_secret_key == "change-me-in-production":
                raise ValueError("APP_SECRET_KEY must be changed in production")
            if not self.app_access_token:
                raise ValueError("APP_ACCESS_TOKEN is required in production")
            if self.bbb_demo_mode:
                raise ValueError("BBB_DEMO_MODE must be false in production")
        if not self.bbb_demo_mode and (not self.bbb_base_url or not self.bbb_secret):
            raise ValueError("BBB_BASE_URL and BBB_SECRET are required when demo mode is off")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
