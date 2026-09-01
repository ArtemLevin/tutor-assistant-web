from __future__ import annotations

import os

from tutor_assistant_web.modules.practice.retention import RETENTION_INDEX_VERSION


def _enabled(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def practice_sync_enabled() -> bool:
    return _enabled("PRACTICE_SYNC_ENABLED", True)


def practice_analytics_enabled() -> bool:
    return _enabled("PRACTICE_ANALYTICS_ENABLED", True)


def practice_retention_index_version() -> int:
    raw = os.getenv("PRACTICE_RETENTION_INDEX_VERSION", str(RETENTION_INDEX_VERSION))
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError("PRACTICE_RETENTION_INDEX_VERSION must be an integer") from exc
    if value != RETENTION_INDEX_VERSION:
        raise RuntimeError(
            f"Unsupported PRACTICE_RETENTION_INDEX_VERSION={value}; "
            f"supported version is {RETENTION_INDEX_VERSION}"
        )
    return value
