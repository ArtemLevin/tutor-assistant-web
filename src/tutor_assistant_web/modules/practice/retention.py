from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from datetime import date
from typing import Any, Iterable

RETENTION_INDEX_VERSION = 1
MIN_RETENTION_EVENTS = 3
MAX_RETENTION_EVENTS = 8


@dataclass(frozen=True)
class RetentionComponents:
    retrieval_success: float
    independence: float
    spacing_strength: float
    lapse_adjustment: float


@dataclass(frozen=True)
class RetentionResult:
    version: int
    category: str
    index: int | None
    event_count: int
    components: RetentionComponents | None
    explanation: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["explanation"] = list(self.explanation)
        return payload


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def _number(value: object, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _calendar_date(value: object) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _recent(events: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return list(events)[-MAX_RETENTION_EVENTS:]


def _weighted_average(values: list[float]) -> float:
    if not values:
        return 0.0
    weights = list(range(1, len(values) + 1))
    return sum(value * weight for value, weight in zip(values, weights, strict=True)) / sum(weights)


def calculate_retention_index(
    events: Iterable[dict[str, Any]],
    schedule: dict[str, Any] | None,
    *,
    today: date,
    version: int = RETENTION_INDEX_VERSION,
) -> RetentionResult:
    if version != RETENTION_INDEX_VERSION:
        raise ValueError(f"Unsupported retention index version: {version}")

    recent = _recent(events)
    if len(recent) < MIN_RETENTION_EVENTS:
        return RetentionResult(
            version=version,
            category="insufficient-data",
            index=None,
            event_count=len(recent),
            components=None,
            explanation=(
                f"Нужно минимум {MIN_RETENTION_EVENTS} retrieval-события для числового индекса.",
            ),
        )

    retrieval_values = [1.0 if event.get("outcome") == "correct" else 0.0 for event in recent]
    retrieval_success = _weighted_average(retrieval_values)

    independence_values: list[float] = []
    for event in recent:
        hints = max(0.0, _number(event.get("hintsUsed")))
        retries = max(0.0, _number(event.get("attemptCount"), 1.0) - 1.0)
        rating_penalty = 0.15 if event.get("rating") == "again" else 0.0
        independence_values.append(_clamp(1.0 - 0.18 * hints - 0.12 * retries - rating_penalty))
    independence = _weighted_average(independence_values)

    schedule = schedule or {}
    interval_days = max(0.0, _number(schedule.get("intervalDays")))
    spacing_strength = _clamp(math.log2(interval_days + 1.0) / math.log2(121.0))

    lapses = max(0.0, _number(schedule.get("lapses")))
    repeated_lapse = bool(schedule.get("repeatedLapse"))
    due_at = _calendar_date(schedule.get("dueAt"))
    overdue_days = max(0, (today - due_at).days) if due_at and due_at < today else 0
    lapse_adjustment = _clamp(
        1.0
        - min(0.45, 0.09 * lapses)
        - (0.22 if repeated_lapse else 0.0)
        - min(0.28, 0.04 * overdue_days)
    )

    raw = (
        0.45 * retrieval_success
        + 0.25 * independence
        + 0.20 * spacing_strength
        + 0.10 * lapse_adjustment
    )
    index = int(round(_clamp(raw) * 100))

    if repeated_lapse or index < 40:
        category = "rebuild"
    elif index < 60:
        category = "fragile"
    elif index < 75:
        category = "watch"
    else:
        category = "stable"

    explanation = (
        f"Успешность retrieval: {round(retrieval_success * 100)}%.",
        f"Самостоятельность без подсказок/повторов: {round(independence * 100)}%.",
        f"Сила текущего интервала: {round(spacing_strength * 100)}%.",
        f"Коррекция за lapses/просрочку: {round(lapse_adjustment * 100)}%.",
    )
    return RetentionResult(
        version=version,
        category=category,
        index=index,
        event_count=len(recent),
        components=RetentionComponents(
            retrieval_success=round(retrieval_success, 4),
            independence=round(independence, 4),
            spacing_strength=round(spacing_strength, 4),
            lapse_adjustment=round(lapse_adjustment, 4),
        ),
        explanation=explanation,
    )
