from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal

from tutor_assistant_web.config import Settings, get_settings

AppProfile = Literal["full", "board"]


@dataclass(frozen=True)
class RuntimeConfiguration:
    profile: AppProfile
    settings: Settings


def resolve_app_profile(value: str | None = None) -> AppProfile:
    candidate = (value if value is not None else os.getenv("APP_PROFILE", "full")).strip().lower()
    if candidate == "":
        candidate = "full"
    if candidate not in {"full", "board"}:
        raise ValueError(f"Unsupported APP_PROFILE: {candidate}")
    return candidate  # type: ignore[return-value]


def load_runtime_configuration(value: str | None = None) -> RuntimeConfiguration:
    profile = resolve_app_profile(value)
    if profile == "full":
        return RuntimeConfiguration(profile="full", settings=get_settings())

    configured_modules = os.getenv("ENABLED_MODULES", "").strip()
    if configured_modules:
        raise ValueError("ENABLED_MODULES must be unset when APP_PROFILE=board")

    # The legacy Settings validator already understands which production
    # requirements are board-specific when only the boards module is enabled.
    # This value is an internal compatibility input only: board composition is
    # selected by APP_PROFILE and never by ModuleRegistry/ENABLED_MODULES.
    settings = Settings(enabled_modules="boards")
    return RuntimeConfiguration(profile="board", settings=settings)
